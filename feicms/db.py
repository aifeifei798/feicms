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
from .models import Base, utcnow

IS_SQLITE = settings.database_url.startswith("sqlite")
FTS_READY = False


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


async def init_db() -> list[str]:
    """Create anything the schema is missing. Returns the duplicate indexes removed."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        if IS_SQLITE:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_columns(conn)
        await _ensure_indexes(conn)
        dropped = await _drop_redundant_indexes(conn)
        await _ensure_fts(conn)

    # `videos.is_adult` is what the front site filters on; keep it in sync with
    # the configured keyword lists (one scan, only when they changed).
    from .adult import refresh_adult_flags

    await refresh_adult_flags()
    return dropped


async def analyze() -> None:
    """Refresh planner statistics - cheap, and keeps LIMIT queries on the index."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("ANALYZE"))


async def rebuild_fts() -> None:
    """Rebuild the title search index from scratch (one pass over the library)."""
    if not IS_SQLITE:
        return
    async with engine.begin() as conn:
        await _ensure_fts(conn, force=True)


async def _ensure_columns(conn) -> None:
    """Tiny additive migration so older databases pick up new columns."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text

    for table, column, ddl in (
        ("sources", "origin", "TEXT NOT NULL DEFAULT 'manual'"),
        ("sources", "subscription_url", "TEXT NOT NULL DEFAULT ''"),
        ("videos", "is_adult", "INTEGER NOT NULL DEFAULT 0"),
        ("source_items", "last_checked_at", "TEXT"),
    ):
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def _ensure_indexes(conn) -> None:
    """Create indexes added after a table already exists (create_all skips them)."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text

    for table, name, columns in (
        ("videos", "ix_videos_type_updated", "type_name, last_updated"),
        ("videos", "ix_videos_year_updated", "year, last_updated"),
        ("videos", "ix_videos_adult_updated", "is_adult, last_updated"),
        ("videos", "ix_videos_adult_type_updated", "is_adult, type_name, last_updated"),
        ("videos", "ix_videos_adult_source", "is_adult, source_count"),
        ("source_items", "ix_items_checked", "last_checked_at"),
    ):
        await conn.execute(
            text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
        )


# Indexes that used to be created twice (once by `index=True` on the column, once
# explicitly, or alongside a UNIQUE constraint). Each pair indexes exactly the same
# columns, so one of them is pure disk and write overhead. `keeper` is the index that
# stays: a dropped index is only removed when its twin is present, so a database that
# never had the twin keeps its only index.
_REDUNDANT_INDEXES = (
    ("source_items", "ix_source_items_video_id", "ix_items_video"),
    # `uq_source_vod UNIQUE (source_id, source_vod_id)` already covers source_id as
    # its leftmost column, so the single-column index is a second copy of a prefix.
    ("source_items", "ix_source_items_source_id", "sqlite_autoindex_source_items_1"),
    ("images", "ix_image_status", "ix_images_status"),
    ("images", "ix_images_url", "sqlite_autoindex_images_1"),
    ("sources", "ix_sources_name", "sqlite_autoindex_sources_1"),
    ("subscriptions", "ix_subscriptions_url", "sqlite_autoindex_subscriptions_1"),
    ("api_keys", "ix_api_keys_key_hash", "sqlite_autoindex_api_keys_1"),
    # A composite index can serve a query on its leftmost column, so the single-column
    # index is only a shorter copy of the same key. `_ensure_indexes` runs first, so
    # the composite is always present when these are dropped.
    ("videos", "ix_videos_type_name", "ix_videos_type_updated"),
    ("videos", "ix_videos_year", "ix_videos_year_updated"),
)


async def _drop_redundant_indexes(conn) -> list[str]:
    """Remove duplicate indexes left over from older schemas."""
    if not IS_SQLITE:
        return []
    from sqlalchemy import text

    existing = {
        row[0]
        for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
    }
    dropped: list[str] = []
    for _table, name, keeper in _REDUNDANT_INDEXES:
        if name in existing and keeper in existing:
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
            dropped.append(name)
    return dropped


async def _ensure_fts(conn, force: bool = False) -> None:
    """Full-text index over titles, so title search is an index seek.

    `name LIKE '%q%'` scans the whole library; the FTS5 trigram index answers the
    same query in under a millisecond. Triggers keep it in sync, and only re-index
    a row when the title actually changed, so collection stays cheap.
    Queries shorter than 3 characters cannot use trigrams and fall back to LIKE.
    """
    global FTS_READY
    if not IS_SQLITE:
        FTS_READY = False
        return
    from sqlalchemy import text

    try:
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5("
                "name, content='videos', content_rowid='id', tokenize='trigram')"
            )
        )
    except Exception:  # noqa: BLE001 - SQLite too old for the trigram tokenizer
        FTS_READY = False
        return

    for trigger, body in (
        (
            "videos_fts_ai",
            "AFTER INSERT ON videos BEGIN "
            "INSERT INTO videos_fts(rowid, name) VALUES (new.id, new.name); END",
        ),
        (
            "videos_fts_ad",
            "AFTER DELETE ON videos BEGIN "
            "INSERT INTO videos_fts(videos_fts, rowid, name) VALUES ('delete', old.id, old.name); END",
        ),
        (
            "videos_fts_au",
            "AFTER UPDATE ON videos WHEN old.name IS NOT new.name BEGIN "
            "INSERT INTO videos_fts(videos_fts, rowid, name) VALUES ('delete', old.id, old.name); "
            "INSERT INTO videos_fts(rowid, name) VALUES (new.id, new.name); END",
        ),
    ):
        await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {trigger} {body}"))

    # An external-content FTS table reads its rows from `videos`, so counting them
    # says nothing about the inverted index. The settings row is the marker that
    # the one-off build already happened; after that the triggers keep it in sync.
    built = (
        await conn.execute(text("SELECT value FROM settings WHERE key = 'fts_index_built'"))
    ).scalar()
    if built is None or force:
        await conn.execute(text("INSERT INTO videos_fts(videos_fts) VALUES ('rebuild')"))
        indexed = (await conn.execute(text("SELECT count(*) FROM videos"))).scalar()
        await conn.execute(
            text(
                "INSERT INTO settings (key, value, updated_at) VALUES ('fts_index_built', :v, :t) "
                "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = :t"
            ),
            {"v": str(indexed), "t": utcnow()},
        )
    FTS_READY = True


async def dispose() -> None:
    await engine.dispose()
