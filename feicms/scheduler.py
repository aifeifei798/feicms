from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from .config import settings
from .collector import collect_all
from .db import session
from .health import health_check_all
from .images import download_images, scan_images
from .models import Source
from .subscribe import ensure_default_subscriptions, sync_all

log = logging.getLogger("feicms.scheduler")


async def auto_collect_loop() -> None:
    """Background maintenance loop: periodic health checks + incremental collection."""
    while True:
        try:
            results = await collect_all(only_enabled=True)
            inserted = sum(r.inserted for r in results)
            updated = sum(r.updated for r in results)
            log.info(
                "auto collect finished: %d sources, +%d new, %d updated in %.1fs",
                len(results),
                inserted,
                updated,
                sum(r.elapsed_ms for r in results) / 1000,
            )
        except Exception:  # noqa: BLE001
            log.exception("auto collect failed")

        await asyncio.sleep(settings.collect_interval_minutes * 60)


async def auto_subscribe_loop() -> None:
    """Pull the registered source-list feeds and keep the source table current."""
    while True:
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
        await asyncio.sleep(settings.subscribe_interval_minutes * 60)


async def auto_health_loop() -> None:
    """Periodically probe sources and auto-disable permanently broken ones."""
    while True:
        try:
            await health_check_all(only_enabled=True)
            async with session() as s:
                await s.execute(
                    update(Source).where(Source.fail_streak >= settings.health_fail_threshold * 2).values(enabled=False)
                )
            log.info("health sweep finished at %s", datetime.now(timezone.utc).isoformat())
        except Exception:  # noqa: BLE001
            log.exception("health sweep failed")
        await asyncio.sleep(settings.health_interval_minutes * 60)


async def auto_image_loop() -> None:
    """Queue posters seen by collection and download a batch every cycle.

    Kept separate from collection so crawling stays fast; posters are fetched in
    bounded batches so the image directory grows steadily instead of in one burst.
    """
    while True:
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
        except Exception:  # noqa: BLE001
            log.exception("image download batch failed")
        await asyncio.sleep(settings.image_interval_minutes * 60)
