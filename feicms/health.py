from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, select, update

from .config import settings
from .fetcher import Fetcher, build_query, fetch
from .models import Source
from .normalizer import parse_response
from .db import session, write_lock


@dataclass
class HealthResult:
    source_id: int
    name: str
    ok: bool
    latency_ms: int
    status_code: int
    items: int
    message: str = ""


def health_rank() -> Any:
    """SQL sort key used by the source list: broken/disabled first, healthy last.

    手动跑完健康检查后被禁用的源会立刻浮到列表顶部，避免「禁用的站消失在底部，
    用户以为它们没显示」——坏站应该最先看到，便于启用/选中； ties broken by priority.
    """
    return case(
        (Source.enabled.is_(False), 0),
        (Source.health_status == "dead", 1),
        (Source.health_status == "unknown", 2),
        (Source.health_status == "slow", 3),
        (Source.health_status == "ok", 4),
        else_=2,
    )


async def check_source(
    source: Source,
    client: Fetcher | None = None,
    disable_on_fail: bool = False,
) -> HealthResult:
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

    async with write_lock():
        async with session() as s:
            src = await s.get(Source, source.id)
            if src is not None:
                src.last_health_at = datetime.now(timezone.utc)
                src.latency_ms = result.latency_ms
                if result.ok:
                    src.health_status = "slow" if result.latency_ms > 5000 else "ok"
                    src.fail_streak = 0
                    src.last_error = None
                    # 只要现在能跑就自动启用（手动点 health 重新启用 / 定时巡검 看到恢复的源也自动-enable），
                    # manual disabled 的源回到可用，恢复 tomorrow 也能 come back。
                    src.enabled = True
                else:
                    src.last_error = result.message
                    if disable_on_fail:
                        src.health_status = "dead"
                        src.fail_streak = max(src.fail_streak or 0, settings.health_fail_threshold)
                        src.enabled = False
                    else:
                        src.fail_streak = (src.fail_streak or 0) + 1
                        is_dead = src.fail_streak >= settings.health_fail_threshold
                        src.health_status = "dead" if is_dead else "slow"
    return result


async def health_check_all(
    only_enabled: bool = True,
    reenable: bool = False,
    disable_on_fail: bool = False,
    timeout: float = 8.0,
    retries: int = 1,
) -> list[HealthResult]:
    import asyncio

    # 用户点的「健康检查」：先把所有源重新启用，再探活，避免上次被自动禁用的源永远不能 comeback。
    if reenable:
        only_enabled = False
        async with write_lock():
            async with session() as s:
                await s.execute(update(Source).values(enabled=True, fail_streak=0))
                await s.commit()

    if only_enabled:
        async with session() as s:
            stmt = select(Source).where(Source.enabled)
            sources = list(await s.scalars(stmt))
    else:
        async with session() as s:
            sources = list(await s.scalars(select(Source)))

    # 速度：探活只做 1 次请求（原重试 3× profile 使单个死域名拖数十秒），
    # timeout 缩到 8s，gather 并行跑；总时间 ≈ 最慢的单站，而非 sum。
    original_retries = settings.max_retries
    settings.max_retries = retries
    client = Fetcher(timeout=timeout)
    try:
        return await asyncio.gather(
            *(check_source(src, client, disable_on_fail=disable_on_fail) for src in sources)
        )
    finally:
        settings.max_retries = original_retries
        await client.aclose()
