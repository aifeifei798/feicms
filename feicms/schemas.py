from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=4)
    api_format: str = "auto"
    enabled: bool = True
    priority: int = 0
    max_pages: int = 5
    tags: str = ""
    uptime: float = 100.0


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    api_format: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    max_pages: Optional[int] = None
    tags: Optional[str] = None
    uptime: Optional[float] = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    api_format: str
    enabled: bool
    priority: int
    max_pages: int
    tags: str
    uptime: float
    origin: str
    subscription_url: str
    health_status: str
    latency_ms: Optional[int]
    last_collect_at: Optional[datetime]
    last_health_at: Optional[datetime]
    last_error: Optional[str]
    fail_streak: int
    item_count: int
    created_at: datetime
    updated_at: datetime


class EpisodeOut(BaseModel):
    name: str
    url: str


class PlayOut(BaseModel):
    from_name: str
    episodes: list[EpisodeOut]


class SourceItemOut(BaseModel):
    source_id: int
    source_name: str
    source_vod_id: int
    play_from: str
    episodes: list[EpisodeOut]


class VideoOut(BaseModel):
    id: int
    name: str
    type_name: str
    year: str
    area: str
    lang: str
    pic: str
    local_pic: Optional[str] = None
    blurb: str
    actor: str
    director: str
    remarks: str
    source_count: int
    last_updated: datetime
    plays: list[PlayOut]


class VideoSummary(BaseModel):
    id: int
    name: str
    type_name: str
    year: str
    pic: str
    local_pic: Optional[str] = None
    remarks: str
    source_count: int
    last_updated: datetime


class Page(BaseModel):
    total: int
    page: int
    limit: int
    items: list[Any]


class CollectRequest(BaseModel):
    source_ids: Optional[list[int]] = None
    names: Optional[list[str]] = None
    # None = per-source max_pages, >0 = exact page count, 0 = full sweep of the source.
    pages: Optional[int] = Field(None, ge=0, le=500)
    all: bool = False
    # 断点续传 for a full sweep (pages=0): None = follow FEICMS_RESUME, False = start over.
    resume: Optional[bool] = None


class EnableRequest(BaseModel):
    enabled: bool = True


class SourceImportRequest(BaseModel):
    """Bulk source list, pasted as `name|url|format|uptime` lines (or YAML/JSON)."""

    text: str
    max_pages: Optional[int] = Field(None, ge=0, le=500)
    enabled: bool = True


class SchedulerUpdate(BaseModel):
    auto_collect: Optional[bool] = None
    auto_collect_full: Optional[bool] = None
    auto_images: Optional[bool] = None
    collect_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    health_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    image_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)


class AdminPasswordRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None


class AdultFilterUpdate(BaseModel):
    hide_adult: Optional[bool] = None
    type_keywords: Optional[list[str]] = None
    name_keywords: Optional[list[str]] = None


class Stats(BaseModel):
    sources: int
    enabled_sources: int
    healthy_sources: int
    videos: int
    source_items: int
    last_collect_at: Optional[datetime]
    by_source: list[dict[str, Any]]
    by_type: list[dict[str, Any]]


class HealthReport(BaseModel):
    checked: int
    ok: int
    dead: int
    results: list[dict[str, Any]]


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    host: str
    status: str
    path: str
    ext: str
    sha256: str
    size_bytes: int
    attempts: int
    last_error: Optional[str]
    first_seen: datetime
    downloaded_at: Optional[datetime]


class ImageDownloadRequest(BaseModel):
    limit: Optional[int] = Field(None, ge=1, le=5000)
    concurrency: Optional[int] = Field(None, ge=1, le=32)
    retry_failed: bool = False


class SubscriptionCreate(BaseModel):
    url: str = Field(min_length=4)
    kind: str = "auto"
    enabled: bool = True


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    kind: str
    enabled: bool
    last_sync_at: Optional[datetime]
    last_added: int
    last_updated: int
    last_entries: int
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ApiKeyCreate(BaseModel):
    name: str = Field(default="key", min_length=1, max_length=120)
    scopes: str = Field(default="read", description="read | write | read,write")
    expires_days: Optional[int] = None


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    scopes: Optional[str] = None
    enabled: Optional[bool] = None
    expires_days: Optional[int] = None


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scopes: str
    enabled: bool
    expires_at: Optional[datetime]
    created_at: datetime
    last_used_at: Optional[datetime]
    usage_count: int
