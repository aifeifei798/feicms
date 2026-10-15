"""MCP (Model Context Protocol) server so AI clients can use feicms directly.

Two transports, one tool set:

    HTTP    POST /mcp        mounted on the FastAPI app. Authenticate with the
                             admin token or an API key:
                                 Authorization: Bearer fk_...
                                 X-API-Key: fk_...
    stdio   uv run feicms mcp
                             newline-delimited JSON-RPC on stdin/stdout, for
                             Claude Desktop / OpenCode / any MCP client. It reads
                             the same .env and database, so it is a local, trusted
                             process (pass --api-key to talk to a remote API instead).

Scopes are enforced per tool: a read-only API key can call the read tools, and
collect / health-check / image download need a `write` key.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .api_routes import _local_pic, _local_pic_map, _video_out
from .apikeys import has_active_keys
from .auth import Principal, authenticate, auth_enabled
from .collector import collect_all
from .config import settings
from .db import init_db, session
from .health import health_check_all
from .images import download_images, image_stats
from .models import Source, SourceItem, Video

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "feicms", "version": "0.1.0"}

# --------------------------------------------------------------------- tools
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_videos",
        "description": "搜索已采集的视频（跨资源站合并后的结果）。返回标题、分类、年份、海报、本地海报路径和播放源数量。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "标题关键词"},
                "type_name": {"type": "string", "description": "分类名，例如 电影 / 电视剧"},
                "year": {"type": "string", "description": "年份，例如 2024"},
                "sort": {"type": "string", "enum": ["updated", "name", "source_count"], "default": "updated"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_video",
        "description": "获取一个视频的详情，包含所有资源站来源和播放链接。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {"video_id": {"type": "integer", "description": "视频 id"}},
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_categories",
        "description": "列出数据库里的分类及每个分类的视频数量。",
        "scopes": {"read"},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_sources",
        "description": "列出资源站（采集源）及其健康状态、采集数量。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "health": {"type": "string", "enum": ["ok", "fail", "unknown"]},
                "query": {"type": "string", "description": "按名称过滤"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stats",
        "description": "总览：资源站数量、视频数量、采集条数、按分类/来源的统计。",
        "scopes": {"read"},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "provide_feed",
        "description": "以 Mackin/资源站通用格式（ac=list / ac=detail）返回本地数据库，方便验证对外 API。",
        "scopes": {"read"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "ac": {"type": "string", "enum": ["list", "detail"], "default": "list"},
                "query": {"type": "string"},
                "type_name": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_health",
        "description": "对资源站做一次连通性/响应检测，并更新健康状态。需要 write 权限。",
        "scopes": {"write"},
        "inputSchema": {
            "type": "object",
            "properties": {"only_enabled": {"type": "boolean", "default": True}},
            "additionalProperties": False,
        },
    },
    {
        "name": "collect_sources",
        "description": "采集资源站并写入数据库。pages=0 表示全量抓取。需要 write 权限。",
        "scopes": {"write"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_ids": {"type": "array", "items": {"type": "integer"}, "description": "指定资源站 id，留空则采集所有启用的站"},
                "pages": {"type": "integer", "description": "每个站抓取页数；0 = 全量（受 FEICMS_FULL_PAGE_CAP 限制）"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "image_stats",
        "description": "海报库统计：待下载 / 已下载 / 失败 / 磁盘占用。",
        "scopes": {"read"},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "download_images",
        "description": "下载一批待下载的海报到本地。需要 write 权限。",
        "scopes": {"write"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "concurrency": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}
TOOLS_MAP = {tool["name"]: tool for tool in TOOLS}

RESOURCES = [
    {"uri": "feicms://stats", "name": "stats", "description": "总览统计"},
    {"uri": "feicms://categories", "name": "categories", "description": "分类列表"},
    {"uri": "feicms://sources", "name": "sources", "description": "资源站列表"},
]


# --------------------------------------------------------------------- handlers
async def _search_videos(
    query: str | None = None,
    type_name: str | None = None,
    year: str | None = None,
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
            stmt = stmt.where(Video.name.like(f"%{query}%"))
        if type_name:
            stmt = stmt.where(Video.type_name == type_name)
        if year:
            stmt = stmt.where(Video.year == year)
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        order = {
            "updated": Video.last_updated.desc(),
            "name": Video.name.asc(),
            "source_count": Video.source_count.desc(),
        }[sort]
        videos = list(await s.scalars(stmt.order_by(order).limit(limit).offset(offset)))
        pic_map = await _local_pic_map(s, [v.pic for v in videos])

    return {
        "total": total or 0,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": v.id,
                "name": v.name,
                "type_name": v.type_name,
                "year": v.year,
                "area": v.area,
                "lang": v.lang,
                "blurb": v.blurb,
                "pic": v.pic,
                "local_pic": _local_pic(v, pic_map),
                "source_count": v.source_count,
                "last_updated": v.last_updated.isoformat() if v.last_updated else None,
            }
            for v in videos
        ],
    }


async def _get_video(video_id: int) -> dict[str, Any]:
    async with session() as s:
        video = await s.get(Video, int(video_id))
        if video is None:
            return {"error": f"video {video_id} not found"}
        items = list(await s.scalars(select(SourceItem).where(SourceItem.video_id == video.id)))
        pic_map = await _local_pic_map(s, [video.pic])
        sources = {
            src.id: src.name
            for src in await s.scalars(select(Source).where(Source.id.in_({i.source_id for i in items})))
        } if items else {}

    out = _video_out(video, items, _local_pic(video, pic_map)).model_dump()
    out["sources"] = [{"source_id": i.source_id, "source_name": sources.get(i.source_id, "")} for i in items]
    return out


async def _list_categories() -> list[dict[str, Any]]:
    async with session() as s:
        rows = await s.execute(
            select(Video.type_name, func.count(Video.id)).group_by(Video.type_name).order_by(func.count(Video.id).desc())
        )
    return [{"type_name": name or "", "count": count} for name, count in rows]


async def _list_sources(
    enabled: bool | None = None,
    health: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 500))
    async with session() as s:
        stmt = select(Source)
        if enabled is not None:
            stmt = stmt.where(Source.enabled == enabled)
        if health:
            stmt = stmt.where(Source.health_status == health)
        if query:
            stmt = stmt.where(Source.name.like(f"%{query}%"))
        sources = list(await s.scalars(stmt.order_by(Source.priority.desc(), Source.id).limit(limit)))
    return [
        {
            "id": src.id,
            "name": src.name,
            "url": src.url,
            "api_format": src.api_format,
            "enabled": src.enabled,
            "health_status": src.health_status,
            "last_error": src.last_error,
            "item_count": src.item_count,
            "max_pages": src.max_pages,
            "tags": src.tags,
            "last_collect_at": src.last_collect_at.isoformat() if src.last_collect_at else None,
        }
        for src in sources
    ]


async def _get_stats() -> dict[str, Any]:
    async with session() as s:
        sources = list(await s.scalars(select(Source)))
        videos = await s.scalar(select(func.count()).select_from(Video))
        items = await s.scalar(select(func.count()).select_from(SourceItem))
        last = await s.scalar(select(func.max(Source.last_collect_at)))
        type_rows = await s.execute(
            select(Video.type_name, func.count(Video.id))
            .group_by(Video.type_name)
            .order_by(func.count(Video.id).desc())
            .limit(30)
        )
    return {
        "sources": len(sources),
        "enabled_sources": sum(1 for src in sources if src.enabled),
        "healthy_sources": sum(1 for src in sources if src.health_status == "ok"),
        "videos": videos or 0,
        "source_items": items or 0,
        "last_collect_at": last.isoformat() if last else None,
        "by_type": [{"type_name": name or "", "count": count} for name, count in type_rows],
    }


async def _provide_feed(
    ac: str = "list",
    query: str | None = None,
    type_name: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 20), 100))
    async with session() as s:
        stmt = select(Video)
        if query:
            stmt = stmt.where(Video.name.like(f"%{query}%"))
        if type_name:
            stmt = stmt.where(Video.type_name == type_name)
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        videos = list(await s.scalars(stmt.order_by(Video.last_updated.desc()).limit(limit).offset((page - 1) * limit)))
        ids = [v.id for v in videos]
        plays: dict[int, list[SourceItem]] = {}
        if ids:
            for item in await s.scalars(select(SourceItem).where(SourceItem.video_id.in_(ids))):
                plays.setdefault(item.video_id, []).append(item)

    out = []
    for v in videos:
        froms: list[str] = []
        urls: list[str] = []
        for item in plays.get(v.id, []):
            for entry in json.loads(item.plays or "[]"):
                froms.append(entry.get("from") or item.play_from or "local")
                urls.append("#".join(f"{e['name']}${e['url']}" for e in entry.get("episodes", [])))
        out.append(
            {
                "vod_id": v.id,
                "vod_name": v.name,
                "type_name": v.type_name,
                "year": v.year,
                "pic": v.pic,
                "remarks": v.remarks,
                "vod_time": v.last_updated.strftime("%Y-%m-%d %H:%M:%S") if v.last_updated else "",
                "vod_from": froms[0] if froms else "",
                "vod_play_from": "||".join(froms),
                "vod_play_url": "||".join(urls),
            }
        )
    return {
        "class": "video",
        "total": total or 0,
        "page": page,
        "limit": limit,
        "list": out if ac == "list" else out,
    }


async def _check_health(only_enabled: bool = True) -> list[dict[str, Any]]:
    results = await health_check_all(only_enabled=only_enabled)
    return [
        {
            "source_id": r.source_id,
            "name": r.name,
            "ok": r.ok,
            "status_code": r.status_code,
            "latency_ms": r.latency_ms,
            "items": r.items,
            "message": r.message,
        }
        for r in results
    ]


async def _collect_sources(source_ids: list[int] | None = None, pages: int | None = None) -> list[dict[str, Any]]:
    results = await collect_all(source_ids=source_ids or None, pages=pages)
    return [
        {
            "source_id": r.source_id,
            "name": r.source_name,
            "pages": r.pages,
            "inserted": r.inserted,
            "updated": r.updated,
            "skipped": r.skipped,
            "pagecount": r.pagecount,
            "total": r.total,
            "error": r.error,
        }
        for r in results
    ]


async def _download_images(limit: int = 200, concurrency: int = 8) -> dict[str, Any]:
    result = await download_images(limit=limit, concurrency=concurrency)
    return {
        "attempted": result.attempted,
        "downloaded": result.downloaded,
        "duplicates": result.duplicates,
        "failed": result.failed,
        "skipped": result.skipped,
        "bytes": result.bytes,
    }


HANDLERS: dict[str, Any] = {
    "search_videos": _search_videos,
    "get_video": _get_video,
    "list_categories": _list_categories,
    "list_sources": _list_sources,
    "get_stats": _get_stats,
    "provide_feed": _provide_feed,
    "check_health": _check_health,
    "collect_sources": _collect_sources,
    "image_stats": image_stats,
    "download_images": _download_images,
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
                    "content": [{"type": "text", "text": f"API key '{principal.name}' 缺少权限：需要 {', '.join(sorted(TOOLS_MAP[name]['scopes']))}"}],
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
        handler = {"feicms://stats": _get_stats, "feicms://categories": _list_categories, "feicms://sources": _list_sources}.get(uri)
        if handler is None:
            return _error(rpc_id, f"unknown resource: {uri}")
        data = await handler()
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, default=str)}]},
        }

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"prompts": []}}

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
            {"server": SERVER_INFO["name"], "version": SERVER_INFO["version"], "db": settings.database_url, "tools": sorted(TOOL_NAMES)}
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
