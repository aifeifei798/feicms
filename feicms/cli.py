from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from .config import BASE_DIR, settings, update_env_file
from .collector import (
    clear_resume,
    collect_all,
    collect_lock_is_stale,
    collect_source,
    resume_file_path,
    resume_summary,
)
from .db import init_db, rebuild_fts, session
from .embeddings import rebuild_embeddings
from .health import health_check_all, health_rank
from .refresh import pick_refresh_targets, refresh_stale, refresh_summary
from .adult import adult_condition, adult_name_keywords, adult_type_keywords, matched_keyword
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


@app.command("reindex")
def reindex_cmd() -> None:
    """Rebuild the title search index (one pass over the library)."""
    asyncio.run(rebuild_fts())
    console.print("[green]title search index rebuilt[/green]")


@app.command("embed")
def embed_cmd(
    force: bool = typer.Option(False, "--force", help="Recompute all vectors"),
    batch_size: int = typer.Option(64, "--batch-size"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    """Build semantic-search embeddings for the library."""
    result = asyncio.run(rebuild_embeddings(batch_size=batch_size, force=force, limit=limit))
    console.print(result)


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
            sources = list(
                await s.scalars(
                    stmt.order_by(health_rank(), Source.priority.desc(), Source.id).limit(limit)
                )
            )
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
    concurrency: int = typer.Option(
        None, "--concurrency", help="override FEICMS_CONCURRENCY for this run (0 = every source at once)"
    ),
    delay: float = typer.Option(None, "--delay", help="override FEICMS_DELAY: seconds between page requests"),
    heartbeat: int = typer.Option(20, "--heartbeat", help="print a progress line every N pages (0 = off)"),
    health_first: bool = typer.Option(
        True,
        "--health-first/--no-health-first",
        help="全量扫描前先做一次健康检查：恢复能连的源、禁用挂掉的源，再扫剩下的",
    ),
    resume: bool = typer.Option(
        None,
        "--resume/--no-resume",
        help="全量扫描断点续传：跳过上次已跑完的站点、从记录的那一页继续（默认跟随 FEICMS_RESUME）",
    ),
) -> None:
    """Run collection now (all enabled sources, or one by --name).

    `--full` walks every page each source reports. It is meant to be left running
    on the server: `./server.sh full` starts it in the background. An interrupted
    full sweep resumes from its checkpoint unless `--no-resume` is given.
    """

    async def _run() -> None:
        if concurrency is not None:
            settings.concurrency = concurrency
        if delay is not None:
            settings.request_delay = delay

        names = [name] if name else None
        effective_pages = 0 if full else pages
        effective_resume = settings.resume if resume is None else resume
        lock_path = Path(settings.lock_file)
        if lock_path.exists() and collect_lock_is_stale():
            console.print(
                "[yellow]上一次扫描留下了锁文件，但那个进程已经不在了，本轮会直接接管。[/yellow]"
            )

        # 全量扫描前先 probe 一次（恢复能用的源、禁用挂掉的源），避免盲扫死源。
        run_health_before = bool(health_first and (full or pages in (None, 0)))
        if run_health_before:
            console.print("[cyan]健康检查：先把所有源重新启用（探活），挂掉的禁用…[/cyan]")
            try:
                hr = await health_check_all(reenable=True, disable_on_fail=True, timeout=8.0, retries=1)
                ok_cnt = sum(1 for r in hr if r.ok)
                console.print(
                    f"[green]健康检查完成[/green] {ok_cnt}/{len(hr)} 可用，剩下的禁用掉再跑"
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]健康检查失败（{exc}），继续采集[/yellow]")

        started_at = datetime.now(timezone.utc)
        running = True
        live: dict[int, dict[str, Any]] = {}
        finished: list[dict[str, Any]] = []
        status_path = Path(settings.status_file)
        # 断点续传: captured before the sweep overwrites the checkpoint file, so
        # `full-status` can still show what the interrupted run had reached.
        resume_info: dict[str, Any] | None = (
            resume_summary() if (effective_pages == 0 and effective_resume) else None
        )

        def write_status() -> None:
            payload = {
                "running": running,
                "started_at": started_at.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_sources": len(live),
                "done_sources": len(finished),
                "pages": sum(s.get("pages", 0) for s in live.values()),
                "inserted": sum(s.get("inserted", 0) for s in live.values()),
                "updated": sum(s.get("updated", 0) for s in live.values()),
                "skipped": sum(s.get("skipped", 0) for s in live.values()),
                "sources": sorted(live.values(), key=lambda s: s.get("pages", 0), reverse=True),
                "finished": finished,
                "resume": resume_info,
            }
            try:
                status_path.parent.mkdir(parents=True, exist_ok=True)
                status_path.write_text(json.dumps(payload, ensure_ascii=False))
            except OSError:
                pass

        def on_progress(source_id: int, snap: dict[str, Any]) -> None:
            entry = live.setdefault(source_id, {"source_id": source_id, "name": snap.get("name", source_id)})
            entry.update(
                {
                    "pages": snap.get("pages", 0),
                    "pagecount": snap.get("pagecount", 0),
                    "target": snap.get("target", 0),
                    "inserted": snap.get("inserted", 0),
                    "updated": snap.get("updated", 0),
                    "skipped": snap.get("skipped", 0),
                }
            )
            if entry["pages"] == 0:
                console.print(f"[dim]开始 {entry['name']}[/dim]")
                write_status()
                return
            if not heartbeat or entry["pages"] % heartbeat:
                return
            total = entry["pagecount"] or "?"
            console.print(
                f"[dim]进度 {entry['name']}: {entry['pages']}/{total} 页 "
                f"(新增 {entry['inserted']}, 更新 {entry['updated']})[/dim]"
            )
            write_status()

        def on_result(r: CollectResult) -> None:
            status = "done" if not (r.error or r.stopped_reason) else "failed"
            tag = "[green]完成[/green]" if status == "done" else "[red]失败[/red]"
            scope = f"{r.pages}/{r.pagecount or '?'}页"
            note = r.error or r.stopped_reason or ""
            if r.warnings:
                note += f" (跳过 {len(r.warnings)} 页)"
            console.print(
                f"{tag} {r.source_name}: {scope} new={r.inserted} updated={r.updated} "
                f"skipped={r.skipped} total={r.total} {r.elapsed_ms / 1000:.1f}s {note}"
            )
            try:
                live[r.source_id] = {
                    "source_id": r.source_id,
                    "name": r.source_name,
                    "pages": r.pages,
                    "pagecount": r.pagecount,
                    "inserted": r.inserted,
                    "updated": r.updated,
                    "skipped": r.skipped,
                    "status": status,
                    "note": note,
                }
                finished.append({"name": r.source_name, "status": status, "note": note})
                write_status()
            except Exception:  # noqa: BLE001 - status bookkeeping must never kill a sweep
                pass

        console.print(
            f"[green]采集开始[/green] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"{'全量（每站真实页数）' if effective_pages == 0 else f'{effective_pages or settings.default_pages} 页/站'}"
        )
        if resume_info and resume_info.get("exists") and not resume_info.get("done"):
            console.print(
                f"[yellow]断点续传[/yellow] 上次记录 {resume_info['sources']} 个站点，"
                f"其中 {resume_info['finished']} 个已跑完（将跳过），其余从记录页继续"
            )
        elif effective_pages == 0 and not effective_resume:
            console.print("[dim]已禁用断点续传：本轮从第 1 页重新开始[/dim]")
        write_status()

        results = await collect_all(
            names=names,
            pages=effective_pages,
            only_enabled=not include_disabled,
            on_result=on_result,
            on_progress=on_progress,
            resume=effective_resume,
        )
        running = False
        resume_info = resume_summary() if effective_pages == 0 else None
        write_status()
        if not results:
            if Path(settings.lock_file).exists():
                console.print("[yellow]另一轮采集正在进行中，本轮未启动。[/yellow]")
            else:
                console.print("[yellow]没有选中任何可采集的站点（检查 --name / --include-disabled）[/yellow]")
            return
        done = sum(1 for r in results if not (r.error or r.stopped_reason))
        console.print(
            f"[green]完成[/green] {done}/{len(results)} 个站点，"
            f"新增 {sum(r.inserted for r in results)}，更新 {sum(r.updated for r in results)}，"
            f"跳过 {sum(r.skipped for r in results)} 条重复"
        )
        if effective_pages == 0 and resume_info and not resume_info.get("done"):
            console.print(
                "[yellow]本轮没跑完[/yellow]（断点已保存，直接再跑一次会自动续传；"
                "想从头开始加 --no-resume）"
            )

    asyncio.run(_run())


@app.command("refresh")
def refresh_cmd(
    days: int = typer.Option(
        None, "--days", help="超过多少天没核对的记录才补扫（默认 FEICMS_REFRESH_DAYS）"
    ),
    limit: int = typer.Option(
        None, "--limit", help="本轮最多核对多少条记录（默认 FEICMS_REFRESH_LIMIT）"
    ),
    name: str = typer.Option(None, "--name", help="只补扫某一个站点"),
    batch: int = typer.Option(
        None, "--batch", help="每次 ids 查询带多少个 vod_id（默认 FEICMS_REFRESH_BATCH）"
    ),
    concurrency: int = typer.Option(
        None, "--concurrency", help="同时补扫的站点数（0 = 全部站点同时）"
    ),
    delay: float = typer.Option(None, "--delay", help="每批之间的间隔秒数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只统计要补扫多少条，不写库"),
    include_disabled: bool = typer.Option(False, "--include-disabled"),
    heartbeat: int = typer.Option(5, "--heartbeat", help="每 N 批打印一次进度（0 = 关闭）"),
) -> None:
    """定向补扫：按 vod_id 直接抓详情，刷新普通采集扫不到的老记录。

    普通采集只走每个站的前 FEICMS_PAGES 页，名字没变但播放源变了的老片子躺在几百页
    之后，永远扫不到。这里用 `ac=detail&ids=<vod_id>` 只抓需要核对的那几条。
    """

    async def _run() -> None:
        await init_db()
        targets = await pick_refresh_targets(
            days=days if days is not None else settings.refresh_days,
            limit=limit if limit is not None else settings.refresh_limit,
            names=[name] if name else None,
            only_enabled=not include_disabled,
        )
        total = sum(len(ids) for ids in targets.values())
        if not total:
            console.print("[green]没有需要补扫的记录[/green]")
            return
        console.print(
            f"[cyan]定向补扫[/cyan] {total} 条记录，{len(targets)} 个站点，"
            f"每批 {batch or settings.refresh_batch} 个 id"
        )

        def on_progress(source_id: int, snap: dict[str, Any]) -> None:
            if heartbeat and snap.get("batches", 0) % heartbeat == 0:
                console.print(
                    f"[dim]  {source_id}: 已抓 {snap.get('fetched', 0)} 条，"
                    f"更新 {snap.get('updated', 0)}，新增 {snap.get('inserted', 0)}，"
                    f"上游已删 {snap.get('gone', 0)}[/dim]"
                )

        results = await refresh_stale(
            days=days,
            limit=limit,
            names=[name] if name else None,
            batch=batch,
            concurrency=concurrency,
            delay=delay,
            dry_run=dry_run,
            only_enabled=not include_disabled,
            on_progress=on_progress,
        )
        if not results:
            console.print("[yellow]另一轮采集正在进行中，本轮未启动。[/yellow]")
            return

        table = Table(title="定向补扫结果")
        table.add_column("站点")
        table.add_column("请求", justify="right")
        table.add_column("抓到", justify="right")
        table.add_column("更新", justify="right")
        table.add_column("新增", justify="right")
        table.add_column("上游已删", justify="right")
        table.add_column("耗时", justify="right")
        for r in results:
            table.add_row(
                r.source_name,
                str(r.requested),
                str(r.fetched),
                str(r.updated),
                str(r.inserted),
                str(r.gone),
                f"{r.elapsed_ms / 1000:.1f}s",
            )
        console.print(table)
        unsupported = [r.source_name for r in results if r.unsupported]
        if unsupported:
            console.print(
                f"[yellow]这些站点不支持按 id 抓取：{', '.join(unsupported)}[/yellow]"
            )
        console.print(
            f"[green]完成[/green] 核对 {sum(r.requested for r in results)} 条，"
            f"更新 {sum(r.updated for r in results)}，新增 {sum(r.inserted for r in results)}，"
            f"上游已删 {sum(r.gone for r in results)}"
        )

    asyncio.run(_run())


@app.command("refresh-stats")
def refresh_stats_cmd() -> None:
    """定向补扫的进度：还有多少记录没核对过。"""

    async def _run() -> None:
        await init_db()
        info = await refresh_summary()
        for key, value in info.items():
            console.print(f"  {key}: {value}")

    asyncio.run(_run())


@app.command("resume")
def resume_cmd(
    clear: bool = typer.Option(False, "--clear", help="删除断点记录，下一次全量扫描从头开始"),
    detail: bool = typer.Option(False, "--detail", help="列出每个站点记录到第几页"),
) -> None:
    """查看 / 清除全量扫描的断点续传记录。"""
    if clear:
        clear_resume()
        console.print(f"[green]已清除断点[/green] {resume_file_path()}")
        return
    info = resume_summary()
    if not info["exists"]:
        console.print(f"[dim]没有断点记录（{resume_file_path()} 不存在）[/dim]")
        return
    state = "已完成（下次全量会从头开始）" if info["done"] else "未跑完（下次全量会续传）"
    console.print(f"断点文件: {resume_file_path()}")
    console.print(
        f"状态: {state}  开始: {info['started_at'] or '?'}  更新: {info['updated_at'] or '?'}"
    )
    console.print(f"记录站点: {info['sources']} 个，其中已跑完 {info['finished']} 个")
    if detail and info["pending"]:
        table = Table("待续传站点", "从第几页继续", "已采集页", "总页数", "新增", "更新")
        for entry in info["pending"]:
            table.add_row(
                str(entry.get("name") or entry.get("source_id")),
                str(entry.get("next_page") or 1),
                str(entry.get("pages") or 0),
                str(entry.get("pagecount") or "?"),
                str(entry.get("inserted") or 0),
                str(entry.get("updated") or 0),
            )
        console.print(table)
    elif detail:
        console.print("[dim]没有待续传的站点[/dim]")


@app.command("health")
def health() -> None:
    """Probe every enabled source and record latency/availability."""

    async def _run() -> None:
        results = await health_check_all(reenable=True, disable_on_fail=True)
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


@app.command("adult")
def adult_filter(
    on: bool = typer.Option(False, "--on", help="开启前台成人内容屏蔽（写入 .env）"),
    off: bool = typer.Option(False, "--off", help="关闭前台成人内容屏蔽（写入 .env）"),
    refresh: bool = typer.Option(False, "--refresh", help="重新计算 videos.is_adult 标记（关键词改动后）"),
    show: int = typer.Option(15, "--show", help="列出被屏蔽的影片条数"),
) -> None:
    """Report what the adult-content filter catches on the public front site."""
    if on or off:
        values = {"FEICMS_HIDE_ADULT": "true" if on else "false"}
        path = update_env_file(values)
        settings.hide_adult = on
        console.print(f"FEICMS_HIDE_ADULT={values['FEICMS_HIDE_ADULT']} 已写入 {path}")

    async def _run() -> None:
        from .adult import refresh_adult_flags

        if refresh:
            result = await refresh_adult_flags(force=True)
            console.print(f"标记重新计算: {result}")

        async with session() as s:
            total = await s.scalar(select(func.count(Video.id)))
            condition = Video.is_adult.is_(True)
            adult = await s.scalar(select(func.count(Video.id)).where(condition))
            rows = list(
                await s.scalars(
                    select(Video).where(condition).order_by(Video.last_updated.desc()).limit(show)
                )
            )
            types = [
                (name or "未分类", count)
                for name, count in await s.execute(
                    select(Video.type_name, func.count(Video.id))
                    .where(condition)
                    .group_by(Video.type_name)
                    .order_by(func.count(Video.id).desc())
                    .limit(15)
                )
            ]

        console.print(
            f"屏蔽状态: {'开启' if settings.hide_adult else '关闭'}  "
            f"命中 {adult}/{total} 条  分类关键词 {len(adult_type_keywords())} 个  标题关键词 {len(adult_name_keywords())} 个"
        )
        if not types:
            console.print("没有命中任何记录")
            return
        table = Table(title="被屏蔽的分类")
        table.add_column("分类")
        table.add_column("条数", justify="right")
        for name, count in types:
            table.add_row(name, str(count))
        console.print(table)
        for video in rows:
            console.print(f"  {video.id} {video.name} [{matched_keyword(video)}]")

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
def repair_plays_cmd(
    batch: int = typer.Option(2000, "--batch", help="每批处理多少条（避免一次性载入全库）"),
) -> None:
    """用当前播放源解析规则重建 source_items.plays（不重新采集，只重新解析 raw 字段）。"""
    from .collector import repair_plays

    async def _run() -> None:
        await init_db()
        result = await repair_plays(batch=batch)
        console.print(
            f"[green]重建 {result['changed']} 条播放源[/green] · 共 {result['total']} 条 raw 记录"
        )

    asyncio.run(_run())


@app.command("compact-raw")
def compact_raw_cmd() -> None:
    """清理已解析出结构化 plays 的行里的冗余 raw play_url，然后 VACUUM 收缩数据库。"""
    from .collector import compact_raw_plays

    async def _run() -> None:
        await init_db()
        result = await compact_raw_plays()
        console.print(f"[green]清理 {result['cleared']} 条冗余 play_url[/green]")

    asyncio.run(_run())


@app.command("pack-plays")
def pack_plays_cmd(
    batch: int = typer.Option(5000, "--batch", help="每批写回多少条"),
) -> None:
    """把 source_items.plays 压缩存储（zlib），播放源 JSON 通常能缩小到 1/6。"""
    from .collector import pack_plays

    async def _run() -> None:
        await init_db()
        result = await pack_plays(batch=batch)
        console.print(
            f"[green]压缩 {result['packed']} 条播放源[/green] · 扫描 {result['scanned']} 条 · "
            f"省 {result['saved_bytes'] / 1e6:.0f} MB"
        )

    asyncio.run(_run())


@app.command("optimize")
def optimize_cmd() -> None:
    """一键优化：删重复索引 + 清冗余 play_url + 压缩 plays + VACUUM + ANALYZE。"""
    from .collector import optimize_database

    async def _run() -> None:
        result = await optimize_database()
        saved = result["saved"]
        console.print(
            f"[green]优化完成[/green] · 清理 {result['cleared_play_url']} 条 play_url · "
            f"压缩 {result['packed_plays']} 条 plays · "
            f"删除 {len(result['dropped_indexes'])} 个重复索引"
        )
        if result["dropped_indexes"]:
            console.print("  删除的索引: " + ", ".join(result["dropped_indexes"]))
        if not result["vacuumed"]:
            console.print("[yellow]VACUUM 未执行[/yellow]（数据库被占用）：停服务后再跑一次 optimize")
        console.print(
            f"数据库 {result['bytes_before'] / 1e9:.2f} GB -> {result['bytes_after'] / 1e9:.2f} GB "
            f"[green]省 {saved / 1e6:.0f} MB[/green]"
        )
        console.print(
            f"plays 列现在 {result['plays_bytes'] / 1e6:.0f} MB "
            f"（{result['packed_rows']} 条已压缩 / {result['plain_rows']} 条仍为明文）"
        )

    asyncio.run(_run())


@app.command("db-size")
def db_size_cmd() -> None:
    """数据库体积体检：文件大小、plays 压缩情况、重复索引。"""
    from .db import _REDUNDANT_INDEXES
    from .storage import database_bytes, database_path, packing_report

    async def _run() -> None:
        path = database_path()
        report = await packing_report()
        async with session() as s:
            from sqlalchemy import text

            names = {
                row[0]
                for row in await s.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                )
            }
            play_url_bytes = (
                await s.scalar(
                    text("SELECT COALESCE(SUM(length(play_url)), 0) FROM source_items WHERE play_url <> ''")
                )
            ) or 0
        table = Table(title="数据库体积")
        table.add_column("项目", style="cyan")
        table.add_column("值")
        table.add_column("说明")
        table.add_row("文件", f"{database_bytes() / 1e9:.2f} GB", path or "(内存)")
        table.add_row("source_items", f"{report['rows']} 条", "")
        table.add_row(
            "plays 已压缩",
            f"{report['packed']} 条",
            f"共 {report['stored_bytes'] / 1e6:.0f} MB 在磁盘上",
        )
        table.add_row("plays 未压缩", f"{report['plain']} 条", "跑 `optimize` 或 `pack-plays` 压缩")
        table.add_row("play_url 残留", f"{play_url_bytes} 字节", "跑 `compact-raw` 清理")
        leftovers = [
            name
            for _t, name, keeper in _REDUNDANT_INDEXES
            if name in names and keeper in names
        ]
        table.add_row("重复索引", ", ".join(leftovers) or "无", "跑 `optimize` 自动删除")
        console.print(table)

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
