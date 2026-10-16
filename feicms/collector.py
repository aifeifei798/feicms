from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .fetcher import Fetcher, build_query, fetch
from .models import CollectJob, Image, Source, SourceItem, SourceType, Video
from .normalizer import VodItem, build_signature, clean_text, parse_play_url, parse_raw_items, parse_response
from .db import IS_SQLITE, session, write_lock

log = logging.getLogger("feicms.collector")

# Absolute runaway guard: only used when a source reports neither pagecount nor
# total and keeps handing back new items. A real full sweep is bounded by the
# pagecount the source itself reports.
RUNAWAY_PAGE_GUARD = 10000
# A source that fails this many pages in a row is unreachable/rate-limiting, so
# the sweep stops instead of walking thousands of pages.
MAX_CONSECUTIVE_PAGE_FAILURES = 5


def acquire_collect_lock(label: str = "collect") -> bool:
    """Best-effort cross-process lock.

    A long `feicms collect --full` running from the shell and the service's own
    auto-collect loop would otherwise walk the same database twice at once.
    A lock whose holder is gone (crash, reboot, kill -9) is stale immediately;
    an old one whose holder cannot be checked goes stale after FEICMS_LOCK_TTL.
    """
    path = Path(settings.lock_file)
    if path.exists():
        info: dict[str, Any] | None
        try:
            info = json.loads(path.read_text())
        except (ValueError, OSError, json.JSONDecodeError):
            info = None
        if info is not None:
            holder = info.get("pid")
            if isinstance(holder, int) and holder > 1 and not _pid_alive(holder):
                # The sweep that wrote this lock died (a reboot leaves both the
                # file and a dead pid behind): take over now instead of waiting
                # out the whole TTL. This is what makes 断点续传 work after a crash.
                log.info("taking over a stale collection lock held by dead pid %s", holder)
            else:
                try:
                    started = datetime.fromisoformat(info.get("started_at", ""))
                except (ValueError, TypeError):
                    return False
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - started < timedelta(hours=settings.lock_ttl_hours):
                    return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "label": label,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
    except OSError:
        return False
    return True


def release_collect_lock() -> None:
    try:
        Path(settings.lock_file).unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by somebody else
    except OSError:
        return True  # unknown: assume alive rather than steal a live lock
    return True


def collect_lock_info() -> dict[str, Any] | None:
    """Who is holding the collection lock, or None when nobody is."""
    path = Path(settings.lock_file)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError, json.JSONDecodeError):
        return None


def collect_lock_is_stale() -> bool:
    """True when the lock file is left over from a sweep that is no longer running."""
    info = collect_lock_info()
    if info is None:
        return not Path(settings.lock_file).exists()
    holder = info.get("pid")
    if isinstance(holder, int) and holder > 1:
        return not _pid_alive(holder)
    try:
        started = datetime.fromisoformat(info.get("started_at", ""))
    except (ValueError, TypeError):
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started >= timedelta(hours=settings.lock_ttl_hours)


# ---------------------------------------------------------------- 断点续传 journal
# A real `--full` sweep is hundreds of thousands of page requests, so an
# interrupted run (reboot, Ctrl-C, OOM kill) must not start over from page 1.
# Each finished page records `next_page` for that source; the next `--resume`
# run skips the sources that already finished and continues the rest.
RESUME_VERSION = 1


def resume_file_path() -> Path:
    return Path(settings.resume_file)


def load_resume() -> dict[str, Any]:
    """Read the checkpoint file; a missing or foreign file is treated as empty."""
    path = resume_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != RESUME_VERSION:
        return {}
    if not isinstance(data.get("sources"), dict):
        return {}
    return data


def save_resume(state: dict[str, Any]) -> None:
    path = resume_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        tmp.replace(path)
    except OSError:
        log.warning("could not write resume checkpoint %s", path)


def clear_resume() -> None:
    try:
        resume_file_path().unlink()
    except OSError:
        pass


def resume_summary() -> dict[str, Any]:
    """What a `--resume` run would skip (used by the CLI / panel to report)."""
    state = load_resume()
    sources = state.get("sources") or {}
    entries = [
        {**entry, "source_id": int(sid)}
        for sid, entry in sources.items()
        if isinstance(entry, dict) and str(sid).isdigit()
    ]
    finished = [e for e in entries if e.get("status") == "done"]
    pending = [e for e in entries if e.get("status") != "done"]
    return {
        "exists": bool(state),
        "done": bool(state.get("done")),
        "started_at": state.get("started_at"),
        "updated_at": state.get("updated_at"),
        "sources": len(entries),
        "finished": len(finished),
        "detail": sorted(finished, key=lambda e: str(e.get("name") or "")),
        "pending": sorted(pending, key=lambda e: str(e.get("name") or "")),
    }


@dataclass
class CollectResult:
    source_id: int
    source_name: str
    pages: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None
    elapsed_ms: float = 0.0
    pagecount: int = 0
    total: int = 0
    warnings: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def get_source(session_, source_id: int | None = None, name: str | None = None) -> Source | None:
    if source_id is not None:
        return await session_.get(Source, source_id)
    if name is not None:
        return await session_.scalar(select(Source).where(Source.name == name))
    return None


async def upsert_page(
    session_: AsyncSession,
    source: Source,
    items: list[VodItem],
    type_map: dict[int, str],
) -> tuple[int, int, int, int]:
    """Write one fetched page in bulk. Returns (inserted, updated, skipped, changes).

    Two key lookups per page (existing videos by signature, existing items by
    (source_id, vod_id)) instead of two SELECTs per item: a full sweep of 73
    sources would otherwise spend hundreds of thousands of round trips just
    checking whether a row already exists.
    """
    for item in items:
        if not item.type_name and item.type_id:
            item.type_name = type_map.get(item.type_id, "")

    signatures = {item.signature for item in items}
    videos_by_sig = {
        video.signature: video
        for video in await session_.scalars(select(Video).where(Video.signature.in_(signatures)))
    }
    vod_ids = [item.vod_id for item in items]
    items_by_key = {
        row.source_vod_id: row
        for row in await session_.scalars(
            select(SourceItem).where(
                SourceItem.source_id == source.id,
                SourceItem.source_vod_id.in_(vod_ids),
            )
        )
    }

    # Create missing videos first so every SourceItem can reference a flushed id.
    for item in items:
        if item.signature not in videos_by_sig:
            video = Video(
                name=item.name,
                signature=item.signature,
                type_name=item.type_name,
                type_id=item.type_id,
                year=item.year,
                area=item.area,
                lang=item.lang,
                pic=item.pic,
                blurb=item.blurb,
                actor=item.actor,
                director=item.director,
                remarks=item.remarks,
                source_count=0,
            )
            session_.add(video)
            videos_by_sig[item.signature] = video
    await session_.flush()

    now = datetime.now(timezone.utc)
    inserted = updated = skipped = 0
    seen_on_page: set[int] = set()
    for item in items:
        video = videos_by_sig[item.signature]
        plays_json = json.dumps([p.as_dict() for p in item.plays], ensure_ascii=False)
        # The raw play_url is only kept when parsing produced nothing: it is then
        # the only material `repair_plays` can re-parse from. Once the structured
        # `plays` exists the raw string is pure duplication (it dominates the DB).
        raw_play_url = "" if item.plays else item.play_url

        existing = items_by_key.get(item.vod_id)
        if existing is None:
            if item.vod_id in seen_on_page:
                continue  # the page itself lists the same vod_id twice
            seen_on_page.add(item.vod_id)
            session_.add(
                SourceItem(
                    source_id=source.id,
                    video_id=video.id,
                    source_vod_id=item.vod_id,
                    name=item.name,
                    type_name=item.type_name,
                    type_id=item.type_id,
                    year=item.year,
                    area=item.area,
                    lang=item.lang,
                    pic=item.pic,
                    blurb=item.blurb,
                    actor=item.actor,
                    director=item.director,
                    remarks=item.remarks,
                    vod_time=item.vod_time,
                    play_from=item.play_from,
                    play_url=raw_play_url,
                    plays=plays_json,
                )
            )
            video.source_count += 1
            video.last_updated = now
            inserted += 1
            continue

        changed = False
        for field_name, value in (
            ("name", item.name),
            ("type_name", item.type_name),
            ("type_id", item.type_id),
            ("year", item.year),
            ("area", item.area),
            ("lang", item.lang),
            ("pic", item.pic),
            ("blurb", item.blurb),
            ("actor", item.actor),
            ("director", item.director),
            ("remarks", item.remarks),
            ("vod_time", item.vod_time),
            ("play_from", item.play_from),
            ("play_url", raw_play_url),
            ("plays", plays_json),
        ):
            if value and getattr(existing, field_name) != value:
                setattr(existing, field_name, value)
                changed = True

        if changed:
            video.last_updated = now
            updated += 1
        else:
            skipped += 1

    return inserted, updated, skipped, inserted + updated


async def load_type_map(client: Fetcher, source: Source) -> dict[int, str]:
    """Best-effort: read `ac=types` so items with only a type_id still get a name."""
    try:
        result = await fetch(client, build_query(source.url, "types", 1, 100))
        raw_items = parse_raw_items(result.text, source.api_format)
    except Exception:  # noqa: BLE001 - types are optional metadata
        return {}
    mapping: dict[int, str] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        tid = raw.get("type_id") or raw.get("tid")
        name = clean_text(raw.get("type_name") or raw.get("name"))
        if tid and name:
            mapping[int(tid)] = name
    return mapping


async def collect_source(
    source: Source,
    pages: int | None = None,
    ac: str = "detail",
    limit: int | None = None,
    stop_on_no_change: bool = True,
    client: Fetcher | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    start_page: int = 1,
    seed: dict[str, Any] | None = None,
) -> CollectResult:
    own_client = client is None
    client = client or Fetcher()
    result = CollectResult(source_id=source.id, source_name=source.name)
    if seed:
        # 断点续传: keep the counters the interrupted run had already produced so
        # the final report covers the whole sweep, not just the resumed part.
        result.pages = int(seed.get("pages") or 0)
        result.pagecount = int(seed.get("pagecount") or 0)
        result.total = int(seed.get("total") or 0)
        result.inserted = int(seed.get("inserted") or 0)
        result.updated = int(seed.get("updated") or 0)
        result.skipped = int(seed.get("skipped") or 0)

    # pages semantics:
    #   None  -> use the per-source max_pages (incremental, stops early)
    #   > 0   -> exactly that many pages
    #   <= 0  -> full sweep: walk every page the source actually reports. The
    #            ceiling is whatever the source says (pagecount, or total/limit);
    #            FEICMS_FULL_PAGE_CAP is only a safety brake and 0 means "no brake".
    full = pages is not None and pages <= 0
    max_pages = settings.full_page_cap if full else (pages if pages is not None else source.max_pages)
    if full:
        stop_on_no_change = False
    limit = limit or settings.page_limit
    started = time.perf_counter()
    if on_progress is not None:
        on_progress(
            {
                "pages": result.pages,
                "pagecount": result.pagecount,
                "target": max_pages,
                "inserted": result.inserted,
                "updated": result.updated,
                "skipped": result.skipped,
            }
        )

    try:
        type_map = await load_type_map(client, source)
        if type_map:
            async with write_lock():
                async with session() as s:
                    for tid, tname in type_map.items():
                        existing = await s.scalar(
                            select(SourceType).where(SourceType.source_id == source.id, SourceType.type_id == tid)
                        )
                        if existing:
                            existing.type_name = tname
                        else:
                            s.add(SourceType(source_id=source.id, type_id=tid, type_name=tname))

        page = max(1, int(start_page or 1))
        previous_key: str | None = None
        consecutive_failures = 0
        last_latency: float | None = None
        while True:
            if max_pages and page > max_pages:
                break
            if page > RUNAWAY_PAGE_GUARD:
                result.stopped_reason = (
                    f"stopped after {RUNAWAY_PAGE_GUARD} pages: the source reports no pagecount "
                    "and keeps returning items"
                )
                break

            try:
                url = build_query(source.url, ac, page, limit)
                response = await fetch(client, url)
                items, pagecount, total = parse_response(response.text, source.api_format)

                # Some sites do not implement ac=detail; fall back to the list endpoint.
                if not items and ac == "detail":
                    response = await fetch(client, build_query(source.url, "list", page, limit))
                    items, pagecount, total = parse_response(response.text, source.api_format)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the sweep
                consecutive_failures += 1
                result.warnings.append(f"page {page}: {exc}")
                if page == 1:
                    result.error = f"page 1 failed: {exc}"
                    break
                if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                    result.stopped_reason = (
                        f"stopped at page {page}: {consecutive_failures} pages failed in a row"
                    )
                    break
                page += 1
                continue

            result.pagecount = pagecount or result.pagecount
            result.total = total or result.total

            if not items:
                if page > 1:
                    # Nothing left to fetch: the catalogue ends here.
                    break
                result.error = f"page {page} returned no items (HTTP {response.status_code})"
                break

            # Sites that report only a total: derive the real number of pages from
            # how many items the page actually carried (many force limit=20).
            if not result.pagecount and result.total and items:
                result.pagecount = -(-result.total // len(items))

            # A source that ignores the page parameter keeps handing back the same
            # list; walking it forever would be pointless.
            page_key = "|".join(f"{item.vod_id}:{item.name}" for item in items)
            if page > 1 and page_key == previous_key:
                result.stopped_reason = f"source repeats page {page - 1} at page {page}"
                break
            previous_key = page_key
            consecutive_failures = 0

            page_changes = 0
            for attempt in range(5):
                try:
                    async with write_lock():
                        async with session() as s:
                            inserted, updated, skipped, page_changes = await upsert_page(
                                s, source, items, type_map
                            )
                    last_latency = response.latency_ms
                    result.inserted += inserted
                    result.updated += updated
                    result.skipped += skipped
                    break
                except (IntegrityError, OperationalError):
                    # Two sources created the same title at the same time, or every
                    # source is writing at once and sqlite briefly locked: the
                    # transaction was rolled back, so replay the page.
                    if attempt == 4:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))

            result.pages = page
            if on_progress is not None:
                on_progress(
                    {
                        "pages": page,
                        "pagecount": result.pagecount,
                        "total": result.total,
                        "inserted": result.inserted,
                        "updated": result.updated,
                        "skipped": result.skipped,
                    }
                )
            if stop_on_no_change and page_changes == 0 and page > 1:
                break
            if result.pagecount and page >= result.pagecount:
                break
            page += 1
            if settings.request_delay:
                await asyncio.sleep(settings.request_delay)

        if result.pages:
            # Source stats are refreshed once per sweep instead of once per page.
            async with write_lock():
                async with session() as s:
                    src = await s.get(Source, source.id)
                    if src is not None:
                        src.last_collect_at = datetime.now(timezone.utc)
                        src.last_page = result.pages
                        if last_latency is not None:
                            src.latency_ms = int(last_latency)
                        src.health_status = "ok"
                        src.fail_streak = 0
                        src.last_error = None
                        src.item_count = (
                            await s.scalar(
                                select(func.count()).select_from(SourceItem).where(SourceItem.source_id == source.id)
                            )
                            or 0
                        )
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        async with write_lock():
            async with session() as s:
                src = await s.get(Source, source.id)
                if src is not None:
                    src.fail_streak = (src.fail_streak or 0) + 1
                    src.last_error = str(exc)
                    src.last_health_at = datetime.now(timezone.utc)
                    if src.fail_streak >= settings.health_fail_threshold:
                        src.health_status = "dead"
    finally:
        if own_client:
            await client.aclose()

    result.elapsed_ms = (time.perf_counter() - started) * 1000

    async with session() as s:
        s.add(
            CollectJob(
                source_id=source.id,
                finished_at=result.finished_at,
                pages=result.pages,
                inserted=result.inserted,
                updated=result.updated,
                skipped=result.skipped,
                error=result.error,
            )
        )
    return result


async def _select_sources(
    s: AsyncSession,
    names: list[str] | None = None,
    only_enabled: bool = True,
    source_ids: list[int] | None = None,
) -> list[Source]:
    stmt = select(Source)
    if only_enabled:
        stmt = stmt.where(Source.enabled)
    if names:
        stmt = stmt.where(Source.name.in_(names))
    if source_ids:
        stmt = stmt.where(Source.id.in_(source_ids))
    return list(await s.scalars(stmt.order_by(Source.priority.desc(), Source.id)))


async def collect_all(
    names: list[str] | None = None,
    pages: int | None = None,
    only_enabled: bool = True,
    source_ids: list[int] | None = None,
    on_result: Callable[[CollectResult], None] | None = None,
    on_progress: Callable[[int, dict[str, Any]], None] | None = None,
    client_factory: Callable[[], Fetcher] | None = None,
    resume: bool = False,
) -> list[CollectResult]:
    async with session() as s:
        sources = await _select_sources(s, names, only_enabled, source_ids)

    if not acquire_collect_lock("collect"):
        holder = collect_lock_info() or {}
        log.warning(
            "another collection sweep is running (pid %s), skipping this one",
            holder.get("pid", "?"),
        )
        return []

    # 0 (default) means "no ceiling": every source is a separate site, so all of
    # them are walked at once. A positive number throttles how many run together.
    limit = settings.concurrency
    sem = asyncio.Semaphore(limit) if limit and limit > 0 else None

    # ---------------------------------------------------------------- 断点续传
    # Only a full sweep is worth a checkpoint (an incremental run is a handful of
    # pages per source). The file is rewritten after every finished page, but at
    # most about once a second so a 300k-page sweep does not hammer the disk.
    journal: dict[str, Any] | None = None
    last_save = 0.0

    def persist(force: bool = False) -> None:
        nonlocal last_save
        if journal is None:
            return
        now = time.monotonic()
        if not force and now - last_save < 1.0:
            return
        last_save = now
        journal["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_resume(journal)

    def checkpoint(source_id: int, name: str, **fields: Any) -> None:
        if journal is None:
            return
        entry = journal["sources"].setdefault(str(source_id), {"name": name})
        entry["name"] = name
        entry.update(fields)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()

    if pages is not None and pages <= 0 and settings.resume:
        previous = load_resume() if resume else {}
        if previous.get("done"):
            previous = {}  # the last sweep ran to the end: nothing to continue
        journal = {
            "version": RESUME_VERSION,
            "started_at": previous.get("started_at") or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "done": False,
            "pages": pages,
            "resumed": bool(previous),
            "sources": dict(previous.get("sources") or {}),
        }
        persist(force=True)

    async def run(src: Source) -> CollectResult:
        # Resume: a source that already finished is reused as-is, a partial one
        # continues from `next_page` with its counters intact.
        start_page = 1
        seed: dict[str, Any] | None = None
        if journal is not None:
            entry = journal["sources"].get(str(src.id))
            if resume and entry:
                if entry.get("status") == "done":
                    result = CollectResult(
                        source_id=src.id,
                        source_name=src.name,
                        pages=int(entry.get("pages") or 0),
                        pagecount=int(entry.get("pagecount") or 0),
                        total=int(entry.get("total") or 0),
                        inserted=int(entry.get("inserted") or 0),
                        updated=int(entry.get("updated") or 0),
                        skipped=int(entry.get("skipped") or 0),
                    )
                    log.info("resume: %s already finished (%s pages)", src.name, result.pages)
                    if on_result is not None:
                        try:
                            on_result(result)
                        except Exception:  # noqa: BLE001
                            log.warning("result callback failed for %s", src.name)
                    return result
                next_page = int(entry.get("next_page") or 0)
                if next_page > 1:
                    start_page = next_page
                    seed = entry
                    log.info("resume: %s continues from page %s", src.name, start_page)
            checkpoint(src.id, src.name, status="running", start_page=start_page)
            persist(force=True)

        page_cb = None
        if on_progress is not None or journal is not None:

            def page_cb(snap: dict[str, Any]) -> None:
                # progress reporting is cosmetic: never let it break a multi-hour sweep
                if journal is not None and snap.get("pages"):
                    checkpoint(
                        src.id,
                        src.name,
                        status="running",
                        pages=snap.get("pages", 0),
                        pagecount=snap.get("pagecount", 0),
                        total=snap.get("total", 0),
                        inserted=snap.get("inserted", 0),
                        updated=snap.get("updated", 0),
                        skipped=snap.get("skipped", 0),
                        next_page=int(snap.get("pages", 0)) + 1,
                    )
                    persist()
                if on_progress is not None:
                    try:
                        on_progress(src.id, {**snap, "name": src.name})
                    except Exception:  # noqa: BLE001
                        log.warning("progress callback failed for %s", src.name)

        try:
            client = client_factory() if client_factory is not None else None
            if sem is None:
                result = await collect_source(
                    src, pages=pages, client=client, on_progress=page_cb,
                    start_page=start_page, seed=seed,
                )
            else:
                async with sem:
                    result = await collect_source(
                        src, pages=pages, client=client, on_progress=page_cb,
                        start_page=start_page, seed=seed,
                    )
        except Exception as exc:  # noqa: BLE001 - one broken source must not abort the sweep
            log.exception("source %s crashed", src.name)
            result = CollectResult(source_id=src.id, source_name=src.name, error=f"内部错误: {exc}")
        if journal is not None:
            finished = not (result.error or result.stopped_reason)
            checkpoint(
                src.id,
                src.name,
                status="done" if finished else "failed",
                pages=result.pages,
                pagecount=result.pagecount,
                total=result.total,
                inserted=result.inserted,
                updated=result.updated,
                skipped=result.skipped,
                next_page=0 if finished else max(1, (result.pages or 0) + 1),
                error=result.error,
                stopped_reason=result.stopped_reason,
                elapsed_ms=round(result.elapsed_ms, 1),
            )
            persist(force=True)
        if on_result is not None:
            try:
                on_result(result)
            except Exception:  # noqa: BLE001
                log.warning("result callback failed for %s", src.name)
        return result

    try:
        results = await asyncio.gather(*(run(src) for src in sources))
    finally:
        release_collect_lock()
        if journal is not None:
            # Sources still marked "running" were cut off (Ctrl-C / power loss):
            # keep their checkpoint so the next --resume continues from there.
            interrupted = 0
            for entry in journal["sources"].values():
                if entry.get("status") == "running":
                    entry["status"] = "failed"
                    entry.setdefault("error", "本轮未跑完（被中断）")
                    interrupted += 1
            # The sweep counts as finished only when every source in *this* run
            # reached the end; a source that failed half way keeps it resumable.
            selected = [journal["sources"].get(str(src.id)) for src in sources]
            journal["done"] = bool(selected) and all(
                entry is not None and entry.get("status") == "done" for entry in selected
            )
            journal["finished_at"] = datetime.now(timezone.utc).isoformat()
            if interrupted:
                log.info("sweep interrupted: %s sources left to resume", interrupted)
            save_resume(journal)
    return results


# ---------------------------------------------------------------- background sweeps
# A single in-process state so the panel can show live progress and the scheduler
# never runs two sweeps at the same time (SQLite would just lock up).
_collect_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total_sources": 0,
    "done_sources": 0,
    "inserted": 0,
    "updated": 0,
    "skipped": 0,
    "failed_sources": 0,
    "error": None,
    "sources": [],
}

# Per-source progress survives across sweeps so the panel can keep showing the
# last result of sources that are not part of the current run.
_source_progress: dict[int, dict[str, Any]] = {}


def collect_progress() -> dict[str, Any]:
    """Snapshot of the current (or last) collection sweep."""
    return dict(_collect_state)


def write_status_snapshot() -> None:
    """Mirror the sweep state to `settings.status_file`.

    `./server.sh full-status` reads that file, so both the CLI sweep and a sweep
    started by the service (auto-collect or the 采集 panel) show up there.
    """
    current = [e for e in _source_progress.values() if e.get("current")]
    payload = {
        "running": bool(_collect_state.get("running")),
        "started_at": _collect_state.get("started_at"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": _collect_state.get("total_sources", 0),
        "done_sources": _collect_state.get("done_sources", 0),
        "pages": sum(e.get("pages", 0) for e in current),
        "inserted": _collect_state.get("inserted", 0),
        "updated": _collect_state.get("updated", 0),
        "skipped": _collect_state.get("skipped", 0),
        "sources": sorted(current, key=lambda e: e.get("pages", 0), reverse=True),
        "finished": [
            {
                "name": e.get("name"),
                "status": e.get("status"),
                "note": e.get("stopped_reason") or "",
            }
            for e in current
            if e.get("status") in ("done", "failed")
        ],
    }
    try:
        path = Path(settings.status_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False))
    except OSError:
        log.warning("could not write status file %s", settings.status_file)


def _progress_entry(src: Source) -> dict[str, Any]:
    return {
        "source_id": src.id,
        "name": src.name,
        "status": "pending",
        "pages": 0,
        "pagecount": 0,
        "target": src.max_pages,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "elapsed_ms": 0,
        "warnings": 0,
        "stopped_reason": None,
        "current": True,
        "enabled": src.enabled,
    }


async def start_collect_all(
    names: list[str] | None = None,
    pages: int | None = None,
    only_enabled: bool = True,
    source_ids: list[int] | None = None,
    client_factory: Callable[[], Fetcher] | None = None,
    resume: bool | None = None,
) -> dict[str, Any]:
    """Kick off a collection sweep in the background and return its progress state."""
    if _collect_state["running"]:
        return dict(_collect_state)

    async def prepare() -> tuple[list[Source], list[Source]]:
        # Sources taking part in this sweep, plus every known source so the
        # panel can keep listing the ones that are not part of it.
        async with session() as s:
            selected = await _select_sources(s, names, only_enabled, source_ids)
            everyone = await _select_sources(s, None, False, None)
        return selected, everyone

    selected, everyone = await prepare()
    selected_ids = {src.id for src in selected}

    known = {src.id for src in everyone}
    for source_id in list(_source_progress):
        if source_id not in known:
            del _source_progress[source_id]

    for src in everyone:
        entry = _source_progress.get(src.id)
        if entry is None:
            entry = _progress_entry(src)
            _source_progress[src.id] = entry
        entry["name"] = src.name
        if pages is None:
            entry["target"] = src.max_pages
        elif pages > 0:
            entry["target"] = pages
        else:
            # Full sweep: the ceiling is the pagecount the source reports, which
            # is only known once the first page has been fetched.
            entry["target"] = 0
        entry["enabled"] = src.enabled
        entry["current"] = src.id in selected_ids
        if src.id in selected_ids:
            entry.update(
                {
                    "status": "pending",
                    "pages": 0,
                    "pagecount": 0,
                    "inserted": 0,
                    "updated": 0,
                    "skipped": 0,
                    "elapsed_ms": 0,
                }
            )

    by_id = {src.id: _source_progress[src.id] for src in selected}
    progress_events = 0

    def on_progress(source_id: int, snap: dict[str, Any]) -> None:
        nonlocal progress_events
        entry = by_id.get(source_id)
        if entry is not None:
            entry.update(snap)
            entry["status"] = "running"
        # A full sweep is hundreds of thousands of pages: only write the file
        # every few events so the disk is not hammered.
        progress_events += 1
        if progress_events % 10 == 0:
            write_status_snapshot()

    def on_result(result: CollectResult) -> None:
        _collect_state["done_sources"] += 1
        _collect_state["inserted"] += result.inserted
        _collect_state["updated"] += result.updated
        _collect_state["skipped"] += result.skipped
        if result.error or result.stopped_reason:
            _collect_state["failed_sources"] += 1
        entry = by_id.get(result.source_id)
        if entry is not None:
            entry.update(
                {
                    "status": "failed" if (result.error or result.stopped_reason) else "done",
                    "pages": result.pages,
                    "pagecount": result.pagecount or entry.get("pagecount", 0),
                    "inserted": result.inserted,
                    "updated": result.updated,
                    "skipped": result.skipped,
                    "elapsed_ms": round(result.elapsed_ms, 1),
                    "warnings": len(result.warnings),
                    "stopped_reason": result.stopped_reason,
                }
            )
        write_status_snapshot()

    async def runner() -> None:
        try:
            await collect_all(
                names,
                pages,
                only_enabled,
                source_ids,
                on_result=on_result,
                on_progress=on_progress,
                client_factory=client_factory,
                resume=settings.resume if resume is None else resume,
            )
        except Exception as exc:  # noqa: BLE001
            _collect_state["error"] = str(exc)
        finally:
            _collect_state["running"] = False
            _collect_state["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_status_snapshot()

    _collect_state.update(
        running=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        total_sources=len(selected),
        done_sources=0,
        inserted=0,
        updated=0,
        skipped=0,
        failed_sources=0,
        error=None,
        sources=list(_source_progress.values()),
    )
    write_status_snapshot()
    asyncio.create_task(runner())
    return dict(_collect_state)


async def merge_duplicates(dry_run: bool = False) -> dict[str, Any]:
    """Merge videos that share the same normalised title.

    Older records were keyed by `title|year`, so the same show could end up as
    two rows. Titles are re-keyed with the current `build_signature()` and every
    duplicate's `source_items` (and therefore its play sources) are re-attached
    to the richest surviving row.
    """
    async with session() as s:
        videos = list(await s.scalars(select(Video).order_by(Video.id)))
        counts = {
            row[0]: row[1]
            for row in await s.execute(
                select(SourceItem.video_id, func.count()).group_by(SourceItem.video_id)
            )
        }

        groups: dict[str, list[Video]] = defaultdict(list)
        for video in videos:
            groups[build_signature(video.name)].append(video)

        merged_groups = 0
        removed = 0
        moved_items = 0
        fixed = 0
        examples: list[dict[str, Any]] = []

        for signature, group in groups.items():
            if len(group) < 2:
                # Legacy `title|year` signature on a single row: re-key it so a
                # later collection of the same title merges instead of duplicating.
                if group[0].signature != signature:
                    group[0].signature = signature
                    fixed += 1
                continue
            merged_groups += 1

            def richness(v: Video) -> tuple[int, int, int]:
                return (
                    counts.get(v.id, 0),
                    1 if v.blurb else 0,
                    1 if v.pic else 0,
                )

            keeper = max(group, key=lambda v: (richness(v), -v.id))
            others = [v for v in group if v.id != keeper.id]
            removed += len(others)
            moved_items += sum(counts.get(v.id, 0) for v in others)

            if dry_run:
                if len(examples) < 20:
                    examples.append(
                        {
                            "signature": signature,
                            "name": keeper.name,
                            "keep": keeper.id,
                            "merge": [v.id for v in others],
                        }
                    )
                continue

            for dup in others:
                await s.execute(
                    update(SourceItem).where(SourceItem.video_id == dup.id).values(video_id=keeper.id)
                )
                await s.execute(update(Image).where(Image.video_id == dup.id).values(video_id=keeper.id))

                for field_name in (
                    "type_name",
                    "type_id",
                    "year",
                    "area",
                    "lang",
                    "category",
                    "pic",
                    "blurb",
                    "actor",
                    "director",
                    "remarks",
                ):
                    if not (getattr(keeper, field_name) or ""):
                        value = getattr(dup, field_name) or ""
                        if value:
                            setattr(keeper, field_name, value)

                if dup.first_seen < keeper.first_seen:
                    keeper.first_seen = dup.first_seen
                if dup.last_updated > keeper.last_updated:
                    keeper.last_updated = dup.last_updated

                await s.execute(delete(Video).where(Video.id == dup.id))

            keeper.signature = signature
            keeper.source_count = int(
                await s.scalar(
                    select(func.count(func.distinct(SourceItem.source_id))).where(
                        SourceItem.video_id == keeper.id
                    )
                )
                or 0
            )

        if not dry_run:
            await s.commit()

    return {
        "groups": merged_groups,
        "removed": removed,
        "moved_items": moved_items,
        "fixed_signatures": fixed,
        "dry_run": dry_run,
        "examples": examples,
    }


async def repair_plays() -> dict[str, int]:
    """Re-parse every stored `play_from` / `play_url` with the current parser.

    Useful after the parser improves: rows whose parser produced nothing still
    keep their raw `play_url`, so the structured `plays` JSON can be rebuilt
    without re-fetching. Rows that already have structured plays have had their
    raw string dropped (see `compact_raw_plays`) and are left alone. Text
    columns are re-cleaned at the same time (HTML entities, stray markup).
    """
    changed = 0
    total = 0
    async with session() as s:
        items = list(await s.scalars(select(SourceItem)))
        for item in items:
            total += 1
            if item.play_url:
                plays = parse_play_url(item.play_from, item.play_url)
                new = json.dumps([p.as_dict() for p in plays], ensure_ascii=False)
                if new != (item.plays or "[]"):
                    item.plays = new
                    changed += 1
            for field_name in ("name", "type_name", "year", "area", "lang", "pic", "blurb", "actor", "director", "remarks"):
                old = getattr(item, field_name) or ""
                cleaned = clean_text(old)
                if cleaned != old:
                    setattr(item, field_name, cleaned)

        videos = list(await s.scalars(select(Video)))
        for video in videos:
            for field_name in ("name", "type_name", "area", "lang", "category", "blurb", "actor", "director", "remarks"):
                old = getattr(video, field_name) or ""
                cleaned = clean_text(old)
                if cleaned != old:
                    setattr(video, field_name, cleaned)
        await s.commit()
    return {"total": total, "changed": changed, "videos": len(videos)}


async def compact_raw_plays() -> dict[str, int]:
    """Drop the raw `play_url` from rows whose structured `plays` is already parsed.

    The raw string is only repair material for rows the parser could not
    structure; keeping both is pure duplication and `play_url` alone accounts
    for a large share of the database file. VACUUM runs afterwards (sqlite
    only) so the file actually shrinks.
    """
    async with session() as s:
        condition = SourceItem.play_url != ""
        condition = condition & SourceItem.plays.notin_(["", "[]"])
        cleared = (await s.scalar(select(func.count()).select_from(SourceItem).where(condition))) or 0
        await s.execute(update(SourceItem).where(condition).values(play_url=""))
        await s.commit()

    if IS_SQLITE:
        from sqlalchemy.engine import make_url
        import aiosqlite

        database = make_url(settings.database_url).database
        if database and database != ":memory:":
            async with aiosqlite.connect(database) as conn:
                await conn.execute("VACUUM")
    return {"cleared": cleared}
