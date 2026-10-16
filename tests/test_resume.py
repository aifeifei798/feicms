"""断点续传: an interrupted `--full` sweep continues where it stopped.

`./server.sh full` runs `feicms collect --full`; the collector writes a per-source
checkpoint (data/collect_resume.json) after every finished page, so a reboot or a
Ctrl-C no longer costs the hours already spent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp()
os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{_TMP}/resume.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")
os.environ.setdefault("FEICMS_RESUME_FILE", f"{_TMP}/collect_resume.json")
os.environ.setdefault("FEICMS_LOCK_FILE", f"{_TMP}/collect.lock")
os.environ.setdefault("FEICMS_STATUS_FILE", f"{_TMP}/collect_status.json")

from sqlalchemy import select  # noqa: E402

from feicms.collector import (  # noqa: E402
    clear_resume,
    collect_all,
    collect_lock_is_stale,
    load_resume,
    release_collect_lock,
    resume_file_path,
    resume_summary,
)
from feicms.config import settings  # noqa: E402
from feicms.db import init_db, session  # noqa: E402
from feicms.fetcher import FetchResult  # noqa: E402
from feicms.models import Source  # noqa: E402


class Stub:
    """Fake 资源站 that remembers which pages were asked for."""

    def __init__(
        self,
        pagecount: int,
        per_page: int = 10,
        prefix: str = "续传影片",
        fail_pages: set[int] | None = None,
    ) -> None:
        self.pagecount = pagecount
        self.per_page = per_page
        self.prefix = prefix
        self.fail_pages = fail_pages or set()
        self.calls: list[tuple[str, int]] = []

    def data_pages(self) -> list[int]:
        """Pages fetched through the collection endpoint (not the `ac=types` probe)."""
        return [page for ac, page in self.calls if ac != "types"]

    async def get(self, url: str) -> FetchResult:
        ac = re.search(r"ac=([a-z]+)", url)
        page = int(re.search(r"pg=(\d+)", url).group(1))
        self.calls.append((ac.group(1) if ac else "?", page))
        if page in self.fail_pages:
            raise RuntimeError(f"stub timeout on page {page}")
        if page > self.pagecount:
            payload: dict[str, object] = {
                "code": 1,
                "list": [],
                "pagecount": self.pagecount,
                "total": self.pagecount * self.per_page,
            }
        else:
            start = (page - 1) * self.per_page
            payload = {
                "code": 1,
                "pagecount": self.pagecount,
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


async def _make_source(name: str) -> Source:
    await init_db()
    async with session() as s:
        src = await s.scalar(select(Source).where(Source.name == name))
        if src is None:
            src = Source(name=name, url="https://stub.example.com/api.php/provide/vod/", max_pages=5)
            s.add(src)
            await s.flush()
        return src


def setup_function() -> None:
    clear_resume()


def test_full_sweep_writes_a_checkpoint():
    async def _run() -> None:
        src = await _make_source("断点写入站")
        await collect_all(
            source_ids=[src.id],
            pages=0,
            client_factory=lambda: Stub(pagecount=4),
            resume=True,
        )

        state = load_resume()
        assert state["version"] == 1
        assert state["done"] is True
        entry = state["sources"][str(src.id)]
        assert entry["status"] == "done"
        assert entry["pages"] == 4
        assert entry["pagecount"] == 4
        assert entry["inserted"] == 40
        assert entry["next_page"] == 0
        assert Path(settings.resume_file).exists()

    asyncio.run(_run())


def test_a_completed_sweep_is_not_resumed_again():
    async def _run() -> None:
        src = await _make_source("已完成站点")
        first = Stub(pagecount=3)
        await collect_all(source_ids=[src.id], pages=0, client_factory=lambda: first, resume=True)
        assert first.data_pages() == [1, 2, 3]

        # Second run: the checkpoint says "done", so it starts over from page 1
        # rather than treating the previous sweep as something to continue.
        second = Stub(pagecount=3)
        result = (await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: second, resume=True
        ))[0]
        assert second.data_pages() == [1, 2, 3]
        assert result.pages == 3
        # Nothing new this time round: every row already exists.
        assert result.inserted == 0
        assert result.skipped == 30

    asyncio.run(_run())


def test_resume_continues_from_the_page_that_failed():
    """A source that was cut off mid-sweep restarts at its recorded next page."""

    async def _run() -> None:
        src = await _make_source("续传继续站")
        # Page 1 lands, pages 2..6 time out -> the sweep gives up at page 6.
        broken = Stub(pagecount=8, fail_pages={2, 3, 4, 5, 6})
        first = (await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: broken, resume=True
        ))[0]
        assert first.error is None
        assert first.pages == 1
        assert first.inserted == 10
        assert first.stopped_reason

        checkpoint = load_resume()["sources"][str(src.id)]
        assert checkpoint["status"] == "failed"
        assert checkpoint["next_page"] == 2

        # Resume: page 1 must not be fetched again, pages 2..8 are.
        healthy = Stub(pagecount=8)
        second = (await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: healthy, resume=True
        ))[0]
        assert healthy.data_pages() == [2, 3, 4, 5, 6, 7, 8]
        assert second.pages == 8
        # Counters carry over, so the report covers the whole sweep.
        assert second.inserted == 80
        assert load_resume()["done"] is True

    asyncio.run(_run())


def test_no_resume_starts_over_from_page_one():
    async def _run() -> None:
        src = await _make_source("重头再来站")
        first = Stub(pagecount=6, fail_pages={2, 3, 4, 5, 6})
        (await collect_all(source_ids=[src.id], pages=0, client_factory=lambda: first, resume=True))[0]
        assert load_resume()["sources"][str(src.id)]["next_page"] == 2

        fresh = Stub(pagecount=6)
        result = (await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: fresh, resume=False
        ))[0]
        assert fresh.data_pages() == [1, 2, 3, 4, 5, 6]
        # Fresh counters: nothing was carried over from the interrupted run.
        assert result.pages == 6

    asyncio.run(_run())


def test_incremental_runs_do_not_touch_the_checkpoint():
    async def _run() -> None:
        src = await _make_source("增量不动断点站")
        await collect_all(
            source_ids=[src.id], pages=2, client_factory=lambda: Stub(pagecount=9), resume=True
        )
        assert load_resume() == {}

    asyncio.run(_run())


def test_resume_summary_and_clear():
    async def _run() -> None:
        src = await _make_source("断点查询站")
        await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: Stub(pagecount=2), resume=True
        )

        info = resume_summary()
        assert info["exists"] is True
        assert info["done"] is True
        assert info["sources"] >= 1
        assert info["finished"] >= 1
        assert any(e["source_id"] == src.id for e in info["detail"])

        clear_resume()
        assert not resume_file_path().exists()
        assert resume_summary()["exists"] is False
        assert load_resume() == {}

    asyncio.run(_run())


def test_a_lock_left_by_a_dead_process_is_taken_over():
    """After a reboot the lock file survives but its pid does not: do not block."""

    async def _run() -> None:
        src = await _make_source("残锁接管站")
        lock = Path(settings.lock_file)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps(
                {
                    "pid": 4_000_000,  # above any real pid, so it is certainly gone
                    "label": "collect",
                    "started_at": "2026-10-16T00:00:00+00:00",
                }
            )
        )
        assert collect_lock_is_stale() is True

        results = await collect_all(
            source_ids=[src.id], pages=0, client_factory=lambda: Stub(pagecount=2), resume=True
        )
        assert len(results) == 1
        assert results[0].inserted == 20
        # The dead lock was replaced by ours and released when the sweep ended.
        assert not lock.exists()

    asyncio.run(_run())


def test_a_live_lock_still_blocks():
    async def _run() -> None:
        src = await _make_source("活锁仍然拦住站")
        lock = Path(settings.lock_file)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": os.getpid(), "label": "collect", "started_at": _now()})
        )
        try:
            assert collect_lock_is_stale() is False
            assert await collect_all(source_ids=[src.id], pages=0) == []
        finally:
            release_collect_lock()

    asyncio.run(_run())


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
