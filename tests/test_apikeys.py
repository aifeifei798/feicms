from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from feicms.api import app
from feicms.apikeys import create_api_key, delete_api_key, hash_key, is_expired, list_api_keys
from feicms.config import settings
from feicms.db import init_db


def _reset() -> TestClient:
    """Start from a clean slate: no admin credentials, no API keys."""
    settings.admin_user = ""
    settings.admin_password = ""
    settings.secret = ""

    async def prepare() -> None:
        await init_db()
        for row in await list_api_keys():
            await delete_api_key(row.id)

    asyncio.run(prepare())
    return TestClient(app)


def test_key_scopes_and_write_guard():
    client = _reset()

    read_row, read_plain = asyncio.run(create_api_key(name="ai-read", scopes="read"))
    assert read_plain.startswith("fk_")

    assert client.get("/api/stats", headers={"X-API-Key": read_plain}).status_code == 200
    assert client.get("/api/sources", headers={"Authorization": f"Bearer {read_plain}"}).status_code == 200

    # no credentials at all: a key exists, so the API is no longer open
    assert client.post("/api/sources", json={"name": "should-fail", "url": "https://example.com/"}).status_code == 401

    # read-only keys may not mutate
    assert (
        client.post(
            "/api/sources",
            json={"name": "should-fail", "url": "https://example.com/"},
            headers={"X-API-Key": read_plain},
        ).status_code
        == 403
    )

    write_row, write_plain = asyncio.run(create_api_key(name="automation", scopes="write"))
    assert (
        client.post(
            "/api/sources",
            json={"name": "write-key-source", "url": "https://example.com/api.php/provide/vod/"},
            headers={"X-API-Key": write_plain},
        ).status_code
        == 201
    )
    assert read_row.id != write_row.id


def test_keys_are_hashed_and_not_recoverable():
    client = _reset()
    row, plain = asyncio.run(create_api_key(name="hash-check", scopes="read,write"))
    assert row.key_hash == hash_key(plain)
    assert plain not in str(row)

    # an API key may not manage API keys
    assert client.get("/api/apikeys", headers={"X-API-Key": plain}).status_code == 403

    settings.admin_user = "admin"
    settings.admin_password = "s3cret"
    settings.secret = "test-secret"
    token = client.post("/api/login", json={"username": "admin", "password": "s3cret"}).json()["token"]
    listed = client.get("/api/apikeys", headers={"Authorization": f"Bearer {token}"}).json()
    assert any(item["prefix"] == plain[:8] for item in listed)
    assert all("key_hash" not in item for item in listed)


def test_admin_can_manage_keys_and_expired_keys_are_rejected():
    client = _reset()
    settings.admin_user = "admin"
    settings.admin_password = "s3cret"
    settings.secret = "test-secret"
    token = client.post("/api/login", json={"username": "admin", "password": "s3cret"}).json()["token"]

    created = client.post(
        "/api/apikeys",
        json={"name": "automation", "scopes": "write", "expires_days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    key, key_id = created.json()["key"], created.json()["id"]

    assert client.get("/api/stats", headers={"X-API-Key": key}).status_code == 200

    disabled = client.patch(
        f"/api/apikeys/{key_id}", json={"enabled": False}, headers={"Authorization": f"Bearer {token}"}
    )
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    assert client.get("/api/stats", headers={"X-API-Key": key}).status_code == 401

    assert client.delete(f"/api/apikeys/{key_id}", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert client.get("/api/stats", headers={"X-API-Key": key}).status_code == 401

    expired_row, expired_key = asyncio.run(create_api_key(name="expired", scopes="read", expires_days=-1))
    assert is_expired(expired_row, datetime.now(timezone.utc))
    assert client.get("/api/stats", headers={"X-API-Key": expired_key}).status_code == 401

    settings.admin_user = ""
    settings.admin_password = ""


def test_bad_scope_is_rejected():
    try:
        asyncio.run(create_api_key(name="bad", scopes="read,admin"))
    except ValueError as exc:
        assert "admin" in str(exc)
    else:
        raise AssertionError("invalid scope should raise")
