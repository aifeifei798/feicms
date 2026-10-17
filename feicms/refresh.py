"""定向补扫：按 vod_id 直接抓单条详情，只刷新"扫不到"的陈旧记录。

一轮普通采集只走每个源的前 FEICMS_PAGES 页（上游按 vod_time 倒序排列，所以只有
刚更新过的片子在前几页）。名字没变、播放源变了的老片子躺在第几百页，普通采集永远
碰不到它，它的播放源就停留在上次全量扫描的快照。

绝大多数资源站支持 `ac=detail&ids=<vod_id>`，于是可以只抓"需要核对的那几条"，而不是
遍历几十万页。这个模块就是那件事：

    feicms refresh                      # 最老的 N 条，按源分组，批量按 id 抓
    feicms refresh --days 60 --limit 20000
    feicms refresh --name 360资源站 --limit 500
    feicms refresh --dry-run            # 只看有多少条要刷新，不写库
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from .collector import acquire_collect_lock, release_collect_lock, upsert_page
from .config import settings
from .db import session, write_lock
from .fetcher import Fetcher, fetch
from .models import Source, SourceItem, Video
from .normalizer import parse_response

log = logging.getLogger("feicms.refresh")


def build_ids_query(url: str, ids: list[int]) -> str:
    """`?ac=detail&ids=1,2,3` — same path, keeps whatever params the source uses."""
    parts = urlsplit(url)
    query = dict(pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair)
    query["ac"] = "detail"
    query["ids"] = ",".join(str(i) for i in ids)
    query["limit"] = str(len(ids))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            "&".join(f"{k}={v}" for k, v in query.items()),
            parts.fragment,
        )
    )


@dataclass
class RefreshResult:
    source_id: int
    source_name: str
    requested: int = 0
    fetched: int = 0
    gone: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    batches: int = 0
    mode: str = "batch"  # batch = ids answered in bulk, single = one id per request
    unsupported: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "requested": self.requested,
            "fetched": self.fetched,
            "gone": self.gone,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "batches": self.batches,
            "mode": self.mode,
            "unsupported": self.unsupported,
            "error": self.error,
            "warnings": len(self.warnings),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


async def pick_refresh_targets(
    days: int,
    limit: int,
    names: list[str] | None = None,
    only_enabled: bool = True,
    include_missing_plays: bool = True,
) -> dict[int, list[int]]:
    """Which source records need a direct re-check, grouped by source.

    A record is a candidate when it has never been checked, was checked longer than
    `days` ago, or its play sources are empty. Oldest first, so a bounded run works
    through the stalest part of the library rather than the newest.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    needs_check = or_(
        SourceItem.last_checked_at.is_(None),
        SourceItem.last_checked_at < cutoff,
    )
    if include_missing_plays:
        needs_check = or_(needs_check, SourceItem.plays.in_(("", "[]")))

    async with session() as s:
        stmt = (
            select(SourceItem.source_id, SourceItem.source_vod_id)
            .join(Video, Video.id == SourceItem.video_id)
            .join(Source, Source.id == SourceItem.source_id)
            .where(needs_check)
            .order_by(Video.last_updated.asc(), SourceItem.id.asc())
            .limit(limit)
        )
        if only_enabled:
            stmt = stmt.where(Source.enabled)
        if names:
            stmt = stmt.where(Source.name.in_(names))

        grouped: dict[int, list[int]] = {}
        for source_id, vod_id in (await s.execute(stmt)).all():
            grouped.setdefault(source_id, []).append(vod_id)
        return grouped


async def _fetch_by_ids(
    client: Fetcher, source: Source, ids: list[int]
) -> dict[int, Any] | None:
    """Ask a source for specific vod_ids. Returns None when it has no ids endpoint."""
    response = await fetch(client, build_ids_query(source.url, ids))
    items, _, _ = parse_response(response.text, source.api_format)
    got = {item.vod_id: item for item in items}
    if set(got) - set(ids):
        # The site ignored `ids` and answered with a generic list.
        return None
    return got


async def refresh_source(
    source: Source,
    vod_ids: list[int],
    client: Fetcher,
    batch: int,
    delay: float,
    dry_run: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> RefreshResult:
    result = RefreshResult(source_id=source.id, source_name=source.name)
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    # Some sites answer `ids=1,2,3` with only the first id. When a batch comes back
    # short we probe one of the missing ids on its own: if it exists, the source is
    # single-id and we walk the rest one by one; if it does not, the records really
    # are gone upstream.
    queue = deque(vod_ids)
    effective = batch

    while queue:
        chunk = [queue.popleft() for _ in range(effective) if queue]
        try:
            got = await _fetch_by_ids(client, source, chunk)
        except Exception as exc:  # noqa: BLE001 - one bad batch must not end the run
            result.warnings.append(f"batch {result.batches + 1}: {exc}")
            if result.batches == 0:
                result.error = f"按 id 抓取失败：{exc}"
                result.unsupported = True
                break
            continue

        if got is None:
            result.unsupported = True
            result.warnings.append("该站点不支持 ac=detail&ids=，按 id 补扫跳过")
            break

        result.batches += 1
        result.requested += len(chunk)
        result.fetched += len(got)
        missing = [vod_id for vod_id in chunk if vod_id not in got]

        if missing and effective > 1:
            try:
                probe = await _fetch_by_ids(client, source, [missing[0]])
            except Exception as exc:  # noqa: BLE001
                probe = None
                result.warnings.append(f"probe {missing[0]}: {exc}")
            result.batches += 1
            if probe is None:
                result.unsupported = True
                result.warnings.append("该站点不支持 ac=detail&ids=，按 id 补扫跳过")
                break
            if probe:
                # ids are only honoured one at a time on this site: re-queue the
                # ones this batch did not answer and walk them singly.
                effective = 1
                result.mode = "single"
                queue.extendleft(reversed(missing))
            else:
                # The probe found nothing either: those records are gone upstream.
                result.gone += len(missing)
        else:
            result.gone += len(missing)

        items = list(got.values())
        if dry_run:
            result.skipped += len(items)
        elif items:
            for attempt in range(5):
                try:
                    async with write_lock():
                        async with session() as s:
                            inserted, updated, skipped, _changes = await upsert_page(
                                s, source, items, {}
                            )
                            # Even records that turned out unchanged are marked as
                            # checked, so the next run moves on to fresher work.
                            await s.execute(
                                update(SourceItem)
                                .where(
                                    SourceItem.source_id == source.id,
                                    SourceItem.source_vod_id.in_(chunk),
                                )
                                .values(last_checked_at=now)
                            )
                    result.inserted += inserted
                    result.updated += updated
                    result.skipped += skipped
                    break
                except (IntegrityError, OperationalError):
                    if attempt == 4:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))
        else:
            # Nothing to write, but the batch was still verified.
            if not dry_run:
                async with write_lock():
                    async with session() as s:
                        await s.execute(
                            update(SourceItem)
                            .where(
                                SourceItem.source_id == source.id,
                                SourceItem.source_vod_id.in_(chunk),
                            )
                            .values(last_checked_at=now)
                        )

        if on_progress is not None:
            on_progress(
                {
                    "requested": result.requested,
                    "fetched": result.fetched,
                    "updated": result.updated,
                    "inserted": result.inserted,
                    "gone": result.gone,
                    "batches": result.batches,
                }
            )
        if delay:
            await asyncio.sleep(delay)

    result.elapsed_ms = (time.perf_counter() - started) * 1000
    if not result.unsupported and result.requested and result.fetched == 0:
        result.warnings.append(
            "按 id 查询全部返回空：上游已没有这些记录，或该站点不支持 ids 参数"
        )
    return result


async def refresh_stale(
    days: int | None = None,
    limit: int | None = None,
    names: list[str] | None = None,
    batch: int | None = None,
    concurrency: int | None = None,
    delay: float | None = None,
    dry_run: bool = False,
    only_enabled: bool = True,
    client_factory: Callable[[], Fetcher] | None = None,
    on_progress: Callable[[int, dict[str, Any]], None] | None = None,
) -> list[RefreshResult]:
    """Run the targeted sweep. Returns one RefreshResult per source touched."""
    days = settings.refresh_days if days is None else max(0, days)
    limit = settings.refresh_limit if limit is None else max(0, limit)
    batch = settings.refresh_batch if batch is None else max(1, batch)
    if delay is not None:
        settings.request_delay = delay

    grouped = await pick_refresh_targets(
        days=days, limit=limit, names=names, only_enabled=only_enabled
    )
    if not grouped:
        log.info("定向补扫：没有需要核对的记录")
        return []

    if not acquire_collect_lock("refresh"):
        log.warning("另一轮采集正在进行，定向补扫跳过本轮")
        return []

    try:
        async with session() as s:
            sources = list(
                await s.scalars(select(Source).where(Source.id.in_(grouped.keys())))
            )
        sources.sort(key=lambda src: -len(grouped[src.id]))

        ceiling = settings.concurrency if concurrency is None else concurrency
        sem = asyncio.Semaphore(ceiling) if ceiling and ceiling > 0 else None

        async def run_one(source: Source) -> RefreshResult:
            if sem is not None:
                await sem.acquire()
            try:
                client = client_factory() if client_factory else Fetcher()
                try:
                    return await refresh_source(
                        source,
                        grouped[source.id],
                        client,
                        batch=batch,
                        delay=settings.request_delay,
                        dry_run=dry_run,
                        on_progress=(
                            lambda snap, sid=source.id: on_progress(sid, snap)
                            if on_progress
                            else None
                        ),
                    )
                finally:
                    if hasattr(client, "aclose"):
                        await client.aclose()
            finally:
                if sem is not None:
                    sem.release()

        return await asyncio.gather(*(run_one(src) for src in sources))
    finally:
        release_collect_lock()


refresh_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "updated_at": None,
    "finished_at": None,
    "dry_run": False,
    "sources": 0,
    "checked": 0,
    "fetched": 0,
    "updated": 0,
    "inserted": 0,
    "gone": 0,
    "unsupported_sources": 0,
    "error": None,
}


def refresh_progress() -> dict[str, Any]:
    """Progress of the current (or most recent) targeted sweep."""
    return dict(refresh_state)


async def start_refresh_stale(**kwargs: Any) -> dict[str, Any]:
    """Run the targeted sweep in a background task so the HTTP request returns at once."""
    if refresh_state["running"]:
        return refresh_progress()

    per_source: dict[int, dict[str, Any]] = {}

    def accumulate(source_id: int, snap: dict[str, Any]) -> None:
        per_source[source_id] = snap
        refresh_state["sources"] = len(per_source)
        refresh_state["checked"] = sum(int(s.get("requested", 0)) for s in per_source.values())
        refresh_state["fetched"] = sum(int(s.get("fetched", 0)) for s in per_source.values())
        refresh_state["updated"] = sum(int(s.get("updated", 0)) for s in per_source.values())
        refresh_state["inserted"] = sum(int(s.get("inserted", 0)) for s in per_source.values())
        refresh_state["gone"] = sum(int(s.get("gone", 0)) for s in per_source.values())
        refresh_state["updated_at"] = datetime.now(timezone.utc).isoformat()

    refresh_state.update(
        {
            "running": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "dry_run": bool(kwargs.get("dry_run")),
            "sources": 0,
            "checked": 0,
            "fetched": 0,
            "updated": 0,
            "inserted": 0,
            "gone": 0,
            "unsupported_sources": 0,
            "error": None,
        }
    )

    async def run() -> None:
        try:
            results = await refresh_stale(**kwargs, on_progress=accumulate)
            refresh_state["unsupported_sources"] = sum(1 for r in results if r.unsupported)
        except Exception as exc:  # noqa: BLE001
            log.exception("定向补扫失败")
            refresh_state["error"] = str(exc)
        finally:
            refresh_state["running"] = False
            refresh_state["finished_at"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(run())
    return refresh_progress()


async def refresh_summary() -> dict[str, Any]:
    """How much of the library is waiting for a targeted re-check."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.refresh_days)
    async with session() as s:
        total = (await s.scalar(select(func.count(SourceItem.id)))) or 0
        never = (
            await s.scalar(
                select(func.count(SourceItem.id)).where(SourceItem.last_checked_at.is_(None))
            )
            or 0
        )
        overdue = (
            await s.scalar(
                select(func.count(SourceItem.id)).where(
                    SourceItem.last_checked_at.is_not(None),
                    SourceItem.last_checked_at < cutoff,
                )
            )
            or 0
        )
        empty_plays = (
            await s.scalar(
                select(func.count(SourceItem.id)).where(SourceItem.plays.in_(("", "[]")))
            )
            or 0
        )
        oldest = (
            await s.scalar(
                select(func.min(Video.last_updated)).join(
                    SourceItem, SourceItem.video_id == Video.id
                )
            )
        )
    return {
        "source_items": total,
        "never_checked": never,
        "overdue_days": settings.refresh_days,
        "overdue": overdue,
        "empty_plays": empty_plays,
        "oldest_video_updated_at": oldest.isoformat() if oldest else None,
    }
