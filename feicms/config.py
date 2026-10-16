from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env (project root) if python-dotenv is available, so local and container
# deployments can be configured the same way.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - optional dependency
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_csv(name: str) -> tuple[str, ...] | None:
    """Comma separated list. Returns None when the variable is not set at all,
    so callers can tell "not configured" apart from "explicitly empty"."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = _env("FEICMS_DB", f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'feicms.db'}")
    sources_file: str = _env("FEICMS_SOURCES", str(BASE_DIR / "sources.yaml"))
    subscriptions: list[str] = field(
        default_factory=lambda: [
            url.strip()
            for url in _env("FEICMS_SUBSCRIPTIONS", "").split(",")
            if url.strip()
        ]
    )

    # HTTP
    request_timeout: float = float(_env("FEICMS_TIMEOUT", "20"))
    impersonate: str = _env("FEICMS_IMPERSONATE", "chrome124")
    request_delay: float = float(_env("FEICMS_DELAY", "0.3"))
    user_agent: str = _env(
        "FEICMS_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0 Safari/537.36",
    )
    # How many sources are collected at the same time. 0 = no ceiling: every
    # source is a different site, so they are all walked in parallel (one
    # connection each). Set a number only if a host or network can't cope.
    concurrency: int = _env_int("FEICMS_CONCURRENCY", 0)
    max_retries: int = _env_int("FEICMS_RETRIES", 3)

    # Collection
    default_pages: int = _env_int("FEICMS_PAGES", 5)
    page_limit: int = _env_int("FEICMS_PAGE_LIMIT", 40)
    # Safety brake for a "full" sweep (pages=0). A sweep walks every page the
    # source reports; this only caps sources that report no pagecount at all.
    # 0 = no ceiling, i.e. a real full sweep of every page.
    full_page_cap: int = _env_int("FEICMS_FULL_PAGE_CAP", 0)
    # Lock file so a CLI sweep (`feicms collect --full`) and the service's own
    # auto-collect never walk the same database at the same time.
    lock_file: str = _env("FEICMS_LOCK_FILE", str(BASE_DIR / "data" / "collect.lock"))
    lock_ttl_hours: float = float(_env("FEICMS_LOCK_TTL", "12"))
    # Live snapshot of a running sweep so `./server.sh full-status` can show
    # progress without tailing the log.
    status_file: str = _env("FEICMS_STATUS_FILE", str(BASE_DIR / "data" / "collect_status.json"))
    # 断点续传: per-source checkpoint of a long `--full` sweep. An interrupted run
    # (reboot, Ctrl-C, killed process) resumes from the recorded page instead of
    # walking hundreds of thousands of pages again. `resume` gates the whole
    # feature; the CLI/panel can still override it per run.
    resume_file: str = _env("FEICMS_RESUME_FILE", str(BASE_DIR / "data" / "collect_resume.json"))
    resume: bool = _env_bool("FEICMS_RESUME", True)

    # Images (vod_pic posters). Files are sharded by sha256 of the image bytes:
    #   <image_dir>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>
    image_dir: str = _env("FEICMS_IMAGE_DIR", str(BASE_DIR / "data" / "images"))
    image_batch: int = _env_int("FEICMS_IMAGE_BATCH", 300)
    image_concurrency: int = _env_int("FEICMS_IMAGE_CONCURRENCY", 8)
    image_delay: float = float(_env("FEICMS_IMAGE_DELAY", "0.15"))
    image_max_size: int = _env_int("FEICMS_IMAGE_MAX_SIZE", 2_000_000)
    image_auto: bool = _env_bool("FEICMS_IMAGE_AUTO", True)
    image_interval_minutes: int = _env_int("FEICMS_IMAGE_INTERVAL", 30)
    auto_collect: bool = _env_bool("FEICMS_AUTO_COLLECT", True)
    # When true the periodic auto-collect walks every page each source reports
    # instead of only FEICMS_PAGES. Useful on a server that is meant to keep
    # sweeping in the background without anyone opening the panel.
    auto_collect_full: bool = _env_bool("FEICMS_AUTO_COLLECT_FULL", False)
    collect_interval_minutes: int = _env_int("FEICMS_COLLECT_INTERVAL", 60)
    health_interval_minutes: int = _env_int("FEICMS_HEALTH_INTERVAL", 30)
    health_fail_threshold: int = _env_int("FEICMS_HEALTH_THRESHOLD", 3)
    subscribe_interval_minutes: int = _env_int("FEICMS_SUBSCRIBE_INTERVAL", 1440)

    # API
    host: str = _env("FEICMS_HOST", "0.0.0.0")
    port: int = _env_int("FEICMS_PORT", 8000)

    # Admin authentication. Both user and password must be set for auth to turn on.
    admin_user: str = _env("FEICMS_ADMIN_USER", "")
    admin_password: str = _env("FEICMS_ADMIN_PASSWORD", "")
    secret: str = _env("FEICMS_SECRET", "")
    token_ttl: int = _env_int("FEICMS_TOKEN_TTL", 24 * 3600)
    # /provide/vod is the feed a downstream CMS collects; keep it open by default.
    public_provide: bool = _env_bool("FEICMS_PUBLIC_PROVIDE", True)
    # The public browsing front end (`/`) reads /api/site/* which is read-only:
    # it can never touch sources, collection or configuration. Set false to make
    # the front site require a login/API key as well.
    public_site: bool = _env_bool("FEICMS_PUBLIC_SITE", True)

    # Adult-content filter for the public front site. When on, records whose
    # category or title matches the keyword lists are not listed. The lists are
    # the defaults in feicms/adult.py and can be replaced from the panel or .env.
    hide_adult: bool = _env_bool("FEICMS_HIDE_ADULT", True)
    adult_type_keywords: tuple[str, ...] | None = field(default_factory=lambda: _env_csv("FEICMS_ADULT_TYPE_KEYWORDS"))
    adult_name_keywords: tuple[str, ...] | None = field(default_factory=lambda: _env_csv("FEICMS_ADULT_NAME_KEYWORDS"))

    settings: dict = field(default_factory=dict)


settings = Settings()


def env_file_path() -> Path:
    """Where the .env lives. Override with FEICMS_ENV_FILE for unusual layouts."""
    raw = os.environ.get("FEICMS_ENV_FILE")
    return Path(raw) if raw else BASE_DIR / ".env"


def update_env_file(values: dict[str, str], path: Path | None = None) -> Path:
    """Write (or replace) KEY=VALUE lines in .env, leaving everything else alone."""
    p = path or env_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    wanted = dict(values)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            out.append(f"{key}={wanted[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            out.append(f"{key}={value}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p
