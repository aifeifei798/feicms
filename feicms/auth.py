"""Authentication for the maintenance API, the DaisyUI panel and the MCP server.

Credentials come from .env:

    FEICMS_ADMIN_USER      empty  ->  admin login disabled
    FEICMS_ADMIN_PASSWORD  required together with the user
    FEICMS_SECRET          token signing key (defaults to user+password)
    FEICMS_TOKEN_TTL       token lifetime in seconds (default 24h)

Two kinds of clients:

    admin   Authorization: Bearer <token from /api/login>
            Authorization: Basic <user:pass>
            Cookie: feicms_token=<token>          (browser UI)

    api key X-API-Key: fk_...            (or Authorization: Bearer fk_...)
            keys are managed with `feicms apikey ...` or the panel; only their
            sha256 is stored, and they carry a scope: read | write

If neither admin credentials nor any API key exists, the API stays open (handy
for local development).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from .apikeys import has_active_keys, lookup_key, parse_scopes
from .config import settings

TOKEN_COOKIE = "feicms_token"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    """Who is calling us and what they are allowed to do."""

    kind: str  # admin | apikey | anonymous
    name: str
    scopes: set[str] = field(default_factory=lambda: {"read", "write"})

    def can_write(self) -> bool:
        return self.kind != "apikey" or "write" in self.scopes


def auth_enabled() -> bool:
    """Admin login is on only when both user and password are configured."""
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
    return hmac.compare_digest(username, settings.admin_user) and hmac.compare_digest(
        password, settings.admin_password
    )


async def authenticate(request: Request) -> Principal | None:
    """Resolve the caller from headers/cookies. Returns None when unauthenticated."""
    header = request.headers.get("authorization", "")
    lowered = header.lower()

    api_key = request.headers.get("x-api-key", "").strip()
    if not api_key and lowered.startswith("bearer "):
        candidate = header[7:].strip()
        if candidate.startswith("fk_"):
            api_key = candidate
        else:
            user = decode_token(candidate)
            if user:
                return Principal("admin", user)

    if not api_key and lowered.startswith("basic "):
        try:
            raw = base64.b64decode(header[6:].strip()).decode("utf-8")
        except Exception:
            raw = ""
        user, _, password = raw.partition(":")
        if check_credentials(user, password):
            return Principal("admin", user)

    if api_key:
        row = await lookup_key(api_key)
        if row is not None:
            return Principal("apikey", row.name, parse_scopes(row))
        return None

    user = decode_token(request.cookies.get(TOKEN_COOKIE, ""))
    if user:
        return Principal("admin", user)
    return None


async def require_auth(request: Request) -> Principal:
    """FastAPI dependency: authenticated principal, or 401/403."""
    principal = await authenticate(request)
    if principal is None:
        if not auth_enabled() and not await has_active_keys():
            return Principal("anonymous", "anonymous")
        raise HTTPException(
            status_code=401,
            detail="未授权：需要登录（POST /api/login）或 API key（X-API-Key: fk_...）",
            headers={"WWW-Authenticate": "Basic"},
        )
    if request.method in WRITE_METHODS and not principal.can_write():
        raise HTTPException(403, f"API key '{principal.name}' 只有 read 权限，不能执行 {request.method}")
    return principal


async def require_admin(request: Request) -> Principal:
    """Dependency for endpoints that manage keys/credentials themselves."""
    principal = await require_auth(request)
    if principal.kind != "admin":
        raise HTTPException(403, "只有管理员账号（FEICMS_ADMIN_USER）可以管理 API key")
    return principal
