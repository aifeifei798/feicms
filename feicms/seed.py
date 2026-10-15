from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_sources(path: str | Path) -> list[dict]:
    """Read source definitions from YAML or JSON.

    Accepted shapes:
      - list of dicts: {name, url, api_format?, enabled?, priority?, max_pages?, tags?}
      - list of strings: "name|url|format|uptime"
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)

    if isinstance(data, dict):
        data = data.get("sources", [])

    out: list[dict] = []
    for entry in data:
        if isinstance(entry, str):
            parts = [x.strip() for x in entry.split("|")]
            entry = {
                "name": parts[0],
                "url": parts[1],
                "api_format": parts[2] if len(parts) > 2 else "auto",
                "uptime": float(parts[3]) if len(parts) > 3 else 100.0,
            }
        if not entry.get("url"):
            continue
        out.append(
            {
                "name": entry["name"],
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
