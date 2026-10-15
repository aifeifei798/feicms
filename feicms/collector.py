from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .fetcher import Fetcher, build_query, fetch
from .models import CollectJob, Source, SourceItem, SourceType, Video
from .normalizer import VodItem, clean_text, parse_raw_items, parse_response
from .db import session


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
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def get_source(session_, source_id: int | None = None, name: str | None = None) -> Source | None:
    if source_id is not None:
        return await session_.get(Source, source_id)
    if name is not None:
        return await session_.scalar(select(Source).where(Source.name == name))
    return None


async def upsert_item(session_: AsyncSession, source: Source, item: VodItem) -> str:
    """Merge one fetched record into the database. Returns inserted|updated|skipped."""
    stmt = select(SourceItem).where(SourceItem.source_id == source.id, SourceItem.source_vod_id == item.vod_id)
    existing = await session_.scalar(stmt)

    video = await session_.scalar(select(Video).where(Video.signature == item.signature))
    if video is None:
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
        await session_.flush()

    plays_json = json.dumps([p.as_dict() for p in item.plays], ensure_ascii=False)

    if existing is None:
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
                play_url=item.play_url,
                plays=plays_json,
            )
        )
        video.source_count += 1
        video.last_updated = datetime.now(timezone.utc)
        return "inserted"

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
        ("play_url", item.play_url),
        ("plays", plays_json),
    ):
        if value and getattr(existing, field_name) != value:
            setattr(existing, field_name, value)
            changed = True

    if changed:
        video.last_updated = datetime.now(timezone.utc)
        return "updated"
    return "skipped"


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
) -> CollectResult:
    own_client = client is None
    client = client or Fetcher()
    result = CollectResult(source_id=source.id, source_name=source.name)

    # pages semantics:
    #   None  -> use the per-source max_pages (incremental, stops early)
    #   > 0   -> exactly that many pages
    #   <= 0  -> full sweep: walk every page the source reports, up to FEICMS_FULL_PAGE_CAP
    full = pages is not None and pages <= 0
    max_pages = settings.full_page_cap if full else (pages if pages is not None else source.max_pages)
    if full:
        stop_on_no_change = False
    limit = limit or settings.page_limit
    started = time.perf_counter()

    try:
        type_map = await load_type_map(client, source)
        if type_map:
            async with session() as s:
                for tid, tname in type_map.items():
                    existing = await s.scalar(
                        select(SourceType).where(SourceType.source_id == source.id, SourceType.type_id == tid)
                    )
                    if existing:
                        existing.type_name = tname
                    else:
                        s.add(SourceType(source_id=source.id, type_id=tid, type_name=tname))

        for page in range(1, max_pages + 1):
            url = build_query(source.url, ac, page, limit)
            response = await fetch(client, url)
            items, pagecount, total = parse_response(response.text, source.api_format)

            # Some sites do not implement ac=detail; fall back to the list endpoint.
            if not items and ac == "detail":
                response = await fetch(client, build_query(source.url, "list", page, limit))
                items, pagecount, total = parse_response(response.text, source.api_format)

            result.pagecount = pagecount or result.pagecount
            result.total = total or result.total

            if not items:
                result.error = f"page {page} returned no items (HTTP {response.status_code})"
                break

            status = response.status_code
            latency = response.latency_ms

            page_changes = 0
            async with session() as s:
                for item in items:
                    if not item.type_name and item.type_id:
                        item.type_name = type_map.get(item.type_id, "")
                    outcome = await upsert_item(s, source, item)
                    if outcome == "inserted":
                        result.inserted += 1
                        page_changes += 1
                    elif outcome == "updated":
                        result.updated += 1
                        page_changes += 1
                    else:
                        result.skipped += 1

                src = await s.get(Source, source.id)
                if src is not None:
                    src.last_collect_at = datetime.now(timezone.utc)
                    src.last_page = page
                    src.latency_ms = int(latency)
                    src.health_status = "ok"
                    src.fail_streak = 0
                    src.last_error = None
                    src.item_count = await s.scalar(
                        select(func.count()).select_from(SourceItem).where(SourceItem.source_id == source.id)
                    )

            result.pages = page
            if stop_on_no_change and page_changes == 0 and page > 1:
                break
            if pagecount and page >= pagecount:
                break
            if settings.request_delay:
                await asyncio.sleep(settings.request_delay)
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
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


async def collect_all(
    names: list[str] | None = None,
    pages: int | None = None,
    only_enabled: bool = True,
    source_ids: list[int] | None = None,
) -> list[CollectResult]:
    async with session() as s:
        stmt = select(Source)
        if only_enabled:
            stmt = stmt.where(Source.enabled)
        if names:
            stmt = stmt.where(Source.name.in_(names))
        if source_ids:
            stmt = stmt.where(Source.id.in_(source_ids))
        sources = list(await s.scalars(stmt.order_by(Source.priority.desc(), Source.id)))

    results: list[CollectResult] = []
    sem = asyncio.Semaphore(settings.concurrency)

    async def run(src: Source) -> CollectResult:
        async with sem:
            return await collect_source(src, pages=pages)

    results = await asyncio.gather(*(run(src) for src in sources))
    return results
