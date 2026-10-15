"""源订阅 (source subscriptions).

A subscription is a remote list of 资源站 endpoints — JSON (ziyuanzu style) or
pipe-delimited TXT. Syncing a subscription:

  * adds sources that are new to the database
  * refreshes uptime/priority/format for sources that already exist
  * reports sources that used to come from the feed but are no longer listed

Matching is by URL, so the same site listed under two names is not duplicated.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from .config import settings
from .db import session
from .fetcher import Fetcher
from .models import Source, Subscription

UPTIME_RE = re.compile(r"(\d+(?:\.\d+)?)")


def detect_kind(url: str) -> str:
    lowered = url.lower()
    if lowered.endswith(".json"):
        return "json"
    if lowered.endswith((".txt", ".csv")):
        return "txt"
    return "auto"


def _parse_uptime(value: object) -> float:
    if value is None:
        return 100.0
    if isinstance(value, (int, float)):
        return float(value)
    match = UPTIME_RE.search(str(value))
    return float(match.group(1)) if match else 100.0


def _format_from_api_type(value: object) -> str:
    text = (value or "").strip().lower()
    if "xml" in text or "rss" in text:
        return "xml"
    if "json" in text:
        return "json"
    return "auto"


def _from_pipe(line: str) -> dict | None:
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2 or not parts[1]:
        return None
    if not parts[1].startswith(("http://", "https://")):
        return None
    uptime = _parse_uptime(parts[3] if len(parts) > 3 else None)
    return {
        "name": parts[0] or urlsplit(parts[1]).netloc,
        "url": parts[1],
        "api_format": _format_from_api_type(parts[2] if len(parts) > 2 else None),
        "uptime": uptime,
        "priority": int(uptime * 10),
        "max_pages": 5,
        "tags": "",
    }


def parse_subscription(text: str, kind: str = "auto") -> list[dict]:
    """Turn a feed into normalised source dicts."""
    text = text.strip()
    if not text:
        return []

    if kind in ("json", "auto") and text.startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("interfaces") or data.get("sources") or data.get("list") or data.get("data") or []
        entries: list[dict] = []
        for raw in data:
            if isinstance(raw, str):
                entry = _from_pipe(raw)
            else:
                url = (raw.get("api") or raw.get("url") or raw.get("vod_url") or raw.get("interface") or "").strip()
                if not url:
                    continue
                uptime = _parse_uptime(raw.get("uptime"))
                entry = {
                    "name": (raw.get("name") or raw.get("title") or urlsplit(url).netloc).strip(),
                    "url": url,
                    "api_format": _format_from_api_type(raw.get("apiType") or raw.get("type")),
                    "uptime": uptime,
                    "priority": int(uptime * 10),
                    "max_pages": 5,
                    "tags": ",".join(str(c) for c in (raw.get("categories") or []))[:200],
                }
            if entry:
                entries.append(entry)
        return entries

    entries = []
    for line in text.splitlines():
        entry = _from_pipe(line.strip())
        if entry:
            entries.append(entry)
    return entries


@dataclass
class SyncResult:
    subscription_id: int
    url: str
    entries: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "subscription_id": self.subscription_id,
            "url": self.url,
            "entries": self.entries,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "missing": self.missing,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


async def ensure_default_subscriptions() -> list[Subscription]:
    """Seed subscriptions from FEICMS_SUBSCRIPTIONS (comma separated)."""
    async with session() as s:
        out: list[Subscription] = []
        for url in settings.subscriptions:
            sub = await s.scalar(select(Subscription).where(Subscription.url == url))
            if sub is None:
                sub = Subscription(url=url, kind=detect_kind(url))
                s.add(sub)
            out.append(sub)
        await s.flush()
        return out


async def _unique_name(s, name: str) -> str:
    if await s.scalar(select(Source).where(Source.name == name)) is None:
        return name
    suffix = 2
    while await s.scalar(select(Source).where(Source.name == f"{name} ({suffix})")) is not None:
        suffix += 1
    return f"{name} ({suffix})"


async def sync_subscription(subscription_id: int, fetcher: Fetcher | None = None) -> SyncResult:
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    started = time.perf_counter()
    result = SyncResult(subscription_id=subscription_id, url="")

    try:
        async with session() as s:
            sub = await s.get(Subscription, subscription_id)
            if sub is None:
                raise ValueError(f"subscription {subscription_id} not found")
            result.url = sub.url

            response = await fetcher.get(sub.url)
            entries = parse_subscription(response.text, sub.kind or detect_kind(sub.url))
            result.entries = len(entries)

            seen: set[str] = set()
            for entry in entries:
                url = entry["url"]
                seen.add(url)
                src = await s.scalar(select(Source).where(Source.url == url))
                if src is None:
                    s.add(
                        Source(
                            name=await _unique_name(s, entry["name"]),
                            url=url,
                            api_format=entry["api_format"],
                            priority=entry["priority"],
                            max_pages=entry["max_pages"],
                            tags=entry["tags"],
                            uptime=entry["uptime"],
                            origin="subscription",
                            subscription_url=sub.url,
                        )
                    )
                    result.added += 1
                    continue

                changed = False
                if src.api_format == "auto" and entry["api_format"] != "auto":
                    src.api_format = entry["api_format"]
                    changed = True
                if src.uptime != entry["uptime"]:
                    src.uptime = entry["uptime"]
                    src.priority = entry["priority"]
                    changed = True
                if src.origin == "manual":
                    src.origin = "subscription"
                    changed = True
                if not src.subscription_url:
                    src.subscription_url = sub.url
                    changed = True
                if changed:
                    result.updated += 1
                else:
                    result.unchanged += 1

            stale = list(
                await s.scalars(
                    select(Source).where(Source.subscription_url == sub.url, Source.url.not_in(seen))
                )
            )
            result.missing = [src.name for src in stale]

            sub.last_sync_at = datetime.now(timezone.utc)
            sub.last_added = result.added
            sub.last_updated = result.updated
            sub.last_entries = result.entries
            sub.last_error = None
            await s.flush()
    except Exception as exc:  # noqa: BLE001 - recorded on the subscription row
        result.error = str(exc)
        async with session() as s:
            sub = await s.get(Subscription, subscription_id)
            if sub is not None:
                sub.last_error = str(exc)
                sub.last_sync_at = datetime.now(timezone.utc)

    finally:
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        if own_fetcher:
            await fetcher.aclose()

    return result


async def sync_all(fetcher: Fetcher | None = None) -> list[SyncResult]:
    async with session() as s:
        ids = [sub.id for sub in await s.scalars(select(Subscription).where(Subscription.enabled))]
    fetcher = fetcher or Fetcher()
    try:
        return [await sync_subscription(sub_id, fetcher) for sub_id in ids]
    finally:
        await fetcher.aclose()
