"""Admin authentication for the maintenance API and the DaisyUI panel.

Credentials come from .env:

    FEICMS_ADMIN_USER      empty  ->  authentication is disabled
    FEICMS_ADMIN_PASSWORD  required together with the user to enable it
    FEICMS_SECRET          token signing key (defaults to user+password)
    FEICMS_TOKEN_TTL       token lifetime in seconds (default 24h)

A client authenticates with either

    Authorization: Bearer <token>        (token from POST /api/login)
    Authorization: Basic <user:pass>     (convenient for curl)
    Cookie: feicms_token=<token>         (set by /api/login, used by the browser UI)

Tokens are HMAC-SHA256 signed (stdlib only, no extra dependency).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from .config import settings

TOKEN_COOKIE = "feicms_token"


def auth_enabled() -> bool:
    """Authentication is on only when both user and password are configured."""
    return bool(settings.admin_user and settings.admin_password)


def _secret() -> bytes:
    return (settings.secret or f"{settings.admin_user}|{settings.admin_password}").encode("utf-8")


def make_token(user: str, ttl: int | None = None) -> str:
    expires = int(time.time()) + (ttl or settings.token_ttl)
    payload = f"{user}|{expires}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def decode_token(token: str) -> str | None:
    """Return the username encoded in a valid, unexpired token, else None."""
    if not token:
        return None
    try:
        payload, signature = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    user, _, expires = payload.partition("|")
    if not user:
        return None
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    return user


def check_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.admin_user) and hmac.compare_digest(password, settings.admin_password)


def _user_from_header(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    lowered = header.lower()
    if lowered.startswith("bearer "):
        return decode_token(header[7:].strip())
    if lowered.startswith("basic "):
        try:
            raw = base64.b64decode(header[6:].strip()).decode("utf-8")
        except Exception:
            return None
        user, _, password = raw.partition(":")
        return user if check_credentials(user, password) else None
    return None


def current_user(request: Request) -> str | None:
    user = _user_from_header(request)
    if user:
        return user
    return decode_token(request.cookies.get(TOKEN_COOKIE, ""))


def require_auth(request: Request) -> str:
    """FastAPI dependency: returns the authenticated user, or 401 when needed."""
    if not auth_enabled():
        return "anonymous"
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="未授权：需要登录（POST /api/login，或 Authorization: Basic/Bearer）",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user
