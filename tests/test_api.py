from __future__ import annotations

import json
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


def test_merge_duplicates_endpoint():
    import asyncio

    async def _make_duplicates() -> None:
        async with session() as s:
            src = await s.scalar(select(Source).where(Source.name == "测试站"))
            legacy = Video(name="合并影片", signature="合并影片|2023")
            modern = Video(name="合并影片", signature=build_signature("合并影片"), blurb="完整简介")
            s.add_all([legacy, modern])
            await s.flush()
            for vod_id, video_id in ((1234, legacy.id), (1235, modern.id)):
                s.add(
                    SourceItem(
                        source_id=src.id,
                        video_id=video_id,
                        source_vod_id=vod_id,
                        name="合并影片",
                        play_from="test",
                        play_url="正片$https://v/%d.mp4" % vod_id,
                        plays=json.dumps(
                            [{"from": "test", "episodes": [{"name": "正片", "url": "https://v/%d.mp4" % vod_id}]}]
                        ),
                    )
                )

    asyncio.run(_make_duplicates())

    preview = client.post("/api/merge-duplicates", params={"dry_run": True}).json()
    assert preview["dry_run"] is True and preview["groups"] >= 1

    result = client.post("/api/merge-duplicates").json()
    assert result["removed"] >= 1 and result["moved_items"] >= 1

    async def _check() -> None:
        async with session() as s:
            videos = list(await s.scalars(select(Video).where(Video.name == "合并影片")))
            assert len(videos) == 1
            keeper = videos[0]
            assert keeper.signature == build_signature("合并影片")
            assert keeper.blurb == "完整简介"
            items = list(await s.scalars(select(SourceItem).where(SourceItem.video_id == keeper.id)))
            assert len(items) == 2

    asyncio.run(_check())


def test_bulk_import_and_export_sources():
    res = client.post(
        "/api/sources/import",
        json={
            "text": (
                "导入站|https://import.example.com/api.php/provide/vod/|xml|98\n"
                "https://bare.example.com/vod/\n"
                "# 注释行会被忽略\n"
            )
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["parsed"] == 2 and body["created"] == 2

    # Re-importing the same URL updates the existing row instead of duplicating it.
    again = client.post(
        "/api/sources/import",
        json={"text": "导入站改名|https://import.example.com/api.php/provide/vod/|json"},
    ).json()
    assert again["created"] == 0 and again["updated"] == 1

    yaml_text = client.get("/api/sources/export?fmt=yaml").text
    assert "import.example.com" in yaml_text
    csv_text = client.get("/api/sources/export?fmt=csv").text
    assert csv_text.startswith("name,url,api_format,enabled,priority,max_pages,tags,uptime")
    assert "导入站改名" in csv_text
    assert client.get("/api/sources/export?fmt=bad").status_code == 422


def test_export_videos_endpoint():
    _seed()
    res = client.get("/api/export?fmt=jsonl&limit=50")
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]

    records = [json.loads(line) for line in res.text.strip().splitlines()]
    record = next(r for r in records if r["name"] == "测试影片")
    assert record["sources"] and record["sources"][0]["plays"]

    assert isinstance(client.get("/api/export?fmt=json").json(), list)
    assert client.get("/api/export?q=不存在的影片").text.strip() == ""


def test_scheduler_status_and_runtime_update():
    state = client.get("/api/scheduler").json()
    assert "auto_collect" in state and "collect_running" in state

    updated = client.post(
        "/api/scheduler",
        json={"collect_interval_minutes": 15, "auto_images": False},
    ).json()
    assert updated["collect_interval_minutes"] == 15
    assert updated["auto_images"] is False
    assert client.post("/api/scheduler", json={"collect_interval_minutes": 0}).status_code == 422


def test_admin_endpoints_require_admin_account():
    assert client.post("/api/admin/password", json={"password": "x"}).status_code == 403
    assert client.post("/api/admin/token").status_code == 403


def test_admin_password_and_token_endpoints():
    from feicms.config import settings

    settings.admin_user, settings.admin_password = "admin", "temporary-secret"
    env_path = os.path.join(tempfile.mkdtemp(), ".env")
    os.environ["FEICMS_ENV_FILE"] = env_path
    try:
        token = client.post("/api/login", json={"username": "admin", "password": "temporary-secret"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        res = client.post("/api/admin/password", json={"username": "admin", "password": "new-strong-pass"}, headers=headers)
        assert res.status_code == 200
        assert res.json()["user"] == "admin"
        written = open(env_path, encoding="utf-8").read()
        assert "FEICMS_ADMIN_USER=admin" in written
        assert "FEICMS_ADMIN_PASSWORD=new-strong-pass" in written

        generated = client.post("/api/admin/password", json={}, headers=headers).json()
        assert generated["password"]

        tok = client.post("/api/admin/token", params={"ttl": 600}, headers=headers).json()
        assert tok["token"].startswith("admin|")
        assert tok["expires_in"] == 600
    finally:
        settings.admin_user, settings.admin_password = "", ""
        os.environ.pop("FEICMS_ENV_FILE", None)


def test_images_layout_endpoint():
    layout = client.get("/api/images/layout?top=5").json()
    assert "buckets" in layout and "files" in layout and "top" in layout
