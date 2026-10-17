from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/refresh.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")
os.environ.setdefault("FEICMS_AUTO_REFRESH", "false")
os.environ.setdefault("FEICMS_LOCK_FILE", f"{tempfile.mkdtemp()}/refresh.lock")

from sqlalchemy import select  # noqa: E402

from feicms.db import init_db, session  # noqa: E402
from feicms.fetcher import FetchResult  # noqa: E402
from feicms.models import Source, SourceItem, Video  # noqa: E402
from feicms.refresh import (  # noqa: E402
    pick_refresh_targets,
    refresh_progress,
    refresh_source,
    refresh_stale,
    start_refresh_stale,
)


class StubIdsClient:
    """Fake 资源站 that answers `ac=detail&ids=1,2,3` from an in-memory catalogue."""

    def __init__(
        self,
        catalogue: dict[int, dict[str, object]],
        ignore_ids: bool = False,
        fail: bool = False,
        first_only: bool = False,
    ) -> None:
        self.catalogue = catalogue
        self.ignore_ids = ignore_ids
        self.fail = fail
        self.first_only = first_only
        self.requests: list[str] = []

    async def get(self, url: str) -> FetchResult:
        self.requests.append(url)
        if self.fail:
            raise RuntimeError("stub ids endpoint unreachable")
        match = re.search(r"ids=([\d,]+)", url)
        ids = [int(x) for x in match.group(1).split(",")] if match else []
        if self.ignore_ids:
            # A site without a per-record endpoint answers with a generic list.
            payload = {
                "code": 1,
                "list": [
                    {
                        "vod_id": 999999,
                        "vod_name": "无关影片",
                        "type_name": "电影",
                        "vod_time": "2026-01-01 00:00:00",
                        "vod_play_from": "web",
                        "vod_play_url": "https://example.com/other.mp4",
                    }
                ],
            }
        else:
            wanted = ids[:1] if self.first_only else ids
            payload = {"code": 1, "list": [self.catalogue[i] for i in wanted if i in self.catalogue]}
        return FetchResult(json.dumps(payload), 200, 5.0, "stub")

    async def aclose(self) -> None:
        pass


async def _make_source(name: str) -> Source:
    await init_db()
    async with session() as s:
        src = await s.scalar(select(Source).where(Source.name == name))
        if src is None:
            src = Source(name=name, url="https://stub.example.com/api.php/provide/vod/")
            s.add(src)
            await s.flush()
        return src


async def _seed(src: Source, vod_id: int, name: str, plays: list[dict]) -> Video:
    async with session() as s:
        video = Video(name=name, signature=name)
        s.add(video)
        await s.flush()
        s.add(
            SourceItem(
                source_id=src.id,
                video_id=video.id,
                source_vod_id=vod_id,
                name=name,
                type_name="电影",
                play_from="web",
                plays=json.dumps(plays),
            )
        )
        await s.commit()
        return video


def test_refresh_replaces_play_sources_of_a_stale_record():
    async def _run() -> None:
        src = await _make_source("补扫站")
        video = await _seed(src, 101, "老片子", [{"from": "old", "episodes": [{"name": "", "url": "https://old.example/1.mp4"}]}])

        client = StubIdsClient(
            {
                101: {
                    "vod_id": 101,
                    "vod_name": "老片子",
                    "type_name": "电影",
                    "vod_time": "2026-10-17 09:00:00",
                    "vod_play_from": "newm3u8",
                    "vod_play_url": "https://new.example/1.mp4",
                }
            }
        )

        results = await refresh_stale(days=0, limit=100, names=["补扫站"], client_factory=lambda: client)
        assert len(results) == 1
        assert results[0].requested == 1 and results[0].fetched == 1
        assert results[0].updated == 1
        assert results[0].unsupported is False

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.source_vod_id == 101)
            )
            refreshed = json.loads(item.plays)
            assert refreshed[0]["from"] == "newm3u8"
            assert refreshed[0]["episodes"][0]["url"] == "https://new.example/1.mp4"
            assert item.last_checked_at is not None

            updated_video = await s.get(Video, video.id)
            assert updated_video.last_updated != video.first_seen

    asyncio.run(_run())


def test_a_source_that_only_answers_one_id_is_walked_one_by_one():
    async def _run() -> None:
        src = await _make_source("单id站")
        await _seed(src, 707, "单id片子A", [{"from": "old", "episodes": [{"url": "https://old.example/a"}]}])
        await _seed(src, 708, "单id片子B", [{"from": "old", "episodes": [{"url": "https://old.example/b"}]}])

        client = StubIdsClient(
            {
                707: {"vod_id": 707, "vod_name": "单id片子A", "vod_play_from": "a", "vod_play_url": "https://new.example/a"},
                708: {"vod_id": 708, "vod_name": "单id片子B", "vod_play_from": "b", "vod_play_url": "https://new.example/b"},
            },
            first_only=True,
        )

        results = await refresh_stale(
            days=0, limit=100, names=["单id站"], batch=20, client_factory=lambda: client
        )
        assert results[0].mode == "single"
        assert results[0].requested == 3  # 2 in the short batch + the probe + 708
        assert results[0].updated == 2

        async with session() as s:
            for vod_id, from_name in ((707, "a"), (708, "b")):
                item = await s.scalar(
                    select(SourceItem).where(
                        SourceItem.source_id == src.id, SourceItem.source_vod_id == vod_id
                    )
                )
                assert json.loads(item.plays)[0]["from"] == from_name
                assert item.last_checked_at is not None

    asyncio.run(_run())


def test_records_the_source_does_not_have_are_counted_as_gone():
    async def _run() -> None:
        src = await _make_source("已删站")
        await _seed(src, 202, "上游已删的片子", [{"from": "web", "episodes": [{"url": "https://a.example/1"}]}])

        results = await refresh_stale(
            days=0,
            limit=100,
            names=["已删站"],
            client_factory=lambda: StubIdsClient({}),
        )
        assert results[0].requested == 1
        assert results[0].fetched == 0
        assert results[0].gone == 1
        assert results[0].updated == 0

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.source_vod_id == 202)
            )
            assert item.last_checked_at is not None  # checked, so the next run moves on

    asyncio.run(_run())


def test_a_source_without_an_ids_endpoint_is_skipped_not_mangled():
    async def _run() -> None:
        src = await _make_source("无详情站")
        await _seed(src, 303, "不该被改的片子", [{"from": "web", "episodes": [{"url": "https://a.example/1"}]}])

        results = await refresh_stale(
            days=0,
            limit=100,
            names=["无详情站"],
            client_factory=lambda: StubIdsClient({}, ignore_ids=True),
        )
        assert results[0].unsupported is True
        assert results[0].inserted == 0 and results[0].updated == 0

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.source_vod_id == 303)
            )
            assert json.loads(item.plays)[0]["from"] == "web"
            assert item.last_checked_at is None

    asyncio.run(_run())


def test_dry_run_changes_nothing():
    async def _run() -> None:
        src = await _make_source("预览站")
        await _seed(src, 404, "预览片子", [{"from": "web", "episodes": [{"url": "https://a.example/1"}]}])

        targets = await pick_refresh_targets(days=0, limit=100, names=["预览站"])
        assert targets[src.id] == [404]

        client = StubIdsClient(
            {404: {"vod_id": 404, "vod_name": "预览片子", "vod_play_from": "web", "vod_play_url": "https://b.example/1"}}
        )
        results = await refresh_stale(
            days=0, limit=100, names=["预览站"], dry_run=True, client_factory=lambda: client
        )
        assert results[0].fetched == 1

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.source_vod_id == 404)
            )
            assert json.loads(item.plays)[0]["episodes"][0]["url"] == "https://a.example/1"
            assert item.last_checked_at is None

    asyncio.run(_run())


def test_records_checked_recently_are_not_picked_up_again():
    async def _run() -> None:
        src = await _make_source("已核对站")
        await _seed(src, 505, "刚核对过的片子", [{"from": "web", "episodes": [{"url": "https://a.example/1"}]}])

        client = StubIdsClient({505: {"vod_id": 505, "vod_name": "刚核对过的片子", "vod_play_from": "web", "vod_play_url": "https://a.example/1"}})
        await refresh_stale(days=0, limit=100, names=["已核对站"], client_factory=lambda: client)

        # Nothing is overdue any more, and the record still has play sources.
        assert await pick_refresh_targets(days=365, limit=100, names=["已核对站"]) == {}

    asyncio.run(_run())


def test_a_source_that_errors_on_ids_is_reported_not_crashed():
    async def _run() -> None:
        src = await _make_source("炸站")
        await _seed(src, 606, "抓不到的片子", [{"from": "web", "episodes": [{"url": "https://a.example/1"}]}])

        results = await refresh_stale(
            days=0, limit=100, names=["炸站"], client_factory=lambda: StubIdsClient({}, fail=True)
        )
        assert results[0].error
        assert results[0].unsupported is True

    asyncio.run(_run())


def test_start_refresh_runs_in_background_and_tracks_progress():
    async def _run() -> None:
        src = await _make_source("后台站")
        await _seed(src, 901, "后台片子", [{"from": "old", "episodes": [{"url": "https://old.example/9"}]}])

        client = StubIdsClient(
            {901: {"vod_id": 901, "vod_name": "后台片子", "vod_play_from": "new", "vod_play_url": "https://new.example/9"}}
        )

        state = await start_refresh_stale(
            days=0, limit=100, names=["后台站"], client_factory=lambda: client
        )
        assert state["running"] is True

        for _ in range(100):
            state = refresh_progress()
            if not state["running"]:
                break
            await asyncio.sleep(0.05)

        assert state["running"] is False
        assert state["finished_at"]
        assert state["checked"] == 1 and state["updated"] == 1
        assert state["error"] is None

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.source_vod_id == 901)
            )
            assert json.loads(item.plays)[0]["from"] == "new"

    asyncio.run(_run())
