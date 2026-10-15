from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from feicms.api import app
from feicms.apikeys import create_api_key, delete_api_key, list_api_keys
from feicms.config import settings
from feicms.db import init_db, session
from feicms.mcp import TOOL_NAMES, handle_rpc
from feicms.models import Source, SourceItem, Video
from feicms.normalizer import build_signature


def _clear_keys() -> None:
    async def inner() -> None:
        for row in await list_api_keys():
            await delete_api_key(row.id)

    asyncio.run(inner())


def _client() -> TestClient:
    settings.admin_user = ""
    settings.admin_password = ""
    settings.secret = ""
    asyncio.run(init_db())
    _clear_keys()
    return TestClient(app)


def _rpc(client: TestClient, method: str, params: dict | None = None, headers: dict | None = None) -> dict:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=headers or {})
    assert response.status_code == 200
    return response.json()


def test_initialize_and_tools_list():
    client = _client()
    init = _rpc(client, "initialize")
    assert init["result"]["serverInfo"]["name"] == "feicms"
    assert init["result"]["protocolVersion"]

    tools = _rpc(client, "tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == TOOL_NAMES
    assert all("inputSchema" in tool for tool in tools)
    # internal scope metadata is not part of the MCP wire format
    assert all("scopes" not in tool for tool in tools)


def test_call_read_tool():
    client = _client()
    result = _rpc(client, "tools/call", {"name": "get_stats"})["result"]
    assert not result.get("isError", False)
    assert "videos" in result["structuredContent"]

    search = _rpc(client, "tools/call", {"name": "search", "arguments": {"limit": 5}})["result"]
    assert isinstance(search["structuredContent"]["items"], list)

    resources = _rpc(client, "resources/list")["result"]["resources"]
    assert any(r["uri"] == "feicms://stats" for r in resources)
    read = _rpc(client, "resources/read", {"uri": "feicms://stats"})["result"]["contents"][0]
    assert read["mimeType"] == "application/json"
    instructions = _rpc(client, "resources/read", {"uri": "feicms://instructions"})["result"]["contents"][0]
    assert "只读" in instructions["text"]


def test_documents_and_chunks_are_text():
    client = _client()
    items = _rpc(client, "tools/call", {"name": "browse", "arguments": {"limit": 3}})["result"]["structuredContent"]["items"]
    if not items:
        return
    video_id = items[0]["id"]

    document = _rpc(client, "tools/call", {"name": "get_document", "arguments": {"video_id": video_id}})["result"]
    assert document["structuredContent"]["text"].startswith(items[0]["name"])

    chunks = _rpc(client, "tools/call", {"name": "read_chunks", "arguments": {"limit": 5}})["result"]["structuredContent"]
    assert len(chunks["chunks"]) <= 5
    assert all(chunk["text"] for chunk in chunks["chunks"])

    related = _rpc(client, "tools/call", {"name": "related", "arguments": {"video_id": video_id}})["result"]["structuredContent"]
    assert isinstance(related["items"], list)


def test_play_urls_are_included_in_documents():
    async def _seed() -> int:
        await init_db()
        async with session() as s:
            source = Source(name="MCP 测试站", url="https://mcp.example.com/api.php")
            s.add(source)
            await s.flush()
            video = Video(name="MCP 播放源测试", signature=build_signature("MCP 播放源测试"))
            s.add(video)
            await s.flush()
            s.add(
                SourceItem(
                    source_id=source.id,
                    video_id=video.id,
                    source_vod_id=1,
                    name="MCP 播放源测试",
                    play_from="line1,line2",
                    plays=json.dumps(
                        [
                            {
                                "from": "line1",
                                "episodes": [
                                    {"name": "第1集", "url": "https://v.example.com/1.mp4"},
                                    {"name": "第2集", "url": "https://v.example.com/2.mp4"},
                                ],
                            },
                            {"from": "line2", "episodes": [{"name": "正片", "url": "https://v.example.com/single.mp4"}]},
                        ]
                    ),
                )
            )
            await s.commit()
            return video.id

    video_id = asyncio.run(_seed())
    client = _client()

    document = _rpc(client, "tools/call", {"name": "get_document", "arguments": {"video_id": video_id}})["result"]["structuredContent"]
    assert "共 2 集" in document["text"]
    assert "https://v.example.com/1.mp4" in document["text"]
    assert "https://v.example.com/single.mp4" in document["text"]

    compact = _rpc(client, "tools/call", {"name": "get_document", "arguments": {"video_id": video_id, "include_urls": False}})["result"]["structuredContent"]
    assert "https://v.example.com" not in compact["text"]

    plays = _rpc(client, "tools/call", {"name": "get_plays", "arguments": {"video_id": video_id}})["result"]["structuredContent"]
    assert plays["play_sources"] == 2
    assert plays["episode_count"] == 3
    assert plays["plays"][0]["source"] == "MCP 测试站"
    assert plays["plays"][0]["episodes"][1]["url"] == "https://v.example.com/2.mp4"

    search = _rpc(client, "tools/call", {"name": "search", "arguments": {"query": "MCP 播放源测试"}})["result"]["structuredContent"]
    assert search["items"][0]["episode_count"] == 3
    assert search["items"][0]["first_url"] == "https://v.example.com/1.mp4"

    chunks = _rpc(client, "tools/call", {"name": "read_chunks", "arguments": {"limit": 500, "include_urls": True}})["result"]["structuredContent"]
    matched = [c for c in chunks["chunks"] if c["id"] == video_id]
    if matched:
        assert "https://v.example.com/1.mp4" in matched[0]["text"]


def test_mcp_exposes_no_maintenance_tools():
    client = _client()
    for mutating in ("collect_sources", "check_health", "download_images", "provide_feed"):
        assert "error" in _rpc(client, "tools/call", {"name": mutating})
    assert all(set(tool["scopes"]) == {"read"} for tool in client.get("/api/mcp/tools").json())


def test_key_scopes_are_enforced():
    client = _client()
    _, plain = asyncio.run(create_api_key(name="ai", scopes="read"))
    headers = {"X-API-Key": plain}

    ok = _rpc(client, "tools/call", {"name": "search", "arguments": {"limit": 3}}, headers)["result"]
    assert not ok.get("isError", False)

    # MCP is read-only, so even a write key only sees the read tools.
    _, write = asyncio.run(create_api_key(name="ops", scopes="read,write"))
    names = {tool["name"] for tool in _rpc(client, "tools/list", headers={"X-API-Key": write})["result"]["tools"]}
    assert names == TOOL_NAMES


def test_mcp_requires_auth_when_credentials_exist():
    settings.admin_user = "admin"
    settings.admin_password = "s3cret"
    settings.secret = "test-secret"
    client = TestClient(app)

    body = _rpc(client, "initialize")
    assert "error" in body

    token = client.post("/api/login", json={"username": "admin", "password": "s3cret"}).json()["token"]
    ok = _rpc(client, "initialize", headers={"Authorization": f"Bearer {token}"})
    assert "result" in ok

    settings.admin_user = ""
    settings.admin_password = ""


def test_unknown_tool_and_method():
    client = _client()
    assert "error" in _rpc(client, "tools/call", {"name": "does_not_exist"})
    assert "error" in _rpc(client, "totally/unknown")


def test_tool_catalogue_endpoint():
    client = _client()
    tools = client.get("/api/mcp/tools").json()
    assert {tool["name"] for tool in tools} == TOOL_NAMES
    assert all(tool["scopes"] for tool in tools)
    info = client.get("/mcp").json()
    assert info["name"] == "feicms" and sorted(info["tools"]) == sorted(TOOL_NAMES)


def test_stdio_handler_is_reusable():
    response = asyncio.run(handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "ping"}, None))
    assert response == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert asyncio.run(handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)) is None
