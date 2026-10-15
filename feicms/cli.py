from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from .config import BASE_DIR, settings
from .collector import collect_all, collect_source
from .db import init_db, session
from .health import health_check_all
from .models import Source, Subscription, Video, SourceItem
from .seed import load_sources
from .subscribe import detect_kind, ensure_default_subscriptions, sync_all, sync_subscription

app = typer.Typer(help="feicms: collect resource sites into a database and keep them maintained.")
console = Console()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create tables."""
    asyncio.run(init_db())
    console.print(f"[green]database initialised[/green] -> {settings.database_url}")


@app.command("seed")
def seed(file: str = typer.Option(settings.sources_file, "--file", help="YAML/JSON file of sources")) -> None:
    """Load source definitions into the database (idempotent)."""
    async def _seed() -> None:
        path = Path(file)
        if not path.exists():
            console.print(
                f"[yellow]{path} not found[/yellow] - copy sources.example.yaml to {path}, "
                "or use `feicms add <name> <url>` / `feicms subscribe --sync`."
            )
            return
        entries = load_sources(path)
        async with session() as s:
            added = 0
            skipped = 0
            for entry in entries:
                existing = await s.scalar(select(Source).where(Source.name == entry["name"]))
                if existing:
                    skipped += 1
                    continue
                s.add(Source(**entry))
                added += 1
        console.print(f"[green]seeded[/green] {added} sources ({skipped} already present)")

    asyncio.run(_seed())


@app.command("sources")
def list_sources(
    enabled_only: bool = typer.Option(False, "--enabled-only"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Show all configured sources and their health."""

    async def _list() -> None:
        async with session() as s:
            stmt = select(Source)
            if enabled_only:
                stmt = stmt.where(Source.enabled)
            sources = list(await s.scalars(stmt.order_by(Source.priority.desc(), Source.id).limit(limit)))
        table = Table(title="资源站")
        table.add_column("id", justify="right")
        table.add_column("name")
        table.add_column("url")
        table.add_column("health")
        table.add_column("ms", justify="right")
        table.add_column("items", justify="right")
        table.add_column("enabled")
        for src in sources:
            table.add_row(
                str(src.id),
                src.name,
                src.url,
                src.health_status,
                str(src.latency_ms or "-"),
                str(src.item_count),
                "yes" if src.enabled else "no",
            )
        console.print(table)

    asyncio.run(_list())


@app.command("subscribe")
def subscribe(
    add: str = typer.Option(None, "--add", help="register a subscription feed URL"),
    remove: str = typer.Option(None, "--remove", help="delete a subscription feed URL"),
    sync: bool = typer.Option(False, "--sync", help="sync all enabled feeds"),
    url: str = typer.Option(None, "--url", help="sync one feed URL (registered if needed)"),
) -> None:
    """Manage source-list subscriptions (源订阅) and pull them into the source table."""

    async def _run() -> None:
        from sqlalchemy import delete, select

        await init_db()
        await ensure_default_subscriptions()
        async with session() as s:
            if add:
                sub = await s.scalar(select(Subscription).where(Subscription.url == add))
                if sub is None:
                    sub = Subscription(url=add, kind=detect_kind(add))
                    s.add(sub)
                    console.print(f"[green]registered subscription[/green] {add}")
                else:
                    console.print(f"[yellow]subscription already registered[/yellow] {add}")
            if remove:
                await s.execute(delete(Subscription).where(Subscription.url == remove))
                console.print(f"[yellow]removed subscription[/yellow] {remove}")

        if url:
            async with session() as s:
                sub = await s.scalar(select(Subscription).where(Subscription.url == url))
                if sub is None:
                    sub = Subscription(url=url, kind=detect_kind(url))
                    s.add(sub)
                    await s.flush()
            result = await sync_subscription(sub.id)
            console.print(
                f"[green]{result.url}[/green]: {result.entries} 条 · 新增 {result.added} · "
                f"更新 {result.updated} · 无变化 {result.unchanged} "
                f"{f'· 不在列表: {len(result.missing)}' if result.missing else ''}"
            )
            if result.error:
                console.print(f"[red]error[/red] {result.error}")
            return

        if not sync:
            async with session() as s:
                subs = list(await s.scalars(select(Subscription).order_by(Subscription.id)))
            if not subs:
                console.print("[yellow]no subscriptions registered[/yellow]")
                return
            table = Table(title="源订阅")
            table.add_column("id", justify="right")
            table.add_column("url")
            table.add_column("kind")
            table.add_column("enabled")
            table.add_column("条目", justify="right")
            table.add_column("新增", justify="right")
            table.add_column("更新", justify="right")
            table.add_column("最近同步")
            for sub in subs:
                table.add_row(
                    str(sub.id),
                    sub.url,
                    sub.kind,
                    "yes" if sub.enabled else "no",
                    str(sub.last_entries),
                    str(sub.last_added),
                    str(sub.last_updated),
                    sub.last_sync_at.strftime("%Y-%m-%d %H:%M") if sub.last_sync_at else "-",
                )
            console.print(table)
            return

        await ensure_default_subscriptions()
        for result in await sync_all():
            status = "OK" if not result.error else "FAIL"
            console.print(
                f"[{status}] {result.url}: {result.entries} 条 · 新增 {result.added} · "
                f"更新 {result.updated} · 无变化 {result.unchanged} "
                f"{f'· 不在列表 {len(result.missing)}' if result.missing else ''}"
            )
            if result.error:
                console.print(f"    [red]{result.error}[/red]")

    asyncio.run(_run())


@app.command("collect")
def collect(
    name: str = typer.Option(None, "--name", help="collect a single source by name"),
    include_disabled: bool = typer.Option(False, "--include-disabled"),
    pages: int = typer.Option(None, "--pages", help="pages per source; 0 = full sweep of every page"),
    full: bool = typer.Option(False, "--full", help="equivalent to --pages 0: walk all pages the source reports"),
) -> None:
    """Run collection now (all enabled sources, or one by --name)."""

    async def _run() -> None:
        names = [name] if name else None
        effective_pages = 0 if full else pages
        results = await collect_all(names=names, pages=effective_pages, only_enabled=not include_disabled)
        for r in results:
            status = "OK" if not r.error else "FAIL"
            scope = f"{r.pages}/{r.pagecount or '?'}页"
            console.print(
                f"[{status}] {r.source_name}: {scope} new={r.inserted} updated={r.updated} "
                f"skipped={r.skipped} total={r.total} {r.elapsed_ms / 1000:.1f}s {r.error or ''}"
            )

    asyncio.run(_run())


@app.command("health")
def health() -> None:
    """Probe every enabled source and record latency/availability."""

    async def _run() -> None:
        results = await health_check_all()
        table = Table(title="source health")
        table.add_column("name")
        table.add_column("ok")
        table.add_column("ms", justify="right")
        table.add_column("http", justify="right")
        table.add_column("note")
        for r in results:
            table.add_row(r.name, "✓" if r.ok else "✗", str(r.latency_ms), str(r.status_code), r.message)
        console.print(table)

    asyncio.run(_run())


@app.command("stats")
def stats() -> None:
    """Print collection statistics."""

    async def _run() -> None:
        async with session() as s:
            videos = await s.scalar(select(func.count()).select_from(Video))
            items = await s.scalar(select(func.count()).select_from(SourceItem))
            sources = await s.scalar(select(func.count()).select_from(Source))
            last = await s.scalar(select(func.max(Source.last_collect_at)))
        console.print(f"sources={sources} videos={videos} source_items={items} last_collect={last}")

    asyncio.run(_run())


@app.command("prune")
def prune() -> None:
    """Maintenance: drop records that have no playable episodes."""

    async def _run() -> None:
        async with session() as s:
            rows = list(await s.scalars(select(SourceItem).where(SourceItem.plays == "[]")))
            for row in rows:
                await s.delete(row)
            orphans = list(
                await s.scalars(select(Video).where(Video.id.not_in(select(SourceItem.video_id))))
            )
            for video in orphans:
                await s.delete(video)
        console.print(
            f"[yellow]removed {len(rows)} records with no playable episodes, {len(orphans)} orphan videos[/yellow]"
        )

    asyncio.run(_run())


@app.command("repair-plays")
def repair_plays_cmd() -> None:
    """用当前播放源解析规则重建 source_items.plays（不重新采集，只重新解析 raw 字段）。"""
    from .collector import repair_plays

    async def _run() -> None:
        await init_db()
        result = await repair_plays()
        console.print(
            f"[green]重建 {result['changed']} 条播放源[/green] · 共 {result['total']} 条 raw 记录"
        )

    asyncio.run(_run())


@app.command("merge-duplicates")
def merge_duplicates_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="只统计，不合并"),
) -> None:
    """合并同标题的重复影片（旧 signature 带年份导致同一影片拆成多条）。"""
    from .collector import merge_duplicates

    async def _run() -> None:
        await init_db()
        result = await merge_duplicates(dry_run=dry_run)
        if dry_run:
            console.print(
                f"[yellow]发现 {result['groups']} 组重复标题，将合并 {result['removed']} 条"
                f"（另有 {result['fixed_signatures']} 条旧 signature 需要重键）[/yellow]"
            )
            for ex in result["examples"]:
                console.print(f"  {ex['name']} → 保留 {ex['keep']}，合并 {ex['merge']}")
        else:
            console.print(
                f"[green]合并 {result['groups']} 组[/green] · 删除 {result['removed']} 条重复影片"
                f" · 迁移 {result['moved_items']} 条原始记录 · 重键 {result['fixed_signatures']} 条 signature"
            )

    asyncio.run(_run())


@app.command("add")
def add_source(
    name: str,
    url: str,
    api_format: str = typer.Option("auto", "--format"),
    max_pages: int = typer.Option(5, "--pages"),
    tags: str = typer.Option("", "--tags"),
) -> None:
    """Add one source."""

    async def _run() -> None:
        async with session() as s:
            src = Source(name=name, url=url, api_format=api_format, max_pages=max_pages, tags=tags)
            s.add(src)
            await s.flush()
        console.print(f"[green]added[/green] {name} ({url})")

    asyncio.run(_run())


@app.command("export")
def export(
    out: str = typer.Option("export.jsonl", "--out"),
    limit: int = typer.Option(10000, "--limit"),
    q: str = typer.Option("", "--q"),
) -> None:
    """Export merged videos to JSONL for backup or migration."""

    async def _run() -> None:
        async with session() as s:
            stmt = select(Video).order_by(Video.last_updated.desc()).limit(limit)
            if q:
                stmt = stmt.where(Video.name.like(f"%{q}%"))
            videos = list(await s.scalars(stmt))
            items = {}
            for it in await s.scalars(select(SourceItem).where(SourceItem.video_id.in_([v.id for v in videos]))):
                items.setdefault(it.video_id, []).append(it)
        with open(out, "w", encoding="utf-8") as f:
            for v in videos:
                f.write(json.dumps(
                    {
                        "id": v.id,
                        "name": v.name,
                        "type": v.type_name,
                        "year": v.year,
                        "pic": v.pic,
                        "sources": [
                            {"source_id": i.source_id, "vod_id": i.source_vod_id, "plays": json.loads(i.plays or "[]")}
                            for i in items.get(v.id, [])
                        ],
                    },
                    ensure_ascii=False,
                ) + "\n")
        console.print(f"[green]wrote[/green] {len(videos)} records -> {out}")

    asyncio.run(_run())


images_app = typer.Typer(help="本地海报图片（vod_pic）采集与目录管理")
app.add_typer(images_app, name="images")


@images_app.command("scan")
def images_scan() -> None:
    """登记所有已采集记录里的图片 URL（不联网）。"""
    from .images import scan_images

    async def _run() -> None:
        await init_db()
        stats = await scan_images()
        console.print(f"[green]queued[/green] {stats['queued']} 张图片（扫描 {stats['seen']} 个 pic 字段）")

    asyncio.run(_run())


@images_app.command("download")
def images_download(
    limit: int = typer.Option(None, "--limit", help="本批次下载多少张"),
    concurrency: int = typer.Option(None, "--concurrency"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="也重试之前失败的图片"),
) -> None:
    """下载待采集的图片到本地分桶目录。"""
    from .images import download_images

    async def _run() -> None:
        await init_db()
        result = await download_images(limit=limit, concurrency=concurrency, include_failed=retry_failed)
        console.print(
            f"[green]下载 {result.downloaded}[/green] · 重复 {result.duplicates} · 失败 {result.failed} · "
            f"跳过 {result.skipped} · {result.bytes / 1024 / 1024:.1f} MB · {result.elapsed_ms / 1000:.1f}s"
        )

    asyncio.run(_run())


@images_app.command("stats")
def images_stats() -> None:
    """图片库统计：状态分布、占用空间、分桶数、Top 图片域名。"""
    from .images import image_stats

    async def _run() -> None:
        await init_db()
        data = await image_stats()
        table = Table(title=f"image library ({data['image_dir']})")
        table.add_column("status")
        table.add_column("count", justify="right")
        for status, count in sorted(data["by_status"].items()):
            table.add_row(status, str(count))
        console.print(table)
        console.print(
            f"磁盘文件 {data['files_on_disk']} 个 · 占用 {data['bytes'] / 1024 / 1024:.1f} MB · "
            f"已使用分桶 {data['shards_used']}"
        )
        for host in data["top_hosts"]:
            console.print(f"  {host['host']}: {host['count']}")

    asyncio.run(_run())


@images_app.command("prune")
def images_prune(delete_files: bool = typer.Option(True, "--delete-files/--keep-files")) -> None:
    """删除已不再被任何影片引用的图片记录（并可选删除文件）。"""
    from .images import prune_images

    async def _run() -> None:
        await init_db()
        result = await prune_images(delete_files=delete_files)
        console.print(f"[yellow]removed {result['removed']} image rows, {result['files_deleted']} files[/yellow]")

    asyncio.run(_run())


@images_app.command("repair")
def images_repair() -> None:
    """校对索引与磁盘：文件存在但状态不对的记录修正为 downloaded/duplicate，文件丢失的重新排队。"""
    from .images import repair_images

    async def _run() -> None:
        await init_db()
        result = await repair_images()
        console.print(
            f"[green]修复 {result['restored']} 条状态[/green] · 重新排队 {result['requeued']} 条 · "
            f"删除 {result['files_deleted']} 个孤儿文件"
        )

    asyncio.run(_run())


@images_app.command("layout")
def images_layout(top: int = typer.Option(12, "--top", help="列出文件最多的分桶")) -> None:
    """打印图片目录的分桶结构，方便检查目录是否膨胀。"""
    from pathlib import Path

    root = Path(settings.image_dir)
    if not root.exists():
        console.print(f"[dim]{root} 还不存在，先运行 feicms images download[/dim]")
        return

    shards = [d for d in root.glob("*/*") if d.is_dir()]
    counts = {d: sum(1 for f in d.iterdir() if f.is_file()) for d in shards}
    total = sum(counts.values())
    busiest = max(counts.values(), default=0)

    console.print(
        f"[bold]{root}[/bold] 共 {total} 个文件 · {len(shards)} 个分桶 · 最忙分桶 {busiest} 个文件"
    )
    console.print("[dim]结构: <sha[0:2]>/<sha[2:4]>/<sha>.<ext>（256×256 = 65536 个桶）[/dim]")
    for shard, count in sorted(counts.items(), key=lambda kv: -kv[1])[:top]:
        console.print(f"  {shard.relative_to(root)}/  {count}")


@app.command("passwd")
def set_admin(
    user: str = typer.Option("admin", "--user", help="管理员用户名"),
    password: str = typer.Option(None, "--password", help="管理员密码（留空则随机生成）"),
    env_file: str = typer.Option(None, "--env-file", help="要写入的 .env 文件（默认项目根目录的 .env）"),
) -> None:
    """Write FEICMS_ADMIN_USER / FEICMS_ADMIN_PASSWORD into .env and enable login."""
    from .auth import auth_enabled
    from .config import update_env_file

    if password is None:
        password = secrets.token_urlsafe(12)

    path = update_env_file(
        {"FEICMS_ADMIN_USER": user, "FEICMS_ADMIN_PASSWORD": password},
        Path(env_file) if env_file else None,
    )

    console.print(f"[green]写入[/green] {path}")
    console.print(f"  FEICMS_ADMIN_USER={user}")
    console.print(f"  FEICMS_ADMIN_PASSWORD={password}")
    if not auth_enabled():
        console.print("[dim]当前进程仍无认证；重启服务后生效：./server.sh restart[/dim]")


@app.command("token")
def print_token(ttl: int = typer.Option(3600, "--ttl", help="token 有效期（秒）")) -> None:
    """Print a signed API token for curl / scripts."""
    from .auth import auth_enabled, make_token

    if not auth_enabled():
        console.print("[dim]认证未启用（FEICMS_ADMIN_USER / FEICMS_ADMIN_PASSWORD 为空），无需 token[/dim]")
        return
    console.print(make_token(settings.admin_user, ttl))


@app.command("serve")
def serve(host: str = typer.Option(settings.host, "--host"), port: int = typer.Option(settings.port, "--port")) -> None:
    """Start the FastAPI server."""
    import uvicorn

    asyncio.run(init_db())
    uvicorn.run("feicms.api:app", host=host, port=port)


# -------------------------------------------------------------------------- api keys
apikey_app = typer.Typer(help="管理 API key（给脚本 / AI 客户端使用的长期访问密钥）")
app.add_typer(apikey_app, name="apikey")


@apikey_app.command("create")
def apikey_create(
    name: str = typer.Option("key", "--name", help="key 名称，方便识别用途"),
    scopes: str = typer.Option("read", "--scopes", help="read | write | read,write"),
    expires_days: int = typer.Option(None, "--expires-days", help="有效期天数（留空则永久）"),
) -> None:
    """Create an API key. The plaintext is printed once and never stored."""
    from .apikeys import create_api_key

    async def run() -> None:
        row, plaintext = await create_api_key(name=name, scopes=scopes, expires_days=expires_days)
        console.print(f"[green]created[/green] {plaintext}")
        console.print(f"  id={row.id}  name={row.name}  scopes={row.scopes}  expires={row.expires_at}")
        console.print("[dim]把它保存在安全的地方；数据库里只存 sha256 哈希，无法找回。[/dim]")

    asyncio.run(run())


@apikey_app.command("list")
def apikey_list() -> None:
    """List API keys (hashes are never shown)."""
    from .apikeys import list_api_keys

    async def run() -> None:
        rows = await list_api_keys()
        if not rows:
            console.print("[dim]没有 API key。用 `feicms apikey create --name ai --scopes read` 创建。[/dim]")
            return
        table = Table(title="API keys")
        table.add_column("id")
        table.add_column("name")
        table.add_column("key")
        table.add_column("scopes")
        table.add_column("enabled")
        table.add_column("uses")
        table.add_column("expires")
        for row in rows:
            table.add_row(
                str(row.id),
                row.name,
                f"{row.prefix}…",
                row.scopes,
                "yes" if row.enabled else "no",
                str(row.usage_count),
                row.expires_at.strftime("%Y-%m-%d") if row.expires_at else "never",
            )
        console.print(table)

    asyncio.run(run())


@apikey_app.command("revoke")
def apikey_revoke(key_id: int) -> None:
    """Delete an API key by id."""
    from .apikeys import delete_api_key

    async def run() -> None:
        if await delete_api_key(key_id):
            console.print(f"[green]deleted[/green] key {key_id}")
        else:
            console.print(f"[yellow]key {key_id} not found[/yellow]")

    asyncio.run(run())


@apikey_app.command("update")
def apikey_update(
    key_id: int,
    name: str = typer.Option(None, "--name"),
    scopes: str = typer.Option(None, "--scopes", help="read | write | read,write"),
    enabled: bool = typer.Option(None, "--enabled/--disabled"),
    expires_days: int = typer.Option(None, "--expires-days"),
) -> None:
    """Change a key's name, scopes or enabled state."""
    from .apikeys import update_api_key

    async def run() -> None:
        row = await update_api_key(key_id, name=name, scopes=scopes, enabled=enabled, expires_days=expires_days)
        if row is None:
            console.print(f"[yellow]key {key_id} not found[/yellow]")
            return
        console.print(f"[green]updated[/green] id={row.id} name={row.name} scopes={row.scopes} enabled={row.enabled}")

    asyncio.run(run())


@app.command("mcp")
def mcp_stdio() -> None:
    """Run the MCP server over stdio (for Claude Desktop / OpenCode / MCP clients)."""
    from .mcp import serve_stdio

    serve_stdio()


@app.command("mcp-config")
def mcp_config(
    url: str = typer.Option(None, "--url", help="HTTP MCP endpoint, e.g. http://127.0.0.1:8000/mcp"),
) -> None:
    """Print a ready-to-paste MCP client configuration."""
    from .mcp import mcp_config_json

    console.print(mcp_config_json(url))


if __name__ == "__main__":
    app()
