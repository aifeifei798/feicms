from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import yaml


def _host_name(url: str) -> str:
    """Fallback display name for a bare URL: its hostname."""
    host = urlparse(url).netloc or url
    return host.replace("www.", "")


def _normalize(data) -> list[dict]:
    """Turn a parsed list of entries into source rows.

    Accepted entry shapes:
      - dict: {name, url, api_format?, enabled?, priority?, max_pages?, tags?, uptime?}
      - string: "name|url|format|uptime" or a bare URL
    """
    if isinstance(data, dict):
        data = data.get("sources", [])

    out: list[dict] = []
    for entry in data:
        if isinstance(entry, str):
            parts = [x.strip() for x in entry.split("|")]
            entry = {
                "name": parts[0] if len(parts) > 1 else _host_name(parts[0]),
                "url": parts[1] if len(parts) > 1 else parts[0],
                "api_format": parts[2] if len(parts) > 2 else "auto",
                "uptime": float(parts[3]) if len(parts) > 3 else 100.0,
            }
        if not entry.get("url"):
            continue
        out.append(
            {
                "name": entry.get("name") or _host_name(entry["url"]),
                "url": entry["url"],
                "api_format": entry.get("api_format", "auto"),
                "enabled": bool(entry.get("enabled", True)),
                "priority": int(entry.get("priority", 0)),
                "max_pages": int(entry.get("max_pages", 5)),
                "tags": entry.get("tags", ""),
                "uptime": float(entry.get("uptime", 100.0)),
            }
        )
    return out


def parse_sources_text(text: str) -> list[dict]:
    """Parse a pasted source list.

    Works with the plain `name|url|format|uptime` line format, a list of bare
    URLs, or a YAML/JSON document. Blank lines and `#` comments are ignored.
    """
    text = (text or "").strip()
    if not text:
        return []

    if text.startswith("[") or text.startswith("{") or text.startswith("- "):
        try:
            data = json.loads(text) if text[0] in "[{" else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError):
            data = None
        if data is not None:
            return _normalize(data)

    entries: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return _normalize(entries)


def load_sources(path: str | Path) -> list[dict]:
    """Read source definitions from YAML, JSON or a plain text list."""
    p = Path(path)
    return parse_sources_text(p.read_text(encoding="utf-8"))
