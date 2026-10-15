from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from feicms.api import app
from feicms.config import settings
from feicms.db import init_db

asyncio.run(init_db())


def _enable_auth() -> None:
    settings.admin_user = "admin"
    settings.admin_password = "s3cret"
    settings.secret = "test-secret"


def _disable_auth() -> None:
    settings.admin_user = ""
    settings.admin_password = ""
    settings.secret = ""


def test_auth_disabled_by_default():
    _disable_auth()
    client = TestClient(app)
    assert client.get("/api/auth/status").json() == {
        "auth_required": False,
        "authenticated": False,
        "user": None,
        "kind": None,
        "scopes": [],
    }
    assert client.get("/api/stats").status_code == 200


def test_login_and_protected_endpoints():
    _enable_auth()
    client = TestClient(app)

    assert client.get("/api/stats").status_code == 401

    status = client.get("/api/auth/status").json()
    assert status["auth_required"] is True
    assert status["authenticated"] is False

    response = client.post("/api/login", json={"username": "admin", "password": "s3cret"})
    assert response.status_code == 200
    token = response.json()["token"]
    assert token

    assert client.get("/api/stats", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/api/sources", headers={"Authorization": f"Basic YWRtaW46czNjcmV0"}).status_code == 200

    # cookie set by /api/login is enough for the browser UI
    cookie_client = TestClient(app)
    cookie_client.post("/api/login", json={"username": "admin", "password": "s3cret"})
    assert cookie_client.get("/api/stats").status_code == 200


def test_wrong_credentials_and_bad_token():
    _enable_auth()
    client = TestClient(app)
    assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Bearer not-a-valid-token"}).status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Basic bm90LXVzZXI6Ym9n"}).status_code == 401


def test_token_is_signed_and_expires():
    _enable_auth()
    from feicms.auth import decode_token, make_token

    token = make_token("admin", ttl=60)
    assert decode_token(token) == "admin"
    assert decode_token(token + "x") is None
    assert decode_token(make_token("admin", ttl=-10)) is None
    assert decode_token("garbage") is None

    _disable_auth()
