"""MCP (Model Context Protocol) server: a read-only view of the collected library.

The MCP surface is deliberately **read-only**: it is meant to be the RAG / knowledge
base side of feicms (search, documents, chunks, related items). Maintenance work
(collecting, health checks, image downloads, editing sources) stays in the admin UI,
the CLI and the `/api/*` endpoints, and is never exposed through MCP.

Two transports, one tool set:

    HTTP    POST /mcp        mounted on the FastAPI app. Authenticate with the
                             admin token or an API key:
                                 Authorization: Bearer fk_...
                                 X-API-Key: fk_...
    stdio   uv run feicms mcp
                             newline-delimited JSON-RPC on stdin/stdout, for
                             Claude Desktop / OpenCode / any MCP client. It reads
                             the same .env and database, so it is a local, trusted
                             process.

Every tool only reads, so a plain `read` API key is enough for an AI client.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from .api_routes import _local_pic, _local_pic_map
from .apikeys import has_active_keys
from .auth import Principal, authenticate, auth_enabled
from .config import settings
from .db import init_db, session
from .models import Source, SourceItem, Video

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "feicms", "version": "0.1.0"}

# --------------------------------------------------------------------- tools
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": (
            "关键词检索影片库（标题 / 简介 / 演员 / 导演 / 分类 / 地区 / 年份 全文匹配）。"
            "返回 id、标题、分类、年份、简介摘要、海报，以及播放源数量与总集数（first_url 是第一个线路的第一集 URL）。"
        ),
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，例如 流浪地球 / 张译"},
                "type_name": {"type": "string", "description": "分类名，例如 电影 / 电视剧"},
                "year": {"type": "string", "description": "年份，例如 2024"},
                "area": {"type": "string", "description": "地区，例如 中国 / 美国"},
                "sort": {"type": "string", "enum": ["updated", "name", "source_count"], "default": "updated"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_document",
        "description": (
            "取一条影片记录作为纯文本文档（标题、分类、年份、地区、导演、主演、简介、备注 + 播放源 URL）。"
            "单集直接给 URL，多集会列出每集 name + URL（默认最多 60 集，超出部分用 get_plays 拿全量）。"
        ),
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "integer", "description": "影片 id（来自 search 结果）"},
                "max_episodes": {"type": "integer", "minimum": 1, "maximum": 500, "default": 60, "description": "每个播放源最多列出多少集"},
                "include_urls": {"type": "boolean", "default": True, "description": "是否包含播放 URL（false 只给线路名和集数）"},
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_plays",
        "description": "某条影片的全部播放源，结构化返回：站点名、线路名(from)、集数、每集 name + URL。单集和多集都支持。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "integer", "description": "影片 id"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50, "description": "最多返回多少个播放源（线路）"},
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browse",
        "description": "按分类 / 年份 / 地区浏览影片库，分页返回。不知道关键词时用这个。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "type_name": {"type": "string"},
                "year": {"type": "string"},
                "area": {"type": "string"},
                "sort": {"type": "string", "enum": ["updated", "name", "source_count"], "default": "updated"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "related",
        "description": "与某条影片标题相关的条目（同系列 / 同名 / 同主演标题），用于扩展检索上下文。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_chunks",
        "description": (
            "批量导出影片文档分块（id + 纯文本），用于把整个库灌进向量数据库 / RAG 索引。按 id 顺序分页。"
            "默认只带播放源摘要（线路名 + 集数，单集带 URL）；include_urls=true 时带完整每集 URL（体积大）。"
        ),
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "type_name": {"type": "string", "description": "只导出某个分类"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "include_urls": {"type": "boolean", "default": False, "description": "分块里是否包含每集播放 URL"},
                "max_episodes": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_types",
        "description": "列出分类（type_name）及每个分类的影片数量。",
        "scopes": {"read"},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_stats",
        "description": "库规模：影片总数、分类分布、最近更新时间。",
        "scopes": {"read"},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}
TOOLS_MAP = {tool["name"]: tool for tool in TOOLS}

RESOURCES = [
    {"uri": "feicms://stats", "name": "stats", "description": "库规模与分类分布"},
    {"uri": "feicms://types", "name": "types", "description": "分类列表"},
    {"uri": "feicms://instructions", "name": "instructions", "description": "如何使用这个 MCP 服务"},
]

INSTRUCTIONS = (
    "feicms MCP 是一个只读的影片库。用法：\n"
    "1) get_stats / list_types 了解库有多大、有哪些分类。\n"
    "2) search(query=...) 关键词检索，返回 id、简介摘要、播放源数量与总集数。\n"
    "3) get_document(video_id) 取单条纯文本文档（含播放源 URL），用于回答或引用。\n"
    "4) get_plays(video_id) 拿全部播放源：站点、线路、每集 name + URL（单集和多集都支持）。\n"
    "5) related(video_id) 获取相关条目，扩展上下文。\n"
    "6) read_chunks(limit, offset) 批量导出文档分块，灌入向量库。\n"
    "所有工具只读，不会采集、写入或删除任何数据。"
)


# --------------------------------------------------------------------- helpers
_TOKEN_SPLIT = re.compile(r"[\s,./·\-()（）\[\]【】:：!?！]+")


def _parse_plays(item: SourceItem, source_name: str) -> list[dict[str, Any]]:
    """Structured play sources of one raw record: single-episode and multi-episode."""
    try:
        entries = json.loads(item.plays or "[]")
    except json.JSONDecodeError:
        entries = []
    plays: list[dict[str, Any]] = []
    for entry in entries:
        episodes = entry.get("episodes") or []
        if not episodes:
            continue
        line = (entry.get("from") or item.play_from or "").strip()
        plays.append(
            {
                "source": source_name,
                "line": line,
                "episode_count": len(episodes),
                "episodes": [
                    {"name": (ep.get("name") or f"第{i + 1}集").strip(), "url": (ep.get("url") or "").strip()}
                    for i, ep in enumerate(episodes)
                ],
            }
        )
    return plays


async def _plays_for(s, video_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Play sources for several videos in one query, keyed by video id."""
    result: dict[int, list[dict[str, Any]]] = {vid: [] for vid in video_ids}
    if not video_ids:
        return result
    rows = list(
        await s.execute(
            select(SourceItem, Source.name)
            .join(Source, Source.id == SourceItem.source_id)
            .where(SourceItem.video_id.in_(video_ids))
            .order_by(Source.name)
        )
    )
    for item, source_name in rows:
        result[item.video_id].extend(_parse_plays(item, source_name))
    return result


def _play_lines(plays: list[dict[str, Any]], max_episodes: int = 60, include_urls: bool = True) -> list[str]:
    """Render play sources as text: one URL for single episodes, one line per episode for series."""
    if not plays:
        return []
    lines = ["播放源:"]
    for play in plays:
        label = play["source"] or play["line"] or "未知站点"
        if play["line"] and play["line"] != label:
            label = f"{label} / {play['line']}"
        if play["episode_count"] == 1:
            if include_urls:
                lines.append(f"- {label}: {play['episodes'][0]['url']}")
            else:
                lines.append(f"- {label}（1 集）")
            continue
        lines.append(f"- {label}（共 {play['episode_count']} 集）")
        if not include_urls:
            continue
        for episode in play["episodes"][:max_episodes]:
            lines.append(f"    {episode['name']}: {episode['url']}")
        if play["episode_count"] > max_episodes:
            lines.append(f"    … 其余 {play['episode_count'] - max_episodes} 集用 get_plays 获取")
    return lines


def _document(
    video: Video,
    plays: list[dict[str, Any]] | None = None,
    max_episodes: int = 60,
    include_urls: bool = True,
) -> str:
    """Plain-text document for RAG: one record, human and model readable."""
    parts = [video.name]
    meta = " · ".join(x for x in (video.type_name, video.year, video.area, video.lang, video.category) if x)
    if meta:
        parts.append(meta)
    if video.director:
        parts.append(f"导演: {video.director}")
    if video.actor:
        parts.append(f"主演: {video.actor}")
    if video.blurb:
        parts.append(video.blurb.strip())
    if video.remarks:
        parts.append(f"备注: {video.remarks}")
    updated = video.last_updated.strftime("%Y-%m-%d") if video.last_updated else ""
    parts.append(f"来源: {video.source_count} 个资源站" + (f" · 更新: {updated}" if updated else ""))
    lines = _play_lines(plays or [], max_episodes=max_episodes, include_urls=include_urls)
    if lines:
        parts.append("\n".join(lines))
    return "\n".join(parts)


def _summary(video: Video, pic: str | None, plays: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    plays = plays or []
    blurb = (video.blurb or "").strip()
    return {
        "id": video.id,
        "name": video.name,
        "type_name": video.type_name,
        "year": video.year,
        "area": video.area,
        "lang": video.lang,
        "category": video.category,
        "blurb": blurb[:400],
        "pic": video.pic,
        "local_pic": pic,
        "source_count": video.source_count,
        "last_updated": video.last_updated.isoformat() if video.last_updated else None,
        "play_sources": len(plays),
        "episode_count": sum(p["episode_count"] for p in plays),
        "play_lines": [
            {"source": p["source"], "line": p["line"], "episodes": p["episode_count"]} for p in plays[:10]
        ],
        "first_url": plays[0]["episodes"][0]["url"] if plays else None,
    }


# --------------------------------------------------------------------- handlers
async def _search(
    query: str | None = None,
    type_name: str | None = None,
    year: str | None = None,
    area: str | None = None,
    sort: str = "updated",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    sort = sort if sort in {"updated", "name", "source_count"} else "updated"

    async with session() as s:
        stmt = select(Video)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Video.name.like(like),
                    Video.blurb.like(like),
                    Video.actor.like(like),
                    Video.director.like(like),
                    Video.category.like(like),
                    Video.type_name.like(like),
                    Video.area.like(like),
                    Video.year.like(like),
                )
            )
        if type_name:
            stmt = stmt.where(Video.type_name == type_name)
        if year:
            stmt = stmt.where(Video.year == year)
        if area:
            stmt = stmt.where(Video.area.like(f"%{area}%"))

        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        order = {
            "updated": Video.last_updated.desc(),
            "name": Video.name.asc(),
            "source_count": Video.source_count.desc(),
        }[sort]
        videos = list(await s.scalars(stmt.order_by(order).limit(limit).offset(offset)))
        pic_map = await _local_pic_map(s, [v.pic for v in videos])
        plays_map = await _plays_for(s, [v.id for v in videos])

    return {
        "total": total or 0,
        "offset": offset,
        "limit": limit,
        "items": [_summary(v, _local_pic(v, pic_map), plays_map.get(v.id, [])) for v in videos],
    }


async def _browse(
    type_name: str | None = None,
    year: str | None = None,
    area: str | None = None,
    sort: str = "updated",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return await _search(query=None, type_name=type_name, year=year, area=area, sort=sort, limit=limit, offset=offset)


async def _get_document(video_id: int, max_episodes: int = 60, include_urls: bool = True) -> dict[str, Any]:
    max_episodes = max(1, min(int(max_episodes or 60), 500))
    async with session() as s:
        video = await s.get(Video, int(video_id))
        if video is None:
            return {"error": f"video {video_id} not found"}
        pic_map = await _local_pic_map(s, [video.pic])
        plays = (await _plays_for(s, [video.id])).get(video.id, [])
    return {
        "id": video.id,
        "name": video.name,
        "text": _document(video, plays, max_episodes=max_episodes, include_urls=include_urls),
        "meta": _summary(video, _local_pic(video, pic_map), plays),
        "plays": plays,
    }


async def _get_plays(video_id: int, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    async with session() as s:
        video = await s.get(Video, int(video_id))
        if video is None:
            return {"error": f"video {video_id} not found"}
        plays = (await _plays_for(s, [video.id])).get(video.id, [])
    return {
        "video_id": video.id,
        "name": video.name,
        "play_sources": len(plays),
        "episode_count": sum(p["episode_count"] for p in plays),
        "plays": plays[:limit],
    }


async def _related(video_id: int, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit or 10), 50))
    async with session() as s:
        video = await s.get(Video, int(video_id))
        if video is None:
            return {"error": f"video {video_id} not found"}
        tokens = [t for t in _TOKEN_SPLIT.split(video.name) if len(t) >= 2]
        tokens = sorted(set(tokens), key=len, reverse=True)[:6]

        seen: set[int] = set()
        found: list[Video] = []
        for token in tokens:
            rows = list(
                await s.scalars(
                    select(Video)
                    .where(Video.name.like(f"%{token}%"), Video.id != video.id)
                    .order_by(Video.source_count.desc(), Video.last_updated.desc())
                    .limit(limit)
                )
            )
            for row in rows:
                if row.id in seen:
                    continue
                seen.add(row.id)
                found.append(row)
                if len(found) >= limit:
                    break

        pic_map = await _local_pic_map(s, [v.pic for v in found])
        plays_map = await _plays_for(s, [v.id for v in found])

    return {
        "video_id": video_id,
        "name": video.name,
        "tokens": tokens,
        "items": [_summary(v, _local_pic(v, pic_map), plays_map.get(v.id, [])) for v in found],
    }


async def _read_chunks(
    type_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_urls: bool = False,
    max_episodes: int = 30,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    max_episodes = max(1, min(int(max_episodes or 30), 200))
    async with session() as s:
        stmt = select(Video)
        if type_name:
            stmt = stmt.where(Video.type_name == type_name)
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        videos = list(await s.scalars(stmt.order_by(Video.id).limit(limit).offset(offset)))
        plays_map = await _plays_for(s, [v.id for v in videos])
    return {
        "total": total or 0,
        "offset": offset,
        "limit": limit,
        "chunks": [
            {
                "id": v.id,
                "name": v.name,
                "type_name": v.type_name,
                "episode_count": sum(p["episode_count"] for p in plays_map.get(v.id, [])),
                "text": _document(v, plays_map.get(v.id, []), max_episodes=max_episodes, include_urls=include_urls),
            }
            for v in videos
        ],
    }


async def _list_types() -> list[dict[str, Any]]:
    async with session() as s:
        rows = await s.execute(
            select(Video.type_name, func.count(Video.id)).group_by(Video.type_name).order_by(func.count(Video.id).desc())
        )
    return [{"type_name": name or "", "count": count} for name, count in rows]


async def _get_stats() -> dict[str, Any]:
    async with session() as s:
        videos = await s.scalar(select(func.count()).select_from(Video))
        last = await s.scalar(select(func.max(Video.last_updated)))
        type_rows = await s.execute(
            select(Video.type_name, func.count(Video.id))
            .group_by(Video.type_name)
            .order_by(func.count(Video.id).desc())
            .limit(30)
        )
        with_blurb = await s.scalar(select(func.count()).select_from(Video).where(Video.blurb != ""))
    return {
        "videos": videos or 0,
        "documents_with_blurb": with_blurb or 0,
        "last_updated": last.isoformat() if last else None,
        "by_type": [{"type_name": name or "", "count": count} for name, count in type_rows],
    }


HANDLERS: dict[str, Any] = {
    "search": _search,
    "get_document": _get_document,
    "get_plays": _get_plays,
    "browse": _browse,
    "related": _related,
    "read_chunks": _read_chunks,
    "list_types": _list_types,
    "get_stats": _get_stats,
}


# --------------------------------------------------------------------- protocol
def _error(rpc_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32600, "message": message}}


async def handle_rpc(payload: dict[str, Any], principal: Principal | None) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": [{k: v for k, v in tool.items() if k != "scopes"} for tool in TOOLS]},
        }

    if method == "tools/call":
        name = params.get("name", "")
        if name not in HANDLERS:
            return _error(rpc_id, f"unknown tool: {name}")
        if principal is not None and principal.kind == "apikey" and not (principal.scopes & TOOLS_MAP[name]["scopes"]):
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"API key '{principal.name}' 缺少权限：需要 {', '.join(sorted(TOOLS_MAP[name]['scopes']))}",
                        }
                    ],
                    "isError": True,
                },
            }
        try:
            result = await HANDLERS[name](**(params.get("arguments") or {}))
        except Exception as exc:  # tool errors are reported as results, per MCP convention
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True},
            }
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": [{"type": "text", "text": text}], "structuredContent": result},
        }

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"resources": RESOURCES}}

    if method == "resources/read":
        uri = (params.get("uri") or "").strip()
        if uri == "feicms://instructions":
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": INSTRUCTIONS}]},
            }
        handler = {"feicms://stats": _get_stats, "feicms://types": _list_types}.get(uri)
        if handler is None:
            return _error(rpc_id, f"unknown resource: {uri}")
        data = await handler()
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, default=str)}]
            },
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "prompts": [
                    {
                        "name": "rag_answer",
                        "description": "基于影片库回答：先检索，再引用文档",
                        "arguments": [{"name": "query", "description": "用户的问题"}],
                    }
                ]
            },
        }

    return _error(rpc_id, f"unknown method: {method}")


# --------------------------------------------------------------------- transports
mcp_router = APIRouter()


@mcp_router.get("/mcp")
async def mcp_info(request: Request) -> dict[str, Any]:
    """Small discovery endpoint: what this MCP server offers."""
    principal = await authenticate(request)
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "transport": "streamable http",
        "endpoint": "/mcp",
        "read_only": True,
        "authenticated": principal is not None,
        "tools": sorted(TOOL_NAMES),
    }


@mcp_router.post("/mcp")
async def mcp_http(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return _error(None, "expected a JSON-RPC body")
    if not isinstance(payload, dict):
        return _error(None, "expected a JSON-RPC object")

    principal = await authenticate(request)
    if principal is None and (auth_enabled() or await has_active_keys()):
        return _error(payload.get("id"), "未授权：需要管理员 token 或 API key（X-API-Key: fk_...）")
    response = await handle_rpc(payload, principal)
    return response or {"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}


def serve_stdio() -> None:
    """Run the MCP server over stdin/stdout (newline-delimited JSON-RPC)."""
    asyncio.run(_stdio_loop())


async def _stdio_loop() -> None:
    await init_db()
    principal = Principal("local", "stdio")
    log = sys.stderr
    print(
        json.dumps(
            {
                "server": SERVER_INFO["name"],
                "version": SERVER_INFO["version"],
                "db": settings.database_url,
                "read_only": True,
                "tools": sorted(TOOL_NAMES),
            }
        ),
        file=log,
    )
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, "invalid JSON")), file=log)
            continue
        response = await handle_rpc(payload, principal)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def mcp_config_json(url: str | None = None) -> str:
    """Print a ready-to-paste MCP client configuration."""
    if url:
        entry = {"type": "http", "url": url}
    else:
        entry = {"type": "stdio", "command": "uv", "args": ["run", "feicms", "mcp"]}
    return json.dumps({"mcpServers": {"feicms": entry}}, ensure_ascii=False, indent=2)
