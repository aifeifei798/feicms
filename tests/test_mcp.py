from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from feicms.api import app
from feicms.apikeys import create_api_key, delete_api_key, list_api_keys
from feicms.config import settings
from feicms.db import init_db
from feicms.mcp import TOOL_NAMES, handle_rpc


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
    assert "sources" in result["structuredContent"]

    search = _rpc(client, "tools/call", {"name": "search_videos", "arguments": {"limit": 5}})["result"]
    assert isinstance(search["structuredContent"]["items"], list)

    resources = _rpc(client, "resources/list")["result"]["resources"]
    assert any(r["uri"] == "feicms://stats" for r in resources)
    read = _rpc(client, "resources/read", {"uri": "feicms://stats"})["result"]["contents"][0]
    assert read["mimeType"] == "application/json"


def test_read_only_key_cannot_call_write_tools():
    client = _client()
    _, plain = asyncio.run(create_api_key(name="ai", scopes="read"))
    headers = {"X-API-Key": plain}

    ok = _rpc(client, "tools/call", {"name": "get_stats"}, headers)["result"]
    assert not ok.get("isError", False)

    denied = _rpc(client, "tools/call", {"name": "collect_sources"}, headers)["result"]
    assert denied["isError"] is True
    assert "权限" in denied["content"][0]["text"]


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


def test_stdio_handler_is_reusable():
    response = asyncio.run(handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "ping"}, None))
    assert response == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert asyncio.run(handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)) is None
