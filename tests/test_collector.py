from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/collector.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")

from sqlalchemy import select  # noqa: E402

from feicms.collector import (  # noqa: E402
    collect_all,
    collect_progress,
    collect_source,
    merge_duplicates,
    start_collect_all,
)
from feicms.db import init_db, session  # noqa: E402
from feicms.fetcher import FetchResult  # noqa: E402
from feicms.models import Source, SourceItem, Video  # noqa: E402


class StubClient:
    """Fake 资源站: `pagecount` pages of `per_page` items, nothing beyond that."""

    def __init__(
        self,
        pagecount: int,
        per_page: int,
        offset: int = 0,
        prefix: str = "影片",
        delay: float = 0.0,
        report_pagecount: bool = True,
        repeat: bool = False,
        fail_pages: set[int] | None = None,
    ) -> None:
        self.pagecount = pagecount
        self.per_page = per_page
        self.offset = offset
        self.prefix = prefix
        self.delay = delay
        # Some sites only report `total`, never `pagecount`.
        self.report_pagecount = report_pagecount
        # Some sites ignore the page parameter and hand back page 1 every time.
        self.repeat = repeat
        # Pages that always blow up (timeouts, 5xx) to test sweep resilience.
        self.fail_pages = fail_pages or set()

    async def get(self, url: str) -> FetchResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        page = int(re.search(r"pg=(\d+)", url).group(1))
        if self.repeat:
            page = 1
        if page in self.fail_pages:
            raise RuntimeError(f"stub timeout on page {page}")
        if page > self.pagecount:
            payload: dict[str, object] = {"code": 1, "list": [], "pagecount": self.pagecount, "total": self.pagecount * self.per_page}
        else:
            start = (page - 1) * self.per_page + self.offset
            payload = {
                "code": 1,
                "pagecount": self.pagecount if self.report_pagecount else 0,
                "total": self.pagecount * self.per_page,
                "list": [
                    {
                        "vod_id": i,
                        "vod_name": f"{self.prefix} {i}",
                        "type_name": "电影",
                        "vod_time": "2026-01-01 00:00:00",
                        "vod_play_from": "web",
                        "vod_play_url": f"第1集$https://example.com/{i}.mp4",
                    }
                    for i in range(start + 1, start + self.per_page + 1)
                ],
            }
        return FetchResult(json.dumps(payload), 200, 5.0, "stub")

    async def aclose(self) -> None:
        pass


async def _make_source(name: str, max_pages: int) -> Source:
    await init_db()
    async with session() as s:
        src = await s.scalar(select(Source).where(Source.name == name))
        if src is None:
            src = Source(name=name, url="https://stub.example.com/api.php/provide/vod/", max_pages=max_pages)
            s.add(src)
            await s.flush()
        return src


def test_full_sweep_walks_every_page():
    async def _run() -> None:
        src = await _make_source("全量测试站", max_pages=5)
        result = await collect_source(src, pages=0, client=StubClient(pagecount=4, per_page=10))
        assert result.pages == 4
        assert result.pagecount == 4
        assert result.total == 40
        assert result.inserted == 40

    asyncio.run(_run())


def test_full_sweep_follows_the_real_pagecount_of_a_big_source():
    """A full sweep is bounded by what the source reports, not by a fixed 200."""

    async def _run() -> None:
        src = await _make_source("大站全量", max_pages=5)
        result = await collect_source(src, pages=0, client=StubClient(pagecount=210, per_page=2))
        assert result.error is None
        assert result.pages == 210
        assert result.pagecount == 210
        assert result.inserted == 420

    asyncio.run(_run())


def test_full_page_cap_is_only_a_safety_brake():
    from feicms.config import settings

    async def _run() -> None:
        src = await _make_source("上限测试站", max_pages=5)
        original = settings.full_page_cap
        settings.full_page_cap = 3
        try:
            result = await collect_source(src, pages=0, client=StubClient(pagecount=210, per_page=2))
            assert result.pages == 3
        finally:
            settings.full_page_cap = original

    asyncio.run(_run())


def test_pagecount_is_derived_from_total_when_missing():
    """Sources that only report `total` still get a real page target."""

    async def _run() -> None:
        src = await _make_source("只报总数站", max_pages=10)
        result = await collect_source(
            src, pages=0, client=StubClient(pagecount=4, per_page=40, report_pagecount=False)
        )
        assert result.pagecount == 4
        assert result.pages == 4
        assert result.inserted == 160

    asyncio.run(_run())


def test_derived_pagecount_uses_the_items_a_page_actually_carries():
    """A site that forces 20 items per page must not be read as 40-per-page."""

    async def _run() -> None:
        src = await _make_source("强制每页20站", max_pages=10)
        result = await collect_source(
            src, pages=0, client=StubClient(pagecount=4, per_page=20, report_pagecount=False)
        )
        assert result.pagecount == 4
        assert result.pages == 4
        assert result.inserted == 80

    asyncio.run(_run())


def test_sweep_stops_when_a_source_ignores_the_page_parameter():
    """A site that hands back page 1 for every `pg` must not be walked forever."""

    async def _run() -> None:
        src = await _make_source("忽略分页站", max_pages=10)
        result = await collect_source(
            src, pages=0, client=StubClient(pagecount=5000, per_page=10, repeat=True)
        )
        assert result.error is None
        assert result.pages == 1
        assert result.inserted == 10
        assert result.stopped_reason

    asyncio.run(_run())


def test_a_single_timeout_does_not_abort_the_sweep():
    """One flaky page is skipped; the rest of the catalogue is still collected."""

    async def _run() -> None:
        src = await _make_source("偶发超时站", max_pages=10)
        result = await collect_source(
            src, pages=0, client=StubClient(pagecount=6, per_page=10, fail_pages={2, 4})
        )
        assert result.error is None
        assert result.stopped_reason is None
        assert result.pages == 6
        assert result.inserted == 40
        assert len(result.warnings) == 2

    asyncio.run(_run())


def test_sweep_gives_up_after_repeated_failures():
    async def _run() -> None:
        src = await _make_source("连续超时站", max_pages=10)
        result = await collect_source(
            src, pages=0, client=StubClient(pagecount=50, per_page=10, fail_pages=set(range(2, 50)))
        )
        assert result.error is None
        assert result.pages == 1
        assert len(result.warnings) == 5
        assert "failed in a row" in (result.stopped_reason or "")

    asyncio.run(_run())


def test_explicit_pages_are_respected():
    async def _run() -> None:
        src = await _make_source("页数测试站", max_pages=5)
        result = await collect_source(src, pages=2, client=StubClient(pagecount=4, per_page=10))
        assert result.pages == 2
        assert result.inserted == 20

    asyncio.run(_run())


def test_incremental_run_stops_when_nothing_changes():
    async def _run() -> None:
        src = await _make_source("增量测试站", max_pages=5)
        client = StubClient(pagecount=4, per_page=10)

        # First pass grabs everything the stub has.
        first = await collect_source(src, pages=0, client=client)
        assert first.pages == 4
        assert first.inserted == 40

        # Second pass: page 1 is all duplicates, page 2 too -> stop at page 2
        # instead of walking all 4 pages again.
        second = await collect_source(src, pages=5, client=client)
        assert second.pages == 2
        assert second.inserted == 0
        assert second.skipped >= 20

    asyncio.run(_run())


def test_background_sweep_reports_progress():
    async def _run() -> None:
        src = await _make_source("后台测试站", max_pages=5)
        state = await start_collect_all(
            source_ids=[src.id],
            client_factory=lambda: StubClient(pagecount=3, per_page=10),
        )
        assert state["running"] is True
        assert state["total_sources"] == 1

        for _ in range(200):
            state = collect_progress()
            if not state["running"]:
                break
            await asyncio.sleep(0.05)

        assert state["running"] is False
        assert state["total_sources"] == 1
        assert state["done_sources"] == 1
        assert state["inserted"] == 30
        assert state["failed_sources"] == 0
        assert state["error"] is None
        assert state["finished_at"]

        entry = next(e for e in state["sources"] if e["source_id"] == src.id)
        assert entry["current"] is True
        assert entry["name"] == "后台测试站"
        assert entry["status"] == "done"
        assert entry["pages"] == 3
        assert entry["pagecount"] == 3
        assert entry["target"] == 5
        assert entry["inserted"] == 30

        # Sources outside this sweep are still listed, flagged as not current.
        others = [e for e in state["sources"] if e["source_id"] != src.id]
        assert all(e["current"] is False for e in others)

    asyncio.run(_run())


def test_collect_all_reports_per_page_progress():
    """The sweep publishes a snapshot per page so the panel can draw a bar."""

    async def _run() -> None:
        src = await _make_source("进度测试站", max_pages=5)
        snapshots: list[tuple[int, dict]] = []
        await collect_all(
            source_ids=[src.id],
            client_factory=lambda: StubClient(pagecount=3, per_page=10),
            on_progress=lambda source_id, snap: snapshots.append((source_id, snap)),
        )

        assert snapshots[0][1]["target"] == 5
        assert snapshots[0][1]["pages"] == 0
        assert snapshots[-1][1]["pages"] == 3
        assert snapshots[-1][1]["pagecount"] == 3
        assert snapshots[-1][1]["inserted"] == 30
        assert len(snapshots) == 4

    asyncio.run(_run())


def test_unlimited_concurrency_runs_every_source_at_once():
    """Default concurrency is 0: every source is walked in parallel."""

    async def _run() -> None:
        from feicms.config import settings

        sources = [await _make_source(f"全开{letter}", max_pages=1) for letter in "ABC"]
        original = settings.concurrency
        settings.concurrency = 0
        try:
            started = time.perf_counter()
            results = await collect_all(
                source_ids=[s.id for s in sources],
                client_factory=lambda: StubClient(pagecount=1, per_page=10, delay=0.4),
            )
            wall = time.perf_counter() - started
        finally:
            settings.concurrency = original

        assert all(r.error is None for r in results)
        assert sum(r.inserted for r in results) == 30
        # Three 0.4s sources in parallel finish well under 1.2s.
        assert wall < 1.0

    asyncio.run(_run())


def test_positive_concurrency_throttles_the_sweep():
    async def _run() -> None:
        from feicms.config import settings

        sources = [await _make_source(f"限流{letter}", max_pages=1) for letter in "ABC"]
        original = settings.concurrency
        settings.concurrency = 1
        try:
            started = time.perf_counter()
            results = await collect_all(
                source_ids=[s.id for s in sources],
                client_factory=lambda: StubClient(pagecount=1, per_page=10, delay=0.3),
            )
            wall = time.perf_counter() - started
        finally:
            settings.concurrency = original

        assert all(r.error is None for r in results)
        assert sum(r.inserted for r in results) == 30
        assert wall >= 0.9

    asyncio.run(_run())


def test_concurrent_sources_merge_same_titles():
    """Two sources collecting the same titles at once must not trip the unique
    signature constraint (this is what made "采集全部" fail on a fresh database)."""

    async def _run() -> None:
        a = await _make_source("并发A", max_pages=1)
        b = await _make_source("并发B", max_pages=1)
        results = await collect_all(
            source_ids=[a.id, b.id],
            client_factory=lambda: StubClient(pagecount=1, per_page=10, prefix="并发影片", delay=0.2),
        )
        assert all(r.error is None for r in results)
        # 20 source rows, but only 10 merged videos.
        assert sum(r.inserted for r in results) == 20

        async with session() as s:
            videos = list(await s.scalars(select(Video).where(Video.name.like("并发影片%"))))
        assert len(videos) == 10
        assert all(v.source_count == 2 for v in videos)

    asyncio.run(_run())


def test_merge_duplicates_keeps_plays_and_metadata():
    """Legacy `title|year` signatures split one show into two rows; merging must
    move the source records (and their play sources) to the richest row."""

    async def _run() -> None:
        a = await _make_source("合并A", max_pages=1)
        b = await _make_source("合并B", max_pages=1)

        async with session() as s:
            legacy = Video(name="合并测试影片", signature="合并测试影片|2024", source_count=1)
            modern = Video(
                name="合并测试影片",
                signature="合并测试影片",
                blurb="完整简介",
                year="2024",
                source_count=1,
            )
            s.add_all([legacy, modern])
            await s.flush()
            s.add(
                SourceItem(
                    source_id=a.id,
                    video_id=legacy.id,
                    source_vod_id=9001,
                    name="合并测试影片",
                    plays=json.dumps(
                        [{"from": "lineA", "episodes": [{"name": "第1集", "url": "https://a.example/1"}]}]
                    ),
                )
            )
            s.add(
                SourceItem(
                    source_id=b.id,
                    video_id=modern.id,
                    source_vod_id=9002,
                    name="合并测试影片",
                    plays=json.dumps(
                        [{"from": "lineB", "episodes": [{"name": "第1集", "url": "https://b.example/1"}]}]
                    ),
                )
            )
            await s.commit()

        preview = await merge_duplicates(dry_run=True)
        assert preview["groups"] >= 1 and preview["removed"] >= 1

        result = await merge_duplicates()
        assert result["removed"] >= 1 and result["moved_items"] >= 1

        async with session() as s:
            videos = list(await s.scalars(select(Video).where(Video.name == "合并测试影片")))
            assert len(videos) == 1
            keeper = videos[0]
            assert keeper.signature == "合并测试影片"
            assert keeper.source_count == 2
            assert keeper.blurb == "完整简介"

            items = list(await s.scalars(select(SourceItem).where(SourceItem.video_id == keeper.id)))
            assert len(items) == 2
            assert {i.source_id for i in items} == {a.id, b.id}

    asyncio.run(_run())
