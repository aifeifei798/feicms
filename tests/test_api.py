from __future__ import annotations

import os
import tempfile

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")

from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import select  # noqa: E402

from feicms.api import app  # noqa: E402
from feicms.db import init_db, session  # noqa: E402
from feicms.models import Source, SourceItem, Video  # noqa: E402
from feicms.normalizer import build_signature  # noqa: E402

client = TestClient(app)


def _seed() -> None:
    import asyncio

    async def _inner() -> None:
        await init_db()
        async with session() as s:
            src = await s.scalar(select(Source).where(Source.name == "测试站"))
            if src is None:
                src = Source(name="测试站", url="https://example.com/api.php/provide/vod/")
                s.add(src)
                await s.flush()
            video = await s.scalar(select(Video).where(Video.signature == build_signature("测试影片")))
            if video is None:
                video = Video(name="测试影片", signature=build_signature("测试影片"), type_name="电影", year="2024")
                s.add(video)
                await s.flush()
                s.add(
                    SourceItem(
                        source_id=src.id,
                        video_id=video.id,
                        source_vod_id=1,
                        name="测试影片",
                        type_name="电影",
                        play_from="test",
                        play_url="正片$https://v/1.mp4",
                        plays='[{"from": "test", "episodes": [{"name": "正片", "url": "https://v/1.mp4"}]}]',
                    )
                )

    asyncio.run(_inner())


def test_health_endpoint():
    assert client.get("/health").json() == {"status": "ok"}


def test_source_crud():
    _seed()
    created = client.post("/api/sources", json={"name": "新站点", "url": "https://new.example.com/api.php/provide/vod/"})
    assert created.status_code == 201
    sid = created.json()["id"]

    listed = client.get("/api/sources").json()
    assert any(s["id"] == sid for s in listed)

    patched = client.patch(f"/api/sources/{sid}", json={"enabled": False, "max_pages": 3})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["max_pages"] == 3

    assert client.delete(f"/api/sources/{sid}").status_code == 204
    assert client.get(f"/api/sources/{sid}").status_code == 404


def test_duplicate_source_rejected():
    _seed()
    payload = {"name": "测试站", "url": "https://dup.example.com/"}
    assert client.post("/api/sources", json=payload).status_code == 409


def test_video_search_and_detail():
    _seed()
    search = client.get("/api/videos", params={"q": "测试"}).json()
    assert search["total"] >= 1
    video_id = search["items"][0]["id"]

    detail = client.get(f"/api/videos/{video_id}").json()
    assert detail["name"] == "测试影片"
    assert detail["plays"][0]["from_name"] == "test"
    assert detail["plays"][0]["episodes"][0]["url"] == "https://v/1.mp4"

    sources = client.get(f"/api/videos/{video_id}/sources").json()
    assert sources[0]["source_name"] == "测试站"


def test_provide_endpoint_is_mackin_compatible():
    _seed()
    body = client.get("/provide/vod", params={"ac": "list", "limit": 5}).json()
    assert body["code"] == 1
    assert "list" in body
    assert body["list"][0]["vod_name"] == "测试影片"
    assert "$" in body["list"][0]["vod_play_url"]


def test_stats():
    _seed()
    stats = client.get("/api/stats").json()
    assert stats["videos"] >= 1
    assert stats["sources"] >= 1


def test_config_endpoint_reads_env():
    cfg = client.get("/api/config").json()
    assert "database_url" in cfg
    assert isinstance(cfg["default_pages"], int)


def test_prune_removes_empty_and_orphan_records():
    _seed()

    import asyncio

    async def _make_orphans() -> None:
        async with session() as s:
            src = await s.scalar(select(Source).where(Source.name == "测试站"))
            orphan_video = Video(name="孤儿影片", signature=build_signature("孤儿影片"))
            s.add(orphan_video)
            await s.flush()
            s.add(
                SourceItem(
                    source_id=src.id,
                    video_id=orphan_video.id,
                    source_vod_id=99,
                    name="孤儿影片",
                    play_from="test",
                    play_url="",
                    plays="[]",
                )
            )

    asyncio.run(_make_orphans())

    result = client.post("/api/prune").json()
    assert result["removed"] >= 1
    assert client.get("/api/videos", params={"q": "孤儿"}).json()["total"] == 0
