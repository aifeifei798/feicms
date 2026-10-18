"""Storage helpers for the columns that dominate the database file.

`source_items.plays` is a JSON array of play sources and their episodes. With a
library of hundreds of thousands of titles it holds tens of millions of episode
URLs, and the JSON scaffolding (`{"name": "...", "url": "..."}`) is a large part
of it. Episode URLs repeat the same host/path patterns over and over, so the
column compresses to roughly a sixth of its size with zlib.

The type decorator keeps the Python side unchanged: code still reads and writes
plain JSON strings. Only the bytes on disk are compressed.
"""

from __future__ import annotations

import zlib
from pathlib import Path

from sqlalchemy import Text
from sqlalchemy.engine import make_url
from sqlalchemy.types import TypeDecorator

from .config import settings

# Values shorter than this are stored as-is: compressing a 20-byte string makes it
# *bigger*, and keeping the small ones plain means SQL predicates such as
# `plays == "[]"` keep working.
MIN_COMPRESS_BYTES = 64
COMPRESS_LEVEL = 6


def database_path() -> str | None:
    """Path of the SQLite file, or None for in-memory / other backends."""
    if not settings.database_url.startswith("sqlite"):
        return None
    database = make_url(settings.database_url).database
    if not database or database == ":memory:":
        return None
    return database


def database_bytes() -> int:
    """Size of the database file on disk (0 when there is no file)."""
    path = database_path()
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def compress_text(value: str) -> bytes | str:
    """Return `value` compressed, or the original string when packing does not help.

    Short values are left as plain text on purpose: compressing a 2-byte string
    makes it bigger, and keeping them plain means SQL predicates such as
    `plays == "[]"` keep working.
    """
    data = value.encode("utf-8")
    if len(data) < MIN_COMPRESS_BYTES:
        return value
    packed = zlib.compress(data, COMPRESS_LEVEL)
    return packed if len(packed) < len(data) else value


def decompress_text(value: bytes | str) -> str:
    """Return the original text, tolerating rows that were never packed."""
    if isinstance(value, str):
        return value
    try:
        return zlib.decompress(value).decode("utf-8")
    except zlib.error:
        # Rows written before packing existed are plain UTF-8 bytes.
        return value.decode("utf-8", "replace")


class CompressedText(TypeDecorator):
    """Text column stored zlib-compressed on SQLite, transparent to callers.

    Other backends keep the value as plain text: a binary blob in a TEXT column is
    not portable, and they do not have the same per-column disk pressure.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name != "sqlite":
            return value if isinstance(value, str) else str(value)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        return compress_text(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decompress_text(value)


async def packing_report() -> dict[str, int]:
    """How much of `source_items.plays` is already compressed, and how big it is."""
    from sqlalchemy import text

    from .db import session

    if not settings.database_url.startswith("sqlite"):
        return {"rows": 0, "packed": 0, "plain": 0, "stored_bytes": 0}

    async with session() as s:
        row = (
            await s.execute(
                text(
                    "SELECT count(*), "
                    "SUM(CASE WHEN typeof(plays) = 'blob' THEN 1 ELSE 0 END), "
                    "COALESCE(SUM(length(plays)), 0) "
                    "FROM source_items"
                )
            )
        ).fetchone()
    rows, packed, stored = row[0] or 0, row[1] or 0, row[2] or 0
    return {"rows": rows, "packed": packed, "plain": rows - packed, "stored_bytes": stored}
