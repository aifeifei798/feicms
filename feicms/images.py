"""Poster/thumbnail (vod_pic) collection.

Why a sharded layout instead of one flat folder:

    data/images/
      3f/
        2a/
        3f2a9c...c1.jpg
      c4/
        11/
        c411bd...e2.webp

The filename is the sha256 of the *image bytes*, split into two 2-character
buckets (256 x 256 = 65536 directories). That keeps every directory tiny even
with hundreds of thousands of posters, avoids filename collisions from titles
like "第1集.jpg", and makes a file addressable from its URL alone without a
lookup round trip. The database (`images` table) is the index; the filesystem is
never walked.

Deduplication happens twice:
  1. by normalised URL (unique constraint) - the same poster is never queued twice;
  2. by content sha256 - different URLs that return identical bytes are stored
     once and the extra rows are marked `duplicate`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import IS_SQLITE, session
from .fetcher import Fetcher, FetchResult
from .models import Image, SourceItem, Video

MAGIC_EXT = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF8": "gif",
    b"BM": "bmp",
}

# Image hosts that hotlink-block plain requests; a Referer usually fixes them.
# Add entries as you discover them, e.g. "img.example.net": "https://www.example.net/".
REFERER_BY_HOST: dict[str, str] = {}


def normalize_image_url(url: str) -> str | None:
    """Canonical form used for dedupe: https scheme, no fragment, no trailing slash."""
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("data:"):
        return None
    parts = urlsplit(url)
    if not parts.netloc:
        return None
    scheme = "https" if parts.scheme in ("", "http", "https") else parts.scheme
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/image"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def detect_extension(data: bytes, content_type: str = "") -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for signature, ext in MAGIC_EXT.items():
        if data.startswith(signature):
            return ext
    if data[:3] == b"<ht" or data[:5] == b"<!DOC":
        return None
    guessed = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(
        content_type.split(";")[0].strip().lower()
    )
    return guessed


def image_path(sha: str, ext: str) -> Path:
    """Two-level shard: <dir>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>."""
    return Path(settings.image_dir) / sha[:2] / sha[2:4] / f"{sha}.{ext}"


def relative_path(path: Path) -> str:
    return str(path.relative_to(Path(settings.image_dir)))


@dataclass
class ImageBatchResult:
    attempted: int = 0
    downloaded: int = 0
    duplicates: int = 0
    failed: int = 0
    skipped: int = 0
    bytes: int = 0
    elapsed_ms: float = 0.0


async def scan_images() -> dict[str, int]:
    """Queue every poster referenced by collected records. Cheap, no network."""
    added = 0
    seen = 0
    async with session() as s:
        source_rows = list(
            await s.execute(
                select(SourceItem.pic, SourceItem.source_id, SourceItem.video_id).where(SourceItem.pic != "")
            )
        )
        video_rows = list(await s.execute(select(Video.pic, Video.id).where(Video.pic != "")))

        pending: list[dict[str, object]] = []
        for pic, source_id, video_id in source_rows:
            url = normalize_image_url(pic)
            if not url:
                continue
            seen += 1
            pending.append({"url": url, "host": urlsplit(url).netloc, "source_id": source_id, "video_id": video_id})

        for pic, video_id in video_rows:
            url = normalize_image_url(pic)
            if not url:
                continue
            seen += 1
            pending.append({"url": url, "host": urlsplit(url).netloc, "source_id": None, "video_id": video_id})

        # Chunked "insert ignore" so a 100k-row poster scan does not need the whole
        # images table in memory and does not blow up on duplicate URLs.
        for i in range(0, len(pending), 1000):
            added += await _insert_ignore(s, pending[i : i + 1000])
    return {"queued": added, "seen": seen}


async def _insert_ignore(s, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    if IS_SQLITE:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        result = await s.execute(sqlite_insert(Image).values(rows).on_conflict_do_nothing(index_elements=["url"]))
        return result.rowcount or 0

    added = 0
    for row in rows:
        try:
            s.add(Image(**row))
            await s.flush()
            added += 1
        except IntegrityError:
            await s.rollback()
    return added


# Guards the dedupe check + file write in download_images().
_batch_running = False

# Live counters for the batch that is running right now (or the last one that
# finished). The admin UI polls download_progress() while a batch is running.
_progress: dict[str, object] = {
    "running": False,
    "queued": 0,
    "downloaded": 0,
    "duplicates": 0,
    "failed": 0,
    "skipped": 0,
    "bytes": 0,
    "started_at": None,
    "finished_at": None,
    "elapsed_ms": 0.0,
    "errors": [],
}


def download_progress() -> dict[str, object]:
    """Snapshot of the current/last poster download batch."""
    return dict(_progress)


async def download_images(
    limit: int | None = None,
    concurrency: int | None = None,
    client: Fetcher | None = None,
    include_failed: bool = False,
) -> ImageBatchResult:
    """Download a batch of pending posters. Safe to run repeatedly.

    Two batches in the same process never overlap (the auto loop and a manual
    "download now" click would otherwise race over the same pending rows).
    """
    global _batch_running
    if _batch_running:
        return ImageBatchResult()
    _batch_running = True
    try:
        return await _download_batch(limit, concurrency, client, include_failed)
    finally:
        _batch_running = False


async def _download_batch(
    limit: int | None,
    concurrency: int | None,
    client: Fetcher | None,
    include_failed: bool,
) -> ImageBatchResult:
    batch = limit or settings.image_batch
    sem = asyncio.Semaphore(concurrency or settings.image_concurrency)
    own_client = client is None
    client = client or Fetcher()
    result = ImageBatchResult()
    started = time.perf_counter()
    # Guards the "already stored?" check + the file write so two concurrent
    # downloads of identical bytes cannot both create a file.
    write_lock = asyncio.Lock()

    async with session() as s:
        stmt = select(Image).where(Image.status == "pending")
        if not include_failed:
            stmt = stmt.where(Image.attempts < settings.image_max_attempts)
        pending = list(await s.scalars(stmt.order_by(Image.id).limit(batch)))

    if not pending:
        _progress.update(
            {
                "running": False,
                "queued": 0,
                "downloaded": 0,
                "duplicates": 0,
                "failed": 0,
                "skipped": 0,
                "bytes": 0,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": 0.0,
                "errors": [],
            }
        )
        return result

    _progress.update(
        {
            "running": True,
            "queued": len(pending),
            "downloaded": 0,
            "duplicates": 0,
            "failed": 0,
            "skipped": 0,
            "bytes": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "elapsed_ms": 0.0,
            "errors": [],
        }
    )

    async def process(image: Image) -> None:
        async with sem:
            def count(field: str, error: str | None = None) -> None:
                setattr(result, field, getattr(result, field) + 1)
                _progress[field] = int(_progress.get(field, 0)) + 1
                if error and len(_progress["errors"]) < 20:
                    _progress["errors"].append({"host": image.host, "error": error[:200]})

            # Another batch (auto loop, or a second CLI run) may already have
            # handled this row; re-check before spending a request on it.
            async with session() as s:
                fresh = await s.get(Image, image.id)
                if fresh is None or fresh.status != "pending":
                    count("skipped")
                    return

            try:
                response: FetchResult = await client.get_bytes(
                    image.url,
                    referer=REFERER_BY_HOST.get(image.host),
                    max_size=settings.image_max_size,
                )
            except Exception as exc:  # noqa: BLE001 - recorded per image
                count("failed", str(exc))
                async with session() as s:
                    row = await s.get(Image, image.id)
                    if row is not None and not row.path:
                        row.attempts = (row.attempts or 0) + 1
                        row.status = "failed" if row.attempts >= settings.image_max_attempts else "pending"
                        row.last_error = str(exc)[:300]
                return

            data = response.content or b""
            ext = detect_extension(data, "")
            if ext is None:
                count("skipped")
                async with session() as s:
                    row = await s.get(Image, image.id)
                    if row is not None and not row.path:
                        row.status = "failed"
                        row.last_error = "not an image"
                return

            sha = hashlib.sha256(data).hexdigest()
            path = image_path(sha, ext)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Serialise the "is this content already stored?" check + the write so two
            # concurrent downloads of identical bytes cannot both create a file.
            async with write_lock:
                async with session() as s:
                    twin = await s.scalar(
                        select(Image).where(Image.sha256 == sha, Image.status.in_(("downloaded", "duplicate")))
                    )
                    if twin is not None:
                        row = await s.get(Image, image.id)
                        if row is not None:
                            row.sha256 = sha
                            row.ext = ext
                            row.size_bytes = len(data)
                            row.status = "duplicate"
                            row.path = twin.path
                            row.downloaded_at = twin.downloaded_at
                        count("duplicates")
                        return

                    tmp = path.with_suffix(path.suffix + ".tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, path)

                    row = await s.get(Image, image.id)
                    if row is not None:
                        row.sha256 = sha
                        row.ext = ext
                        row.size_bytes = len(data)
                        row.path = relative_path(path)
                        row.status = "downloaded"
                    row.downloaded_at = datetime.now(timezone.utc)
                    row.last_error = None
                count("downloaded")
                result.bytes += len(data)
                _progress["bytes"] = int(_progress.get("bytes", 0)) + len(data)

            if settings.image_delay:
                await asyncio.sleep(settings.image_delay)

    await asyncio.gather(*(process(image) for image in pending))
    result.attempted = len(pending)
    result.elapsed_ms = (time.perf_counter() - started) * 1000
    _progress.update(
        {
            "running": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": result.elapsed_ms,
        }
    )

    if own_client:
        await client.aclose()
    return result


async def image_stats() -> dict[str, object]:
    async with session() as s:
        status_rows = await s.execute(select(Image.status, func.count(Image.id)).group_by(Image.status))
        by_status = {status: count for status, count in status_rows}
        total_bytes = await s.scalar(select(func.sum(Image.size_bytes))) or 0
        hosts = list(
            await s.execute(
                select(Image.host, func.count(Image.id))
                .where(Image.status == "downloaded")
                .group_by(Image.host)
                .order_by(func.count(Image.id).desc())
                .limit(10)
            )
        )
        shard_count = await s.scalar(select(func.count(func.distinct(func.substr(Image.sha256, 1, 2)))))

    root = Path(settings.image_dir)
    on_disk = sum(1 for shard in root.glob("*/*") for _ in shard.iterdir()) if root.exists() else 0

    return {
        "image_dir": str(root),
        "total": sum(by_status.values()),
        "by_status": by_status,
        "bytes": int(total_bytes),
        "files_on_disk": on_disk,
        "shards_used": int(shard_count or 0),
        "top_hosts": [{"host": host, "count": count} for host, count in hosts],
    }


async def prune_images(delete_files: bool = True) -> dict[str, int]:
    """Drop image rows nobody references any more, and delete their files."""
    async with session() as s:
        referenced = {
            url
            for url in (
                normalize_image_url(pic)
                for pic in await s.scalars(select(SourceItem.pic).where(SourceItem.pic != ""))
            )
            if url
        }
        referenced.update(
            url
            for url in (
                normalize_image_url(pic)
                for pic in await s.scalars(select(Video.pic).where(Video.pic != ""))
            )
            if url
        )

        rows = list(await s.scalars(select(Image)))
        removed = 0
        deleted_files = 0
        for row in rows:
            if row.url in referenced:
                continue
            if delete_files and row.path and row.status == "downloaded":
                path = Path(settings.image_dir) / row.path
                if path.exists():
                    path.unlink()
                    deleted_files += 1
            await s.delete(row)
            removed += 1

        # Files on disk that no row points at (crash, manual copy, race) are
        # removed too, so the shard directories stay in sync with the index.
        if delete_files:
            known = {
                path
                for path in await s.scalars(select(Image.path).where(Image.path != ""))
            }
            root = Path(settings.image_dir)
            for shard in root.glob("*/*"):
                for file in shard.iterdir():
                    if file.is_file() and relative_path(file) not in known:
                        file.unlink()
                        deleted_files += 1
    return {"removed": removed, "files_deleted": deleted_files}


async def repair_images() -> dict[str, int]:
    """Reconcile the index with the filesystem.

    Rows whose file is on disk but are still marked pending/failed are counted
    as downloaded; rows marked duplicate with no owner are promoted; rows whose
    file vanished are queued again. Leftover `.tmp` files are removed.
    """
    restored = 0
    requeued = 0
    deleted = 0
    root = Path(settings.image_dir)

    for tmp in root.glob("*/*/*.tmp"):
        tmp.unlink()
        deleted += 1

    async with session() as s:
        rows = list(await s.scalars(select(Image).where(Image.path != "")))
        groups: dict[str, list[Image]] = {}
        for row in rows:
            groups.setdefault(row.sha256 or row.path, []).append(row)

        for group in groups.values():
            for row in group:
                if not (root / row.path).exists():
                    if row.status in ("downloaded", "duplicate"):
                        row.status = "pending"
                        row.attempts = 0
                        requeued += 1
                elif row.status in ("pending", "failed"):
                    row.status = "downloaded"
                    row.last_error = None
                    restored += 1

            if not any(r.status == "downloaded" and (root / r.path).exists() for r in group):
                for row in group:
                    if (root / row.path).exists():
                        row.status = "downloaded"
                        row.last_error = None
                        restored += 1
                        break
            else:
                # More than one row "owns" the same bytes (two processes raced):
                # keep the first as the owner, the rest are duplicates.
                owner = next(
                    (r for r in group if r.status == "downloaded" and (root / r.path).exists()),
                    None,
                )
                for row in group:
                    if row is not owner and row.status == "downloaded":
                        row.status = "duplicate"
                        row.path = owner.path
                        row.downloaded_at = owner.downloaded_at
                        restored += 1
    return {"restored": restored, "requeued": requeued, "files_deleted": deleted}
