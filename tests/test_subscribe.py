from __future__ import annotations

import asyncio
import os
import tempfile

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/subscribe.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")

from sqlalchemy import select  # noqa: E402

from feicms.db import init_db, session  # noqa: E402
from feicms.fetcher import FetchResult  # noqa: E402
from feicms.models import Source, Subscription  # noqa: E402
from feicms.subscribe import detect_kind, parse_subscription, sync_subscription  # noqa: E402

JSON_FEED = """[
  {"name": "360资源站", "api": "https://360zy.com/api.php/provide/vod/", "apiType": "json",
   "uptime": "99.999%", "totalResources": 65092, "categories": []},
  {"name": "大地资源站", "api": "https://dadiapi.com/api.php", "apiType": "xml",
   "uptime": "100%", "totalResources": 277283, "categories": ["日韩"]}
]"""

TXT_FEED = """# 资源组
360资源站|https://360zy.com/api.php/provide/vod/|json|99.999%
大地资源站|https://dadiapi.com/api.php|xml|100%
新站|https://new.example.com/api.php/provide/vod/|json|98%
"""


class FakeFetcher:
    def __init__(self, text: str) -> None:
        self.text = text

    async def get(self, url: str) -> FetchResult:
        return FetchResult(text=self.text, status_code=200, latency_ms=1.0, engine="fake")

    async def aclose(self) -> None:
        pass


def test_detect_kind():
    assert detect_kind("https://x.com/feed.json") == "json"
    assert detect_kind("https://x.com/feed.txt") == "txt"
    assert detect_kind("https://x.com/api") == "auto"


def test_parse_json_feed():
    entries = parse_subscription(JSON_FEED, "json")
    assert len(entries) == 2
    assert entries[0]["name"] == "360资源站"
    assert entries[0]["api_format"] == "json"
    assert entries[0]["uptime"] == 99.999
    assert entries[0]["priority"] == 999
    assert entries[1]["api_format"] == "xml"


def test_parse_txt_feed():
    entries = parse_subscription(TXT_FEED, "txt")
    assert len(entries) == 3
    assert entries[2]["url"] == "https://new.example.com/api.php/provide/vod/"
    assert entries[2]["uptime"] == 98.0


def test_sync_subscription_adds_and_dedupes():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            sub = Subscription(url="https://example.com/feed.txt", kind="txt")
            s.add(sub)
            await s.flush()
            sub_id = sub.id

        result = await sync_subscription(sub_id, FakeFetcher(TXT_FEED))
        assert result.entries == 3
        assert result.added == 3

        async with session() as s:
            sources = list(await s.scalars(select(Source).where(Source.origin == "subscription")))
            assert len(sources) == 3

        # second run must not duplicate anything
        again = await sync_subscription(sub_id, FakeFetcher(TXT_FEED))
        assert again.added == 0
        assert again.unchanged == 3

        async with session() as s:
            assert len(list(await s.scalars(select(Source).where(Source.origin == "subscription")))) == 3

    asyncio.run(_run())


def test_sync_subscription_reports_missing_sources():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            sub = Subscription(url="https://example.com/feed2.txt", kind="txt")
            s.add(sub)
            await s.flush()
            sub_id = sub.id
            s.add(Source(name="旧站", url="https://old.example.com/api.php", origin="subscription", subscription_url="https://example.com/feed2.txt"))
            await s.flush()

        result = await sync_subscription(sub_id, FakeFetcher("新站|https://new.example.com/api.php|json|100%"))
        assert result.added == 1
        assert result.missing == ["旧站"]

    asyncio.run(_run())
