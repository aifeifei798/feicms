from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings
from .models import Base

IS_SQLITE = settings.database_url.startswith("sqlite")


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a sqlite file so a fresh checkout works."""
    if not url.startswith("sqlite"):
        return
    database = make_url(url).database
    if not database or database == ":memory:":
        return
    path = Path(database)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool if IS_SQLITE else None,
    # sqlite default busy timeout is 5s; collection runs many concurrent writers,
    # so wait longer instead of failing with "database is locked".
    connect_args={"timeout": 30} if IS_SQLITE else {},
    future=True,
)

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

_WRITE_LOCK: asyncio.Lock | None = None
_WRITE_LOCK_LOOP = None


def write_lock():
    """Serialize SQLite writes so concurrent sources don't trip 'database is locked'.

    For non-sqlite backends this returns an async no-op context manager.
    """
    if not IS_SQLITE:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop():
            yield

        return _noop()

    global _WRITE_LOCK, _WRITE_LOCK_LOOP
    loop = asyncio.get_event_loop()
    if _WRITE_LOCK is None or _WRITE_LOCK_LOOP is not loop:
        _WRITE_LOCK = asyncio.Lock()
        _WRITE_LOCK_LOOP = loop
    return _WRITE_LOCK


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    if not IS_SQLITE:
        return
    cur = dbapi_connection.cursor()
    # Give a waiting writer 60s to acquire the lock before raising "database is locked".
    cur.execute("PRAGMA busy_timeout=60000")
    # WAL already set in init_db, but re-assert on every new connection is harmless.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s
        await s.commit()


async def init_db() -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        if IS_SQLITE:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_columns(conn)
        await _ensure_indexes(conn)


async def _ensure_columns(conn) -> None:
    """Tiny additive migration so older databases pick up new columns."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text

    rows = (await conn.execute(text("PRAGMA table_info(sources)"))).fetchall()
    existing = {row[1] for row in rows}
    for column, ddl in (
        ("origin", "TEXT NOT NULL DEFAULT 'manual'"),
        ("subscription_url", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE sources ADD COLUMN {column} {ddl}"))


async def _ensure_indexes(conn) -> None:
    """Create indexes added after a table already exists (create_all skips them)."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text

    for name, columns in (
        ("ix_videos_type_updated", "type_name, last_updated"),
        ("ix_videos_year_updated", "year, last_updated"),
    ):
        await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON videos ({columns})"))


async def dispose() -> None:
    await engine.dispose()
