from __future__ import annotations

import csv
import io
import json
import secrets
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, not_, select
from sqlalchemy.sql.expression import ColumnElement

from .apikeys import create_api_key, delete_api_key, list_api_keys, update_api_key
from .adult import adult_condition, adult_name_keywords, adult_type_keywords, is_adult
from .auth import (
    TOKEN_COOKIE,
    auth_enabled,
    authenticate,
    check_credentials,
    make_token,
    require_admin,
    require_auth,
)
from .collector import (
    clear_resume,
    collect_all,
    collect_progress,
    collect_source,
    compact_raw_plays,
    merge_duplicates,
    repair_plays,
    resume_summary,
    start_collect_all,
)
from .config import settings, update_env_file
from .db import session
from .health import health_check_all, health_rank
from .images import (
    download_images,
    download_progress,
    image_stats,
    normalize_image_url,
    prune_images,
    repair_images,
    scan_images,
)
from .models import ApiKey, CollectJob, Image, Source, SourceItem, Subscription, Video
from .scheduler import scheduler_status, set_scheduler
from .schemas import (
    AdultFilterUpdate,
    ApiKeyCreate,
    ApiKeyOut,
    ApiKeyUpdate,
    AdminPasswordRequest,
    CollectRequest,
    EnableRequest,
    HealthReport,
    ImageDownloadRequest,
    ImageOut,
    LoginRequest,
    Page,
    SchedulerUpdate,
    SourceCreate,
    SourceImportRequest,
    SourceOut,
    SourceUpdate,
    Stats,
    SubscriptionCreate,
    SubscriptionOut,
    VideoOut,
    VideoSummary,
)
from .seed import parse_sources_text
from .subscribe import detect_kind, ensure_default_subscriptions, sync_all, sync_subscription

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
auth_router = APIRouter(prefix="/api")  # login/status stay reachable without a token
keys_router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])  # only the admin account

MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

UNCATEGORIZED = "未分类"


def _type_condition(type_name: str | None) -> ColumnElement | None:
    """Filter by category.

    Listings label records with an empty `type_name` as 「未分类」, so that label
    has to match NULL/'' rather than a literal string, otherwise clicking the
    chip shows nothing.
    """
    if not type_name:
        return None
    if type_name == UNCATEGORIZED:
        return Video.type_name.is_(None) | (Video.type_name == "")
    return Video.type_name == type_name


# --------------------------------------------------------------------------- auth
@auth_router.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """Whether authentication is enabled and whether this request is authenticated."""
    principal = await authenticate(request)
    return {
        "auth_required": auth_enabled(),
        "authenticated": principal is not None,
        "user": principal.name if principal else None,
        "kind": principal.kind if principal else None,
        "scopes": sorted(principal.scopes) if principal else [],
    }


@auth_router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict[str, Any]:
    """Exchange username/password for a signed token (also set as a browser cookie)."""
    if not auth_enabled():
        return {"token": None, "user": "anonymous", "expires_in": 0, "message": "认证未启用：FEICMS_ADMIN_USER / FEICMS_ADMIN_PASSWORD 为空"}
    if not check_credentials(body.username, body.password):
        raise HTTPException(401, "用户名或密码错误")
    token = make_token(body.username)
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax", max_age=settings.token_ttl)
    return {"token": token, "user": body.username, "expires_in": settings.token_ttl}


@auth_router.post("/logout", response_model=None)
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(TOKEN_COOKIE)
    return {"ok": True}


# -------------------------------------------------------------------------- api keys
@keys_router.get("/apikeys", response_model=list[ApiKeyOut])
async def list_keys(include_disabled: bool = True) -> list[ApiKey]:
    """List API keys. The plaintext key is never stored or returned again."""
    return await list_api_keys(include_disabled=include_disabled)


@keys_router.post("/apikeys", status_code=201)
async def create_key(payload: ApiKeyCreate) -> dict[str, Any]:
    """Create an API key. The plaintext is returned once — store it yourself."""
    try:
        row, plaintext = await create_api_key(
            name=payload.name, scopes=payload.scopes, expires_days=payload.expires_days
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"key": plaintext, "id": row.id, "name": row.name, "scopes": row.scopes}


@keys_router.patch("/apikeys/{key_id}", response_model=ApiKeyOut)
async def update_key(key_id: int, payload: ApiKeyUpdate) -> ApiKey:
    try:
        row = await update_api_key(
            key_id,
            name=payload.name,
            scopes=payload.scopes,
            enabled=payload.enabled,
            expires_days=payload.expires_days,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "api key not found")
    return row


@keys_router.delete("/apikeys/{key_id}", status_code=204)
async def delete_key(key_id: int) -> None:
    if not await delete_api_key(key_id):
        raise HTTPException(404, "api key not found")


# --------------------------------------------------------------------------- sources
@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    enabled: bool | None = None,
    health: str | None = None,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[Source]:
    async with session() as s:
        stmt = select(Source)
        if enabled is not None:
            stmt = stmt.where(Source.enabled == enabled)
        if health:
            stmt = stmt.where(Source.health_status == health)
        if q:
            stmt = stmt.where(Source.name.like(f"%{q}%"))
        return list(
            await s.scalars(
                stmt.order_by(health_rank(), Source.priority.desc(), Source.id).limit(limit).offset(offset)
            )
        )


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(payload: SourceCreate) -> Source:
    async with session() as s:
        if await s.scalar(select(Source).where(Source.name == payload.name)):
            raise HTTPException(409, f"source '{payload.name}' already exists")
        src = Source(**payload.model_dump())
        s.add(src)
        await s.flush()
        return src


@router.post("/sources/import")
async def import_sources(payload: SourceImportRequest) -> dict[str, Any]:
    """Bulk-import a source list pasted into the panel.

    Accepts `name|url|format|uptime` lines, bare URLs, or a YAML/JSON document.
    Existing URLs are refreshed instead of duplicated.
    """
    entries = parse_sources_text(payload.text)
    created = 0
    updated = 0
    skipped: list[str] = []

    async with session() as s:
        for entry in entries:
            if payload.max_pages is not None:
                entry["max_pages"] = payload.max_pages
            if not payload.enabled:
                entry["enabled"] = False

            existing = await s.scalar(select(Source).where(Source.url == entry["url"]))
            if existing is None:
                if await s.scalar(select(Source).where(Source.name == entry["name"])):
                    skipped.append(f"{entry['name']}：名称已存在，跳过")
                    continue
                s.add(Source(**entry))
                created += 1
            else:
                name_taken = await s.scalar(
                    select(Source).where(Source.name == entry["name"], Source.id != existing.id)
                )
                if name_taken is None:
                    existing.name = entry["name"]
                for field_name in ("api_format", "priority", "tags", "uptime"):
                    value = entry.get(field_name)
                    if value not in (None, ""):
                        setattr(existing, field_name, value)
                if payload.max_pages is not None:
                    existing.max_pages = entry["max_pages"]
                updated += 1
        await s.commit()

    return {"parsed": len(entries), "created": created, "updated": updated, "skipped": skipped}


@router.get("/sources/export")
async def export_sources(
    enabled_only: bool = False,
    fmt: str = Query("yaml", pattern="^(yaml|json|csv)$"),
) -> Response:
    """Download the current source list (yaml / json / csv)."""
    async with session() as s:
        stmt = select(Source).order_by(health_rank(), Source.priority.desc(), Source.id)
        if enabled_only:
            stmt = stmt.where(Source.enabled)
        rows = list(await s.scalars(stmt))

    data = [
        {
            "name": r.name,
            "url": r.url,
            "api_format": r.api_format,
            "enabled": r.enabled,
            "priority": r.priority,
            "max_pages": r.max_pages,
            "tags": r.tags,
            "uptime": r.uptime,
        }
        for r in rows
    ]

    if fmt == "json":
        body = json.dumps(data, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["name", "url", "api_format", "enabled", "priority", "max_pages", "tags", "uptime"])
        for row in data:
            writer.writerow([row[key] for key in ("name", "url", "api_format", "enabled", "priority", "max_pages", "tags", "uptime")])
        body = buffer.getvalue()
    else:
        body = yaml.safe_dump({"sources": data}, allow_unicode=True, sort_keys=False)

    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sources.{fmt}"},
    )


@router.get("/sources/{source_id}", response_model=SourceOut)
async def get_source(source_id: int) -> Source:
    async with session() as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "source not found")
        return src


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, payload: SourceUpdate) -> Source:
    async with session() as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "source not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(src, key, value)
        await s.flush()
        return src


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int) -> None:
    async with session() as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "source not found")
        await s.delete(src)


@router.post("/sources/{source_id}/collect")
async def collect_one(
    source_id: int,
    pages: int | None = Query(None, ge=0, le=500, description="0 = 全量遍历该站点所有页"),
) -> dict[str, Any]:
    async with session() as s:
        src = await s.get(Source, source_id)
    if src is None:
        raise HTTPException(404, "source not found")
    result = await collect_source(src, pages=pages)
    return {
        "source_id": result.source_id,
        "source_name": result.source_name,
        "pages": result.pages,
        "pagecount": result.pagecount,
        "total": result.total,
        "inserted": result.inserted,
        "updated": result.updated,
        "skipped": result.skipped,
        "error": result.error,
        "warnings": len(result.warnings),
        "stopped_reason": result.stopped_reason,
        "elapsed_ms": round(result.elapsed_ms, 1),
    }


@router.post("/collect")
async def collect_many(payload: CollectRequest) -> list[dict[str, Any]]:
    if collect_progress()["running"]:
        raise HTTPException(409, "采集正在进行中，请先等待或查看 /api/collect/progress")
    results = await collect_all(
        names=payload.names,
        pages=payload.pages,
        only_enabled=not payload.all,
        source_ids=payload.source_ids,
    )
    return [
        {
            "source_id": r.source_id,
            "source_name": r.source_name,
            "pages": r.pages,
            "pagecount": r.pagecount,
            "total": r.total,
            "inserted": r.inserted,
            "updated": r.updated,
            "skipped": r.skipped,
            "error": r.error,
            "warnings": len(r.warnings),
            "stopped_reason": r.stopped_reason,
            "elapsed_ms": round(r.elapsed_ms, 1),
        }
        for r in results
    ]


@router.post("/collect/start")
async def collect_start(payload: CollectRequest) -> dict[str, Any]:
    """Start a sweep in the background; poll /api/collect/progress for live status.

    A full sweep (`pages=0`) keeps a checkpoint per source, so unless
    `resume=false` is sent it continues an interrupted sweep instead of starting
    from page 1.
    """
    if collect_progress()["running"]:
        raise HTTPException(409, "采集正在进行中，请等待当前一轮完成")
    return await start_collect_all(
        names=payload.names,
        pages=payload.pages,
        only_enabled=not payload.all,
        source_ids=payload.source_ids,
        resume=payload.resume,
    )


@router.get("/collect/resume")
async def collect_resume_status() -> dict[str, Any]:
    """断点续传 checkpoint: what a resumed full sweep would skip."""
    return resume_summary()


@router.delete("/collect/resume")
async def collect_resume_clear() -> dict[str, Any]:
    """Drop the checkpoint so the next full sweep starts from page 1."""
    clear_resume()
    return {"cleared": True}


@router.get("/collect/progress")
async def collect_status() -> dict[str, Any]:
    """Progress of the current (or most recent) collection sweep."""
    return collect_progress()


@router.post("/health-check", response_model=HealthReport)
async def run_health_check(only_enabled: bool = True) -> HealthReport:
    results = await health_check_all(only_enabled=only_enabled)
    return HealthReport(
        checked=len(results),
        ok=sum(1 for r in results if r.ok),
        dead=sum(1 for r in results if not r.ok),
        results=[
            {
                "source_id": r.source_id,
                "name": r.name,
                "ok": r.ok,
                "latency_ms": r.latency_ms,
                "status_code": r.status_code,
                "items": r.items,
                "message": r.message,
            }
            for r in results
        ],
    )


@router.post("/sources/{source_id}/enable")
async def enable_source(source_id: int, payload: EnableRequest) -> SourceOut:
    async with session() as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "source not found")
        src.enabled = payload.enabled
        await s.flush()
        return src


# --------------------------------------------------------------------------- subscriptions
@router.get("/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions() -> list[Subscription]:
    await ensure_default_subscriptions()
    async with session() as s:
        return list(await s.scalars(select(Subscription).order_by(Subscription.id)))


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(payload: SubscriptionCreate) -> Subscription:
    async with session() as s:
        if await s.scalar(select(Subscription).where(Subscription.url == payload.url)):
            raise HTTPException(409, "subscription already exists")
        sub = Subscription(
            url=payload.url,
            kind=payload.kind if payload.kind != "auto" else detect_kind(payload.url),
            enabled=payload.enabled,
        )
        s.add(sub)
        await s.flush()
        return sub


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(subscription_id: int) -> None:
    async with session() as s:
        sub = await s.get(Subscription, subscription_id)
        if sub is None:
            raise HTTPException(404, "subscription not found")
        await s.delete(sub)


@router.post("/subscriptions/sync")
async def sync_subscriptions(subscription_id: int | None = None) -> list[dict[str, Any]]:
    """Pull every enabled subscription feed and upsert the source list."""
    if subscription_id is not None:
        return [(await sync_subscription(subscription_id)).as_dict()]
    return [result.as_dict() for result in await sync_all()]


# --------------------------------------------------------------------------- images
@router.get("/images/stats")
async def images_stats() -> dict[str, Any]:
    return await image_stats()


@router.post("/images/scan")
async def images_scan() -> dict[str, int]:
    """Queue every poster referenced by collected records (no network)."""
    return await scan_images()


@router.post("/images/download")
async def images_download(payload: ImageDownloadRequest) -> dict[str, Any]:
    result = await download_images(
        limit=payload.limit,
        concurrency=payload.concurrency,
        include_failed=payload.retry_failed,
    )
    return {
        "attempted": result.attempted,
        "downloaded": result.downloaded,
        "duplicates": result.duplicates,
        "failed": result.failed,
        "skipped": result.skipped,
        "bytes": result.bytes,
        "elapsed_ms": round(result.elapsed_ms, 1),
    }


@router.get("/images/download/progress")
async def images_download_progress() -> dict[str, Any]:
    """Live counters for the poster batch running now (or the last one that finished)."""
    return download_progress()


@router.get("/images", response_model=list[ImageOut])
async def list_images(
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Image]:
    async with session() as s:
        stmt = select(Image)
        if status:
            stmt = stmt.where(Image.status == status)
        if q:
            stmt = stmt.where(Image.host.like(f"%{q}%"))
        return list(await s.scalars(stmt.order_by(Image.id.desc()).limit(limit).offset(offset)))


@router.get("/images/layout")
async def images_layout(top: int = Query(12, ge=1, le=50)) -> dict[str, Any]:
    """Poster directory structure: bucket count, busiest buckets, total files."""
    root = Path(settings.image_dir)
    if not root.exists():
        return {"root": str(root), "exists": False, "files": 0, "buckets": 0, "busiest": 0, "top": []}

    shards = [d for d in root.glob("*/*") if d.is_dir()]
    counts = {d: sum(1 for f in d.iterdir() if f.is_file()) for d in shards}
    return {
        "root": str(root),
        "exists": True,
        "files": sum(counts.values()),
        "buckets": len(shards),
        "busiest": max(counts.values(), default=0),
        "top": [
            {"path": str(shard.relative_to(root)), "files": count}
            for shard, count in sorted(counts.items(), key=lambda kv: -kv[1])[:top]
        ],
    }


@router.get("/images/{image_id}", response_model=ImageOut)
async def get_image(image_id: int) -> Image:
    async with session() as s:
        image = await s.get(Image, image_id)
    if image is None:
        raise HTTPException(404, "image not found")
    return image


@router.post("/images/prune")
async def images_prune(delete_files: bool = True) -> dict[str, int]:
    return await prune_images(delete_files=delete_files)


@router.post("/images/repair")
async def images_repair() -> dict[str, int]:
    return await repair_images()


# --------------------------------------------------------------------------- videos
async def _local_pic_map(s, pics: list[str]) -> dict[str, int]:
    """Map normalised poster URLs -> local image id, for posters already on disk."""
    normalized = [url for url in (normalize_image_url(pic) for pic in pics if pic) if url]
    if not normalized:
        return {}
    rows = list(
        await s.scalars(
            select(Image).where(Image.url.in_(normalized), Image.status.in_(("downloaded", "duplicate")))
        )
    )
    return {row.url: row.id for row in rows}


def _local_pic(video: Video, map_: dict[str, int]) -> str | None:
    image_id = map_.get(normalize_image_url(video.pic) or "")
    return f"/images/{image_id}" if image_id else None


def _video_out(video: Video, items: list[SourceItem], local_pic: str | None = None) -> VideoOut:
    plays = []
    for item in items:
        try:
            parsed = json.loads(item.plays or "[]")
        except json.JSONDecodeError:
            parsed = []
        for entry in parsed:
            plays.append({"from_name": entry.get("from", ""), "episodes": entry.get("episodes", [])})
    return VideoOut(
        id=video.id,
        name=video.name,
        type_name=video.type_name,
        year=video.year,
        area=video.area,
        lang=video.lang,
        pic=video.pic,
        local_pic=local_pic,
        blurb=video.blurb,
        actor=video.actor,
        director=video.director,
        remarks=video.remarks,
        source_count=video.source_count,
        last_updated=video.last_updated,
        plays=plays,
    )


@router.get("/videos")
async def list_videos(
    q: str | None = None,
    type_name: str | None = None,
    year: str | None = None,
    source_id: int | None = None,
    has_playable: bool = False,
    sort: str = Query("updated", pattern="^(updated|name|source_count)$"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    async with session() as s:
        stmt = select(Video)
        if q:
            stmt = stmt.where(Video.name.like(f"%{q}%"))
        type_condition = _type_condition(type_name)
        if type_condition is not None:
            stmt = stmt.where(type_condition)
        if year:
            stmt = stmt.where(Video.year == year)
        if source_id:
            stmt = stmt.where(
                Video.id.in_(select(SourceItem.video_id).where(SourceItem.source_id == source_id))
            )
        if has_playable:
            stmt = stmt.where(Video.source_count > 0)

        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        order = {
            "updated": Video.last_updated.desc(),
            "name": Video.name.asc(),
            "source_count": Video.source_count.desc(),
        }[sort]
        videos = list(await s.scalars(stmt.order_by(order).limit(limit).offset(offset)))
        pic_map = await _local_pic_map(s, [v.pic for v in videos])

        summaries = [
            VideoSummary(
                id=v.id,
                name=v.name,
                type_name=v.type_name,
                year=v.year,
                pic=v.pic,
                local_pic=_local_pic(v, pic_map),
                remarks=v.remarks,
                source_count=v.source_count,
                last_updated=v.last_updated,
            )
            for v in videos
        ]
    return Page(total=total or 0, page=offset // limit + 1, limit=limit, items=summaries)


@router.get("/videos/{video_id}", response_model=VideoOut)
async def get_video(video_id: int) -> VideoOut:
    async with session() as s:
        video = await s.get(Video, video_id)
        if video is None:
            raise HTTPException(404, "video not found")
        items = list(await s.scalars(select(SourceItem).where(SourceItem.video_id == video_id)))
        pic_map = await _local_pic_map(s, [video.pic])
    return _video_out(video, items, _local_pic(video, pic_map))


@router.get("/videos/{video_id}/sources")
async def get_video_sources(video_id: int) -> list[dict[str, Any]]:
    async with session() as s:
        items = list(
            await s.scalars(
                select(SourceItem).where(SourceItem.video_id == video_id).order_by(SourceItem.source_id)
            )
        )
        source_ids = {i.source_id for i in items}
        sources = {src.id: src for src in await s.scalars(select(Source).where(Source.id.in_(source_ids)))} if source_ids else {}

    out = []
    for item in items:
        src = sources.get(item.source_id)
        out.append(
            {
                "source_id": item.source_id,
                "source_name": src.name if src else "",
                "source_vod_id": item.source_vod_id,
                "play_from": item.play_from,
                "vod_time": item.vod_time,
                "episodes": json.loads(item.plays or "[]"),
            }
        )
    return out


@router.get("/categories")
async def categories() -> list[dict[str, Any]]:
    async with session() as s:
        rows = await s.execute(
            select(Video.type_name, func.count(Video.id)).group_by(Video.type_name).order_by(func.count(Video.id).desc())
        )
    return [{"type_name": name or "", "count": count} for name, count in rows]


@router.get("/stats", response_model=Stats)
async def stats() -> Stats:
    async with session() as s:
        sources = list(await s.scalars(select(Source)))
        videos = await s.scalar(select(func.count()).select_from(Video))
        items = await s.scalar(select(func.count()).select_from(SourceItem))
        last = await s.scalar(select(func.max(Source.last_collect_at)))
        by_source = [
            {"name": src.name, "enabled": src.enabled, "health": src.health_status, "count": src.item_count}
            for src in sources
        ]
        type_rows = await s.execute(
            select(Video.type_name, func.count(Video.id)).group_by(Video.type_name).order_by(func.count(Video.id).desc()).limit(30)
        )
        by_type = [{"type_name": name or "", "count": count} for name, count in type_rows]

    return Stats(
        sources=len(sources),
        enabled_sources=sum(1 for src in sources if src.enabled),
        healthy_sources=sum(1 for src in sources if src.health_status == "ok"),
        videos=videos or 0,
        source_items=items or 0,
        last_collect_at=last,
        by_source=by_source,
        by_type=by_type,
    )


@router.get("/mcp/tools")
async def mcp_tool_catalogue() -> list[dict[str, Any]]:
    """MCP tool catalogue, including the scope each tool requires (for the UI)."""
    from .mcp import TOOLS

    return TOOLS


@router.get("/config")
async def config() -> dict[str, Any]:
    """Effective configuration, all of it driven by .env / environment variables."""
    return {
        "database_url": settings.database_url,
        "sources_file": settings.sources_file,
        "subscriptions": settings.subscriptions,
        "request_timeout": settings.request_timeout,
        "user_agent": settings.user_agent,
        "concurrency": settings.concurrency,
        "max_retries": settings.max_retries,
        "default_pages": settings.default_pages,
        "page_limit": settings.page_limit,
        "full_page_cap": settings.full_page_cap,
        "auto_collect": settings.auto_collect,
        "auto_collect_full": settings.auto_collect_full,
        "collect_interval_minutes": settings.collect_interval_minutes,
        "health_interval_minutes": settings.health_interval_minutes,
        "subscribe_interval_minutes": settings.subscribe_interval_minutes,
        "health_fail_threshold": settings.health_fail_threshold,
        "image_dir": settings.image_dir,
        "image_batch": settings.image_batch,
        "image_concurrency": settings.image_concurrency,
        "image_delay": settings.image_delay,
        "image_max_size": settings.image_max_size,
        "image_auto": settings.image_auto,
        "image_interval_minutes": settings.image_interval_minutes,
        "host": settings.host,
        "port": settings.port,
        "auth_required": auth_enabled(),
        "admin_user": settings.admin_user,
        "public_site": settings.public_site,
        "hide_adult": settings.hide_adult,
        "adult_type_keywords": list(adult_type_keywords()),
        "adult_name_keywords": list(adult_name_keywords()),
    }


@router.post("/adult-filter")
async def update_adult_filter(body: AdultFilterUpdate) -> dict[str, Any]:
    """Turn the adult filter on/off and edit the keyword lists.

    Changes are applied to the running process and written to .env so they
    survive a restart. Only the public front site is filtered; the admin
    library always shows everything.
    """
    if body.hide_adult is not None:
        settings.hide_adult = body.hide_adult
    if body.type_keywords is not None:
        settings.adult_type_keywords = tuple(k.strip() for k in body.type_keywords if k.strip())
    if body.name_keywords is not None:
        settings.adult_name_keywords = tuple(k.strip() for k in body.name_keywords if k.strip())

    values = {"FEICMS_HIDE_ADULT": "true" if settings.hide_adult else "false"}
    if body.type_keywords is not None:
        values["FEICMS_ADULT_TYPE_KEYWORDS"] = ",".join(settings.adult_type_keywords or ())
    if body.name_keywords is not None:
        values["FEICMS_ADULT_NAME_KEYWORDS"] = ",".join(settings.adult_name_keywords or ())
    path = update_env_file(values)

    return {
        "hide_adult": settings.hide_adult,
        "type_keywords": list(adult_type_keywords()),
        "name_keywords": list(adult_name_keywords()),
        "env_file": str(path),
    }


@router.post("/prune")
async def prune() -> dict[str, int]:
    """Maintenance: drop source records with no playable episodes, then orphan videos."""
    async with session() as s:
        rows = list(await s.scalars(select(SourceItem).where(SourceItem.plays == "[]")))
        for row in rows:
            await s.delete(row)
        orphans = list(
            await s.scalars(select(Video).where(Video.id.not_in(select(SourceItem.video_id))))
        )
        for video in orphans:
            await s.delete(video)
    return {"removed": len(rows), "orphan_videos": len(orphans)}


@router.post("/compact-raw")
async def compact_raw() -> dict[str, int]:
    """Maintenance: drop raw play_url strings already parsed into structured plays, then VACUUM."""
    return await compact_raw_plays()


@router.post("/repair-plays")
async def repair_plays_endpoint() -> dict[str, int]:
    """Re-parse stored `play_from` / `play_url` into structured play sources."""
    return await repair_plays()


@router.post("/merge-duplicates")
async def merge_duplicates_endpoint(dry_run: bool = False) -> dict[str, Any]:
    """Merge videos that share the same normalised title.

    `?dry_run=true` only reports how many groups would be merged.
    """
    return await merge_duplicates(dry_run=dry_run)


@router.get("/export")
async def export_videos(
    limit: int = Query(10000, ge=1, le=100000),
    q: str = "",
    fmt: str = Query("jsonl", pattern="^(jsonl|json)$"),
) -> Response:
    """Download the merged library as JSONL/JSON for backup or migration."""
    async with session() as s:
        stmt = select(Video).order_by(Video.last_updated.desc()).limit(limit)
        if q:
            stmt = stmt.where(Video.name.like(f"%{q}%"))
        videos = list(await s.scalars(stmt))
        by_video: dict[int, list[SourceItem]] = {}
        if videos:
            for item in await s.scalars(
                select(SourceItem).where(SourceItem.video_id.in_([v.id for v in videos]))
            ):
                by_video.setdefault(item.video_id, []).append(item)

    records = [
        {
            "id": v.id,
            "name": v.name,
            "type": v.type_name,
            "year": v.year,
            "area": v.area,
            "blurb": v.blurb,
            "pic": v.pic,
            "source_count": v.source_count,
            "sources": [
                {
                    "source_id": i.source_id,
                    "vod_id": i.source_vod_id,
                    "plays": json.loads(i.plays or "[]"),
                }
                for i in by_video.get(v.id, [])
            ],
        }
        for v in videos
    ]

    if fmt == "json":
        body = json.dumps(records, ensure_ascii=False, indent=2)
    else:
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"

    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=feicms-export.{fmt}"},
    )


@router.get("/scheduler")
async def get_scheduler() -> dict[str, Any]:
    """State of the background loops (auto collect / health / posters)."""
    return scheduler_status()


@router.post("/scheduler")
async def update_scheduler(payload: SchedulerUpdate) -> dict[str, Any]:
    """Turn the background loops on/off or change their intervals at runtime."""
    return set_scheduler(**payload.model_dump(exclude_unset=True))


@keys_router.post("/admin/password")
async def set_admin_password(payload: AdminPasswordRequest) -> dict[str, Any]:
    """Write the admin account into .env. The new password is shown once."""
    user = (payload.username or settings.admin_user or "admin").strip()
    password = payload.password or secrets.token_urlsafe(12)
    path = update_env_file({"FEICMS_ADMIN_USER": user, "FEICMS_ADMIN_PASSWORD": password})
    return {
        "user": user,
        "password": password,
        "env_file": str(path),
        "restart_required": settings.admin_user != user or settings.admin_password != password,
    }


@keys_router.post("/admin/token")
async def create_admin_token(ttl: int = Query(3600, ge=60, le=86400 * 30)) -> dict[str, Any]:
    """Generate a signed token for curl / scripts (admin account only)."""
    if not auth_enabled():
        raise HTTPException(400, "认证未启用：FEICMS_ADMIN_USER / FEICMS_ADMIN_PASSWORD 为空")
    user = (settings.admin_user or "admin").strip()
    return {"token": make_token(user, ttl), "user": user, "expires_in": ttl}


@router.get("/jobs")
async def jobs(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    async with session() as s:
        rows = list(await s.scalars(select(CollectJob).order_by(CollectJob.id.desc()).limit(limit)))
    return [
        {
            "id": j.id,
            "source_id": j.source_id,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "pages": j.pages,
            "inserted": j.inserted,
            "updated": j.updated,
            "skipped": j.skipped,
            "error": j.error,
        }
        for j in rows
    ]


# ------------------------------------------------------- mackin-compatible self API
# ------------------------------------------------------- mackin-compatible self API
provide_router = APIRouter(dependencies=[Depends(require_auth)] if not settings.public_provide else [])


@provide_router.get("/provide/vod")
async def provide_vod(
    ac: str = "list",
    q: str | None = None,
    t: str | None = None,
    pg: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Serve the local database using the same shape as upstream resource sites."""
    async with session() as s:
        stmt = select(Video)
        if q:
            stmt = stmt.where(Video.name.like(f"%{q}%"))
        if t:
            stmt = stmt.where(Video.type_name == t)
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        videos = list(
            await s.scalars(stmt.order_by(Video.last_updated.desc()).limit(limit).offset((pg - 1) * limit))
        )
        ids = [v.id for v in videos]
        items = {}
        if ids:
            for item in await s.scalars(select(SourceItem).where(SourceItem.video_id.in_(ids))):
                items.setdefault(item.video_id, []).append(item)

        pagecount = max(1, (total + limit - 1) // limit) if total else 0
        out = []
        for v in videos:
            froms: list[str] = []
            urls: list[str] = []
            for item in items.get(v.id, []):
                for entry in json.loads(item.plays or "[]"):
                    froms.append(entry.get("from") or item.play_from or "local")
                    urls.append("#".join(
                        f"{e['name']}${e['url']}" for e in entry.get("episodes", [])
                    ))
            out.append(
                {
                    "vod_id": v.id,
                    "vod_name": v.name,
                    "type_id": v.type_id,
                    "type_name": v.type_name,
                    "vod_time": v.last_updated.strftime("%Y-%m-%d %H:%M:%S"),
                    "vod_remarks": v.remarks,
                    "vod_pic": v.pic,
                    "vod_play_from": ",".join(froms),
                    "vod_play_url": "#".join(urls),
                }
            )
    return {"code": 1, "msg": "数据列表", "page": pg, "pagecount": pagecount, "limit": limit, "total": total, "list": out}


# ---------------------------------------------------------------- public front site
# Read-only endpoints for the browsing front end served at `/`. Nothing here can
# reach sources, collection, images queue or configuration - it only exposes the
# library. Turn it off with FEICMS_PUBLIC_SITE=false to require a login too.
site_router = APIRouter(
    prefix="/api/site",
    dependencies=[Depends(require_auth)] if not settings.public_site else [],
)


def _site_pic(video: Video, pic_map: dict[str, int]) -> str | None:
    image_id = pic_map.get(normalize_image_url(video.pic) or "")
    return f"/api/site/pic/{image_id}" if image_id else None


def _effective_hide_adult(hide_adult: bool | None) -> bool:
    return settings.hide_adult if hide_adult is None else hide_adult


def _hide_adult(hide_adult: bool | None) -> ColumnElement | None:
    """Condition that keeps adult records out of a query, or None when off.

    `hide_adult` is the explicit query parameter; when it is absent the server
    default (FEICMS_HIDE_ADULT, changeable from the panel) applies.
    """
    if not _effective_hide_adult(hide_adult):
        return None
    condition = adult_condition()
    return not_(condition) if condition is not None else None


def _summary(video: Video, pic_map: dict[str, int]) -> VideoSummary:
    return VideoSummary(
        id=video.id,
        name=video.name,
        type_name=video.type_name,
        year=video.year,
        pic=video.pic,
        local_pic=_site_pic(video, pic_map),
        remarks=video.remarks,
        source_count=video.source_count,
        last_updated=video.last_updated,
    )


@site_router.get("/stats")
async def site_stats() -> dict[str, Any]:
    async with session() as s:
        videos = await s.scalar(select(func.count(Video.id)))
        sources = await s.scalar(select(func.count(Source.id)).where(Source.enabled))
        posters = await s.scalar(
            select(func.count(Image.id)).where(Image.status.in_(("downloaded", "duplicate")))
        )
        latest = await s.scalar(select(func.max(Video.last_updated)))
        condition = adult_condition()
        adult = await s.scalar(select(func.count(Video.id)).where(condition)) if condition is not None else 0
    return {
        "videos": videos or 0,
        "sources": sources or 0,
        "posters": posters or 0,
        "last_updated": latest.isoformat() if latest else None,
        "adult_videos": adult or 0,
        "hide_adult": settings.hide_adult,
    }


@site_router.get("/filters")
async def site_filters(hide_adult: bool | None = Query(None)) -> dict[str, Any]:
    """Categories, years and regions present in the library, with counts."""
    async with session() as s:
        stmt = select(Video.type_name, func.count(Video.id))
        filter_ = _hide_adult(hide_adult)
        if filter_ is not None:
            stmt = stmt.where(filter_)
        types = [
            {"type_name": name or "未分类", "count": count}
            for name, count in await s.execute(
                stmt.group_by(Video.type_name).order_by(func.count(Video.id).desc())
            )
        ]
        year_stmt = select(Video.year).where(Video.year != "")
        area_stmt = select(Video.area).where(Video.area != "")
        if filter_ is not None:
            year_stmt = year_stmt.where(filter_)
            area_stmt = area_stmt.where(filter_)
        years = [
            year
            for year in await s.scalars(year_stmt.group_by(Video.year).order_by(Video.year.desc()))
        ]
        areas = [
            area
            for area in await s.scalars(area_stmt.group_by(Video.area).order_by(Video.area))
        ]
    return {"types": types, "years": years, "areas": areas, "hide_adult": _effective_hide_adult(hide_adult)}


@site_router.get("/videos")
async def site_videos(
    q: str | None = None,
    type_name: str | None = None,
    year: str | None = None,
    sort: str = Query("updated", pattern="^(updated|name|source_count)$"),
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    hide_adult: bool | None = Query(None),
) -> Page:
    async with session() as s:
        stmt = select(Video)
        if q:
            stmt = stmt.where(Video.name.like(f"%{q}%"))
        type_condition = _type_condition(type_name)
        if type_condition is not None:
            stmt = stmt.where(type_condition)
        if year:
            stmt = stmt.where(Video.year == year)
        filter_ = _hide_adult(hide_adult)
        if filter_ is not None:
            stmt = stmt.where(filter_)

        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        order = {
            "updated": Video.last_updated.desc(),
            "name": Video.name,
            "source_count": Video.source_count.desc(),
        }
        videos = list(await s.scalars(stmt.order_by(order[sort]).limit(limit).offset(offset)))
        pic_map = await _local_pic_map(s, [v.pic for v in videos])
        return Page(
            total=total,
            page=offset // limit + 1,
            limit=limit,
            items=[_summary(v, pic_map) for v in videos],
        )


@site_router.get("/videos/{video_id}", response_model=VideoOut)
async def site_video(video_id: int, hide_adult: bool | None = Query(None)) -> VideoOut:
    async with session() as s:
        video = await s.get(Video, video_id)
        if video is None or (_effective_hide_adult(hide_adult) and is_adult(video)):
            raise HTTPException(404, "video not found")
        items = list(await s.scalars(select(SourceItem).where(SourceItem.video_id == video_id)))
        pic_map = await _local_pic_map(s, [video.pic])
        return _video_out(video, items, _site_pic(video, pic_map))


@site_router.get("/videos/{video_id}/related")
async def site_related(
    video_id: int,
    limit: int = Query(12, ge=1, le=40),
    hide_adult: bool | None = Query(None),
) -> list[VideoSummary]:
    """Same category, most recently updated, excluding the video itself."""
    async with session() as s:
        video = await s.get(Video, video_id)
        if video is None:
            raise HTTPException(404, "video not found")
        stmt = select(Video).where(Video.id != video_id)
        type_condition = _type_condition(video.type_name)
        if type_condition is not None:
            stmt = stmt.where(type_condition)
        filter_ = _hide_adult(hide_adult)
        if filter_ is not None:
            stmt = stmt.where(filter_)
        videos = list(
            await s.scalars(
                stmt.order_by(Video.source_count.desc(), Video.last_updated.desc()).limit(limit)
            )
        )
        pic_map = await _local_pic_map(s, [v.pic for v in videos])
        return [_summary(v, pic_map) for v in videos]


@site_router.get("/pic/{image_id}", response_model=None)
async def site_pic(image_id: int) -> FileResponse | RedirectResponse:
    """Public poster endpoint. Falls back to the original remote URL when the
    poster has not been downloaded yet."""
    async with session() as s:
        image = await s.get(Image, image_id)
    if image is None:
        raise HTTPException(404, "image not found")

    path = Path(settings.image_dir) / image.path if image.path else None
    if path and path.exists():
        return FileResponse(
            str(path),
            media_type=MEDIA_TYPES.get(image.ext, "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return RedirectResponse(image.url)
