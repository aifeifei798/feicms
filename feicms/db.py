from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings
from .models import Base

IS_SQLITE = settings.database_url.startswith("sqlite")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool if IS_SQLITE else None,
    future=True,
)

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


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


async def dispose() -> None:
    await engine.dispose()
