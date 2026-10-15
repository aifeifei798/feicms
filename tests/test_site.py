from __future__ import annotations

import json
import os

os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from feicms.api import app  # noqa: E402
from feicms.db import init_db, session  # noqa: E402
from feicms.models import Source, SourceItem, Video  # noqa: E402
from feicms.normalizer import build_signature  # noqa: E402

client = TestClient(app)


def _seed() -> int:
    import asyncio

    async def _inner() -> int:
        await init_db()
        async with session() as s:
            src = await s.scalar(select(Source).where(Source.name == "前台测试站"))
            if src is None:
                src = Source(name="前台测试站", url="https://example.com/api.php/provide/vod/")
                s.add(src)
                await s.flush()

            video = await s.scalar(select(Video).where(Video.signature == build_signature("前台影片")))
            if video is None:
                video = Video(
                    name="前台影片",
                    signature=build_signature("前台影片"),
                    type_name="电影",
                    year="2024",
                    area="大陆",
                    source_count=1,
                )
                s.add(video)
                await s.flush()
                s.add(
                    SourceItem(
                        source_id=src.id,
                        video_id=video.id,
                        source_vod_id=1,
                        name="前台影片",
                        type_name="电影",
                        play_from="testzy",
                        plays=json.dumps(
                            [
                                {
                                    "from": "testzy",
                                    "episodes": [{"name": "第01集", "url": "https://cdn.example.com/a/index.m3u8"}],
                                }
                            ]
                        ),
                    )
                )
                await s.flush()
                return video.id
            return video.id

    return asyncio.run(_inner())


def test_front_site_reads_the_library_without_login():
    from feicms.config import settings

    video_id = _seed()
    settings.admin_user, settings.admin_password = "admin", "secret"
    try:
        # The admin API is locked, the public front site is not.
        assert client.get("/api/videos").status_code == 401

        listing = client.get("/api/site/videos", params={"q": "前台", "limit": 5})
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] >= 1
        assert body["items"][0]["name"] == "前台影片"

        detail = client.get(f"/api/site/videos/{video_id}")
        assert detail.status_code == 200
        plays = detail.json()["plays"]
        assert plays[0]["from_name"] == "testzy"
        assert plays[0]["episodes"][0]["url"].endswith(".m3u8")

        assert client.get(f"/api/site/videos/{video_id}/related").status_code == 200
        filters = client.get("/api/site/filters").json()
        assert any(t["type_name"] == "电影" for t in filters["types"])
        assert "2024" in filters["years"]

        stats = client.get("/api/site/stats").json()
        assert stats["videos"] >= 1 and stats["sources"] >= 1

        assert client.get("/api/site/videos/999999").status_code == 404
    finally:
        settings.admin_user, settings.admin_password = "", ""


def test_front_site_filters_and_sorting():
    _seed()
    by_type = client.get("/api/site/videos", params={"type_name": "电影"}).json()
    assert by_type["total"] >= 1
    assert client.get("/api/site/videos", params={"type_name": "不存在"}).json()["total"] == 0
    assert client.get("/api/site/videos", params={"sort": "name", "limit": 1}).status_code == 200
    assert client.get("/api/site/videos", params={"sort": "bogus"}).status_code == 422


def test_spa_deep_links_fall_back_to_the_app():
    from feicms.api import STATIC_ROOT

    if STATIC_ROOT is None:  # frontend not built in this environment
        return
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/video/123").status_code == 200
    assert client.get("/assets/definitely-missing.js").status_code == 404
