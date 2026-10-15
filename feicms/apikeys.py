"""API key management for programmatic and AI access.

Keys look like `fk_<43 url-safe chars>`. Only the sha256 hash is stored, so a
leaked database does not leak usable keys. The plaintext is returned once, at
creation time.

Scopes
    read   - query endpoints only (sources, videos, stats, images list)
    write  - everything, including collect / health-check / prune / image download

A key without `write` gets 403 on POST/PUT/PATCH/DELETE.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import session
from .models import ApiKey

VALID_SCOPES = ("read", "write")
KEY_PREFIX = "fk_"


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize_scopes(scopes: str | list[str] | None) -> str:
    if scopes is None:
        return "read"
    if isinstance(scopes, str):
        items = scopes.split(",")
    else:
        items = list(scopes)
    cleaned = {item.strip().lower() for item in items if item.strip()}
    unknown = cleaned - set(VALID_SCOPES)
    if unknown:
        raise ValueError(f"未知 scope: {', '.join(sorted(unknown))}（可用: {', '.join(VALID_SCOPES)}）")
    if not cleaned:
        cleaned = {"read"}
    return ",".join(sorted(cleaned))


def parse_scopes(row: ApiKey) -> set[str]:
    return {item.strip() for item in (row.scopes or "").split(",") if item.strip()}


def is_expired(row: ApiKey, now: datetime | None = None) -> bool:
    if row.expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return expires < now


async def create_api_key(
    name: str = "key",
    scopes: str | list[str] = "read",
    expires_days: int | None = None,
) -> tuple[ApiKey, str]:
    """Create a key. Returns (row, plaintext_key)."""
    plaintext = generate_key()
    row = ApiKey(
        name=name or "key",
        key_hash=hash_key(plaintext),
        prefix=plaintext[:8],
        scopes=normalize_scopes(scopes),
        enabled=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None,
    )
    async with session() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row, plaintext


async def list_api_keys(include_disabled: bool = True) -> list[ApiKey]:
    async with session() as s:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
        if not include_disabled:
            stmt = stmt.where(ApiKey.enabled.is_(True))
        return list(await s.scalars(stmt))


async def get_api_key(key_id: int) -> ApiKey | None:
    async with session() as s:
        return await s.get(ApiKey, key_id)


async def update_api_key(
    key_id: int,
    name: str | None = None,
    scopes: str | list[str] | None = None,
    enabled: bool | None = None,
    expires_days: int | None = None,
) -> ApiKey | None:
    async with session() as s:
        row = await s.get(ApiKey, key_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if scopes is not None:
            row.scopes = normalize_scopes(scopes)
        if enabled is not None:
            row.enabled = enabled
        if expires_days is not None:
            row.expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None
        await s.commit()
        await s.refresh(row)
        return row


async def delete_api_key(key_id: int) -> bool:
    async with session() as s:
        row = await s.get(ApiKey, key_id)
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
        return True


async def lookup_key(plaintext: str) -> ApiKey | None:
    """Resolve a plaintext key, touching last_used_at/usage_count."""
    if not plaintext:
        return None
    async with session() as s:
        row = await s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(plaintext)))
        if row is None or not row.enabled or is_expired(row):
            return None
        row.last_used_at = datetime.now(timezone.utc)
        row.usage_count += 1
        await s.commit()
        await s.refresh(row)
        return row


async def has_active_keys() -> bool:
    async with session() as s:
        rows = list(await s.scalars(select(ApiKey).where(ApiKey.enabled.is_(True))))
    return any(not is_expired(row) for row in rows)
