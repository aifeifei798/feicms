from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    """A resource site (采集站) that can be crawled."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(500), index=True)
    api_format: Mapped[str] = mapped_column(String(20), default="auto")  # auto | json | xml
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    max_pages: Mapped[int] = mapped_column(Integer, default=5)
    tags: Mapped[str] = mapped_column(String(200), default="")
    uptime: Mapped[float] = mapped_column(Float, default=100.0)
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # manual | subscription
    subscription_url: Mapped[str] = mapped_column(String(500), default="")

    last_collect_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_page: Mapped[int] = mapped_column(Integer, default=0)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health_status: Mapped[str] = mapped_column(String(20), default="unknown")  # ok | slow | dead | unknown
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_streak: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items: Mapped[list[SourceItem]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Source {self.id} {self.name} {self.health_status}>"


class Video(Base):
    """Merged video entry. One row per distinct title (deduped across sources)."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    signature: Mapped[str] = mapped_column(String(400), unique=True, index=True)
    type_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    type_id: Mapped[int] = mapped_column(Integer, default=0)
    year: Mapped[str] = mapped_column(String(10), default="", index=True)
    area: Mapped[str] = mapped_column(String(100), default="")
    lang: Mapped[str] = mapped_column(String(50), default="")
    category: Mapped[str] = mapped_column(String(120), default="", index=True)
    pic: Mapped[str] = mapped_column(String(600), default="")
    blurb: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(Text, default="")
    director: Mapped[str] = mapped_column(Text, default="")
    remarks: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[int] = mapped_column(Integer, default=1)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    items: Mapped[list[SourceItem]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class SourceItem(Base):
    """One raw record fetched from one source, attached to a merged video."""

    __tablename__ = "source_items"
    __table_args__ = (
        UniqueConstraint("source_id", "source_vod_id", name="uq_source_vod"),
        Index("ix_items_video", "video_id"),
        Index("ix_items_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    source_vod_id: Mapped[int] = mapped_column(Integer)

    name: Mapped[str] = mapped_column(String(300), default="")
    type_name: Mapped[str] = mapped_column(String(120), default="")
    type_id: Mapped[int] = mapped_column(Integer, default=0)
    year: Mapped[str] = mapped_column(String(10), default="")
    area: Mapped[str] = mapped_column(String(100), default="")
    lang: Mapped[str] = mapped_column(String(50), default="")
    pic: Mapped[str] = mapped_column(String(600), default="")
    blurb: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(Text, default="")
    director: Mapped[str] = mapped_column(Text, default="")
    remarks: Mapped[str] = mapped_column(String(200), default="")
    vod_time: Mapped[str] = mapped_column(String(30), default="")

    play_from: Mapped[str] = mapped_column(String(300), default="")
    play_url: Mapped[str] = mapped_column(Text, default="")
    # parsed: [{"from": "example", "episodes": [{"name": "第01集", "url": "https://..."}]}]
    plays: Mapped[str] = mapped_column(Text, default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    source: Mapped[Source] = relationship(back_populates="items")
    video: Mapped[Video] = relationship(back_populates="items")


class SourceType(Base):
    """type_id -> type_name map discovered from a source (ac=types)."""

    __tablename__ = "source_types"
    __table_args__ = (UniqueConstraint("source_id", "type_id", name="uq_source_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    type_id: Mapped[int] = mapped_column(Integer)
    type_name: Mapped[str] = mapped_column(String(120), default="")


class CollectJob(Base):
    """Audit trail for every collection run (useful for maintenance/debugging)."""

    __tablename__ = "collect_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Subscription(Base):
    """A remote source list (订阅) that keeps the `sources` table up to date.

    Example feeds: ziyuanzu-cms-interfaces.json / .txt
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="auto")  # json | txt | auto
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_added: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[int] = mapped_column(Integer, default=0)
    last_entries: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Subscription {self.id} {self.url}>"


class ApiKey(Base):
    """A long-lived key for programmatic / AI access to the API.

    Only the sha256 of the key is stored; the plaintext is shown once when the
    key is created. Scopes: `read` (query only) and `write` (mutating endpoints).
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_key_hash"),
        Index("ix_api_key_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="key")
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    prefix: Mapped[str] = mapped_column(String(12), default="")  # first chars, for display
    scopes: Mapped[str] = mapped_column(String(60), default="read")  # comma separated
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ApiKey {self.id} {self.name} {self.prefix}… {self.scopes}>"


class Setting(Base):
    """Small key/value store for runtime maintenance flags."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Image(Base):
    """A poster/thumbnail (vod_pic) queued for local download.

    Files live under settings.image_dir sharded by the sha256 of the normalised
    URL: <dir>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>. The database is the index; the
    filesystem is only ever addressed through `path`.
    """

    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("url", name="uq_image_url"),
        Index("ix_image_status", "status"),
        Index("ix_image_sha", "sha256"),
        Index("ix_image_seen", "first_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(600), index=True)
    host: Mapped[str] = mapped_column(String(200), default="", index=True)

    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | downloaded | duplicate | failed
    path: Mapped[str] = mapped_column(String(400), default="")
    ext: Mapped[str] = mapped_column(String(8), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(60), default="")

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
