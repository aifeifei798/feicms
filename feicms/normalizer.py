"""Normalise the many dialects of "资源站" APIs into one internal shape.

Supported shapes:
  * mackin JSON:   {"code":1,"list":[{vod_id, vod_name, ...}]}
  * json.php:      same as above (kxgav / msnii / pgxdy ...)
  * RSS/XML:       <video><id><name><type><dt><note>...  (XML 站点)
  * bare list:     [{"id":..,"name":..}]
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from xml.etree import ElementTree as ET

try:  # lxml recovers from the broken XML these sites often emit
    from lxml import etree as _lxml
except ImportError:  # pragma: no cover - optional
    _lxml = None

YEAR_RE = re.compile(r"(?:19|20)\d{2}")
EPISODE_SPLIT = re.compile(r"[#|]")
NAME_URL_SPLIT = re.compile(r"(?:\$|::|:)")
# Many sites list several play lines in one field, separated by `$$$`:
#   vod_play_from = "hnyun$$$hnm3u8"
#   vod_play_url  = "全集完结$https://a/play/x$$$全集完结$https://b/x/index.m3u8"
SOURCE_SPLIT = re.compile(r"\s*\$\$\$\s*")


@dataclass
class Episode:
    name: str
    url: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "url": self.url}


@dataclass
class PlaySource:
    from_name: str
    episodes: list[Episode] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.from_name, "episodes": [e.as_dict() for e in self.episodes]}

    def joined_url(self) -> str:
        return "#".join(f"{e.name}${e.url}" for e in self.episodes)


@dataclass
class VodItem:
    vod_id: int
    name: str
    type_name: str = ""
    type_id: int = 0
    year: str = ""
    area: str = ""
    lang: str = ""
    pic: str = ""
    blurb: str = ""
    actor: str = ""
    director: str = ""
    remarks: str = ""
    vod_time: str = ""
    play_from: str = ""
    play_url: str = ""
    plays: list[PlaySource] = field(default_factory=list)

    @property
    def signature(self) -> str:
        return build_signature(self.name, self.year)

    def has_playable(self) -> bool:
        return any(p.episodes for p in self.plays)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)  # sites often embed markup in descriptions
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return unicodedata.normalize("NFKC", text).strip()


def build_signature(name: str, year: str = "") -> str:
    """Stable key used to merge the same title coming from different sources.

    Titles are compared after removing whitespace and punctuation, so
    "神兽学院 2026" and "神兽学院2026" collapse into one record.
    """
    n = clean_text(name).lower()
    n = re.sub(r"\s+", "", n)
    n = re.sub(r"[^\w\u4e00-\u9fff0-9]", "", n)
    return n


def guess_year(name: str, explicit: str = "") -> str:
    explicit = clean_text(explicit)
    if YEAR_RE.search(explicit):
        return YEAR_RE.search(explicit).group(0)
    matches = YEAR_RE.findall(name)
    return matches[-1] if matches else ""


def _episode_from_part(part: str) -> Episode | None:
    """Parse one `name$url` (or bare url) episode fragment."""
    part = part.strip().strip("#|")
    if not part:
        return None
    if part.startswith(("http", "//")):
        name, url = "", part
    elif "$" in part:
        name, url = part.split("$", 1)
        url = url.strip()
        if not url.startswith(("http", "//")):
            # junk after the first `$`, e.g. "url$$$全集完结$https://..."
            candidate = next((p for p in part.split("$") if p.startswith(("http", "//"))), "")
            if not candidate:
                return None
            name, url = "", candidate
    else:
        m = NAME_URL_SPLIT.search(part)
        if m and "http" in part[m.end():]:
            name, url = part[: m.start()], part[m.end():]
        else:
            name, url = "", part
    url = url.strip().strip("#|")
    if not url.startswith(("http", "//")):
        return None
    return Episode(name=name.strip(), url=url)


def parse_play_url(play_from: str, play_url: str) -> list[PlaySource]:
    """Turn `vod_play_from` / `vod_play_url` into structured sources.

    Play lines are separated by `$$$` (most sites) or `,`; episodes inside one
    line are separated by `#` or `|`. Single-episode entries and long episode
    lists are both handled.
    """
    froms = [f.strip() for f in SOURCE_SPLIT.split(clean_text(play_from)) if f.strip()]
    if len(froms) <= 1:
        parts = [f.strip() for f in clean_text(play_from).split(",") if f.strip()]
        if len(parts) > len(froms):
            froms = parts

    raw = clean_text(play_url)
    if not raw:
        return []

    chunks = [c for c in SOURCE_SPLIT.split(raw) if c.strip()]
    if len(chunks) != len(froms) and len(froms) > 1 and "," in raw:
        chunks = [c for c in raw.split(",") if c.strip()]
    if len(chunks) > len(froms):
        froms = froms + [f"line{i + 1}" for i in range(len(chunks) - len(froms))]
    elif len(chunks) < len(froms):
        chunks = [raw]
        froms = froms[:1] or ["default"]

    result: list[PlaySource] = []
    for idx, chunk in enumerate(chunks):
        from_name = froms[idx] if idx < len(froms) else (froms[0] if froms else "default")
        episodes: list[Episode] = []
        seen: set[str] = set()
        for part in EPISODE_SPLIT.split(chunk):
            episode = _episode_from_part(part)
            if episode is None or episode.url in seen:
                continue
            seen.add(episode.url)
            if not episode.name:
                episode.name = "正片" if not episodes else f"第{len(episodes) + 1}集"
            episodes.append(episode)
        if episodes:
            result.append(PlaySource(from_name=from_name, episodes=episodes))
    return result


def _first(raw: dict, *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return ""


def normalize_item(raw: dict) -> VodItem | None:
    vod_id = _first(raw, "vod_id", "id", "video_id")
    name = clean_text(_first(raw, "vod_name", "name", "title"))
    try:
        vod_id = int(vod_id)
    except (TypeError, ValueError):
        return None
    if not name or not vod_id:
        return None

    play_from = clean_text(_first(raw, "vod_play_from", "dt", "from", "play_from"))
    play_url = clean_text(_first(raw, "vod_play_url", "url", "play_url", "note"))

    item = VodItem(
        vod_id=vod_id,
        name=name,
        type_name=clean_text(_first(raw, "type_name", "type", "category")),
        type_id=int(_first(raw, "type_id", "tid") or 0),
        year=guess_year(name, clean_text(_first(raw, "vod_year", "year", "pubdate"))),
        area=clean_text(_first(raw, "vod_area", "area")),
        lang=clean_text(_first(raw, "vod_lang", "lang")),
        pic=clean_text(_first(raw, "vod_pic", "pic", "img")),
        blurb=clean_text(_first(raw, "vod_blurb", "desc", "content", "note")),
        actor=clean_text(_first(raw, "vod_actor", "actor")),
        director=clean_text(_first(raw, "vod_director", "director")),
        remarks=clean_text(_first(raw, "vod_remarks", "remark", "note")),
        vod_time=clean_text(_first(raw, "vod_time", "last", "updated")),
        play_from=play_from,
        play_url=play_url,
    )
    item.plays = parse_play_url(play_from, play_url)
    return item


def parse_response(text: str, api_format: str = "auto") -> tuple[list[VodItem], int, int]:
    """Return (items, page_count, total)."""
    fmt = api_format
    if fmt == "auto":
        fmt = "xml" if text.lstrip().startswith("<") else "json"

    if fmt == "xml":
        return _parse_xml(text)
    return _parse_json(text)


def _xml_root(text: str):
    """Parse XML leniently (lxml recovers, ElementTree is the fallback)."""
    if _lxml is not None:
        try:
            return _lxml.fromstring(text.encode("utf-8"))
        except Exception:  # noqa: BLE001 - fall through to ElementTree
            pass
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def _video_nodes(root) -> list[dict]:
    list_node = root.find("list")
    nodes = list_node.findall("video") if list_node is not None else root.findall("video")
    return [{child.tag: (child.text or "") for child in node} for node in nodes]


def parse_raw_items(text: str, api_format: str = "auto") -> list[dict]:
    """Return the raw record dicts (un-normalised) — used for `ac=types` style payloads."""
    fmt = api_format
    if fmt == "auto":
        fmt = "xml" if text.lstrip().startswith("<") else "json"

    if fmt == "xml":
        root = _xml_root(text)
        return _video_nodes(root) if root is not None else []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return data.get("list") or data.get("data") or data.get("items") or []


def _parse_json(text: str) -> tuple[list[VodItem], int, int]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], 0, 0

    if isinstance(data, list):
        payload = data
        pagecount = 0
        total = len(data)
    else:
        payload = data.get("list") or data.get("data") or data.get("items") or []
        pagecount = int(data.get("pagecount") or data.get("pages") or 0)
        total = int(data.get("total") or data.get("recordcount") or 0)

    items = [i for i in (normalize_item(x) for x in payload) if i]
    return items, pagecount, total


def _parse_xml(text: str) -> tuple[list[VodItem], int, int]:
    root = _xml_root(text)
    if root is None:
        return [], 0, 0

    list_node = root.find("list")
    pagecount = int(list_node.attrib.get("pagecount", 0)) if list_node is not None else 0
    total = int(list_node.attrib.get("recordcount", 0)) if list_node is not None else 0

    items: list[VodItem] = []
    for raw in _video_nodes(root):
        item = normalize_item(raw)
        if item:
            items.append(item)
    return items, pagecount, total
