from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .fetcher import Fetcher, build_query, fetch
from .models import Source
from .normalizer import parse_response
from .db import session


@dataclass
class HealthResult:
    source_id: int
    name: str
    ok: bool
    latency_ms: int
    status_code: int
    items: int
    message: str = ""


async def check_source(source: Source, client: Fetcher | None = None) -> HealthResult:
    own = client is None
    client = client or Fetcher()
    url = build_query(source.url, "list", 1, 1)
    result = HealthResult(source_id=source.id, name=source.name, ok=False, latency_ms=0, status_code=0, items=0)
    try:
        response = await fetch(client, url)
        items, pagecount, total = parse_response(response.text, source.api_format)
        result.ok = bool(items)
        result.status_code = response.status_code
        result.latency_ms = int(response.latency_ms)
        result.items = len(items)
        if not items:
            result.message = "no items returned"
    except Exception as exc:  # noqa: BLE001
        result.message = str(exc)
    finally:
        if own:
            await client.aclose()

    async with session() as s:
        src = await s.get(Source, source.id)
        if src is not None:
            src.last_health_at = datetime.now(timezone.utc)
            src.latency_ms = result.latency_ms
            if result.ok:
                src.health_status = "slow" if result.latency_ms > 5000 else "ok"
                src.fail_streak = 0
                src.last_error = None
            else:
                src.fail_streak = (src.fail_streak or 0) + 1
                src.last_error = result.message
                src.health_status = "dead" if src.fail_streak >= settings.health_fail_threshold else "slow"
    return result


async def health_check_all(only_enabled: bool = True) -> list[HealthResult]:
    import asyncio

    async with session() as s:
        stmt = select(Source)
        if only_enabled:
            stmt = stmt.where(Source.enabled)
        sources = list(await s.scalars(stmt))

    client = Fetcher()
    try:
        return await asyncio.gather(*(check_source(src, client) for src in sources))
    finally:
        await client.aclose()
