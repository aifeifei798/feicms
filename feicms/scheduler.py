from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from .config import settings
from .collector import collect_progress, start_collect_all
from .db import analyze, session
from .health import health_check_all
from .images import download_images, scan_images
from .models import Source
from .refresh import refresh_stale
from .subscribe import ensure_default_subscriptions, sync_all

log = logging.getLogger("feicms.scheduler")

# Runtime view of the background loops so the admin UI can show (and change)
# what is running without restarting the service.
scheduler_state: dict[str, object] = {
    "auto_collect": settings.auto_collect,
    "auto_collect_full": settings.auto_collect_full,
    "auto_images": settings.image_auto,
    "collect_interval_minutes": settings.collect_interval_minutes,
    "health_interval_minutes": settings.health_interval_minutes,
    "image_interval_minutes": settings.image_interval_minutes,
    "auto_refresh": settings.auto_refresh,
    "refresh_interval_minutes": settings.refresh_interval_minutes,
    "refresh_days": settings.refresh_days,
    "refresh_limit": settings.refresh_limit,
    "last_collect_at": None,
    "last_health_at": None,
    "last_image_at": None,
    "last_refresh_at": None,
    "last_collect_result": None,
    "last_refresh_result": None,
}


def scheduler_status() -> dict[str, object]:
    progress = collect_progress()
    return {
        **scheduler_state,
        "collect_running": progress["running"],
        "collect_progress": progress,
    }


def set_scheduler(
    auto_collect: bool | None = None,
    auto_collect_full: bool | None = None,
    auto_images: bool | None = None,
    collect_interval_minutes: int | None = None,
    health_interval_minutes: int | None = None,
    image_interval_minutes: int | None = None,
    auto_refresh: bool | None = None,
    refresh_interval_minutes: int | None = None,
) -> dict[str, object]:
    """Toggle the background loops at runtime (intervals take effect next cycle)."""
    if auto_refresh is not None:
        scheduler_state["auto_refresh"] = bool(auto_refresh)
        settings.auto_refresh = bool(auto_refresh)
    if refresh_interval_minutes is not None:
        minutes = max(1, int(refresh_interval_minutes))
        scheduler_state["refresh_interval_minutes"] = minutes
        settings.refresh_interval_minutes = minutes
    if auto_collect is not None:
        scheduler_state["auto_collect"] = bool(auto_collect)
        settings.auto_collect = bool(auto_collect)
    if auto_collect_full is not None:
        scheduler_state["auto_collect_full"] = bool(auto_collect_full)
        settings.auto_collect_full = bool(auto_collect_full)
    if auto_images is not None:
        scheduler_state["auto_images"] = bool(auto_images)
        settings.image_auto = bool(auto_images)
    if collect_interval_minutes is not None:
        minutes = max(1, int(collect_interval_minutes))
        scheduler_state["collect_interval_minutes"] = minutes
        settings.collect_interval_minutes = minutes
    if health_interval_minutes is not None:
        minutes = max(1, int(health_interval_minutes))
        scheduler_state["health_interval_minutes"] = minutes
        settings.health_interval_minutes = minutes
    if image_interval_minutes is not None:
        minutes = max(1, int(image_interval_minutes))
        scheduler_state["image_interval_minutes"] = minutes
        settings.image_interval_minutes = minutes
    return scheduler_status()


async def auto_collect_loop() -> None:
    """Background maintenance loop: periodic health checks + incremental collection."""
    while True:
        if scheduler_state["auto_collect"]:
            try:
                # A full sweep walks every page each source reports; the default
                # is the incremental FEICMS_PAGES pages per source.
                pages = 0 if scheduler_state.get("auto_collect_full") else None
                await start_collect_all(only_enabled=True, pages=pages)
                while collect_progress()["running"]:
                    await asyncio.sleep(5)
                state = collect_progress()
                scheduler_state["last_collect_at"] = datetime.now(timezone.utc).isoformat()
                scheduler_state["last_collect_result"] = {
                    "sources": state["total_sources"],
                    "inserted": state["inserted"],
                    "updated": state["updated"],
                    "failed": state["failed_sources"],
                }
                log.info(
                    "auto collect finished: %d sources, +%d new, %d updated, %d failed",
                    state["total_sources"],
                    state["inserted"],
                    state["updated"],
                    state["failed_sources"],
                )
                if state["inserted"] or state["updated"]:
                    # Planner statistics go stale after a big insert; ANALYZE keeps
                    # the front-site LIMIT queries on the composite indexes.
                    await analyze()
            except Exception:  # noqa: BLE001
                log.exception("auto collect failed")

        await asyncio.sleep(max(1, int(scheduler_state["collect_interval_minutes"])) * 60)


async def auto_refresh_loop() -> None:
    """Targeted re-check of records a normal sweep never reaches.

    A sweep only walks the first FEICMS_PAGES pages of every source, so an old
    title whose play sources changed upstream is never re-read. This loop asks each
    source for those records by vod_id instead of walking hundreds of pages.
    """
    while True:
        if scheduler_state.get("auto_refresh"):
            try:
                results = await refresh_stale()
                if results:
                    scheduler_state["last_refresh_at"] = datetime.now(timezone.utc).isoformat()
                    scheduler_state["last_refresh_result"] = {
                        "sources": len(results),
                        "checked": sum(r.requested for r in results),
                        "updated": sum(r.updated for r in results),
                        "inserted": sum(r.inserted for r in results),
                        "gone": sum(r.gone for r in results),
                    }
                    log.info(
                        "targeted refresh finished: %d sources, %d records checked, "
                        "%d play sources refreshed",
                        len(results),
                        scheduler_state["last_refresh_result"]["checked"],
                        scheduler_state["last_refresh_result"]["updated"],
                    )
                    if scheduler_state["last_refresh_result"]["updated"]:
                        await analyze()
                else:
                    log.info("targeted refresh: nothing to check")
            except Exception:  # noqa: BLE001
                log.exception("targeted refresh failed")
        await asyncio.sleep(max(1, int(scheduler_state.get("refresh_interval_minutes", 720))) * 60)


async def auto_subscribe_loop() -> None:
    """Pull the registered source-list feeds and keep the source table current."""
    while True:
        if scheduler_state["auto_collect"]:
            try:
                results = await sync_all()
                added = sum(r.added for r in results)
                updated = sum(r.updated for r in results)
                log.info(
                    "subscription sync finished: %d feeds, +%d new sources, %d refreshed",
                    len(results),
                    added,
                    updated,
                )
            except Exception:  # noqa: BLE001
                log.exception("subscription sync failed")
        await asyncio.sleep(max(1, int(scheduler_state["collect_interval_minutes"])) * 60)


async def auto_health_loop() -> None:
    """Periodically probe sources and auto-disable permanently broken ones."""
    while True:
        if scheduler_state["auto_collect"]:
            try:
                await health_check_all(only_enabled=False, reenable=False, disable_on_fail=False, timeout=8.0, retries=1)
                async with session() as s:
                    await s.execute(
                        update(Source).where(Source.fail_streak >= settings.health_fail_threshold * 2).values(enabled=False)
                    )
                scheduler_state["last_health_at"] = datetime.now(timezone.utc).isoformat()
                log.info("health sweep finished at %s", scheduler_state["last_health_at"])
            except Exception:  # noqa: BLE001
                log.exception("health sweep failed")
        await asyncio.sleep(max(1, int(scheduler_state["health_interval_minutes"])) * 60)


async def auto_image_loop() -> None:
    """Queue posters seen by collection and download a batch every cycle.

    Kept separate from collection so crawling stays fast; posters are fetched in
    bounded batches so the image directory grows steadily instead of in one burst.
    """
    while True:
        if scheduler_state["auto_images"]:
            try:
                queued = await scan_images()
                result = await download_images(limit=settings.image_batch)
                if result.attempted:
                    log.info(
                        "image batch: %d downloaded, %d duplicate, %d failed, %d skipped "
                        "(%.1f MB, %.1fs) - queued %d new posters",
                        result.downloaded,
                        result.duplicates,
                        result.failed,
                        result.skipped,
                        result.bytes / 1024 / 1024,
                        result.elapsed_ms / 1000,
                        queued["queued"],
                    )
                scheduler_state["last_image_at"] = datetime.now(timezone.utc).isoformat()
            except Exception:  # noqa: BLE001
                log.exception("image download batch failed")
        await asyncio.sleep(max(1, int(scheduler_state["image_interval_minutes"])) * 60)
