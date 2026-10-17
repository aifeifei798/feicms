import React from "react";
import {
  api,
  type CollectProgress,
  type CollectResume,
  type RefreshStats,
  type RefreshState,
  type SourceProgress,
} from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

const STATUS: Record<string, { label: string; badge: string }> = {
  pending: { label: "待采集", badge: "badge badge-ghost" },
  running: { label: "采集中", badge: "badge badge-primary" },
  done: { label: "完成", badge: "badge badge-success" },
  failed: { label: "失败", badge: "badge badge-error" },
};

const STATUS_ORDER: Record<string, number> = { running: 0, pending: 1, done: 2, failed: 3 };

function totalPages(s: SourceProgress): number {
  // The bar measures this run's plan (how many pages we asked for), not the
  // site's total catalogue, otherwise "1 page of 3651" would look like 0%.
  const target = s.target || 0;
  const reported = s.pagecount || 0;
  if (target && reported) return Math.min(target, reported);
  return target || reported || 0;
}

function percent(s: SourceProgress): number {
  const total = totalPages(s);
  if (total <= 0) return s.status === "done" ? 100 : 0;
  return Math.min(100, Math.round((s.pages / total) * 100));
}

export default function Collect({ notify }: { notify: Notify }) {
  const [progress, setProgress] = React.useState<CollectProgress | null>(null);
  const [checkpoint, setCheckpoint] = React.useState<CollectResume | null>(null);
  const [pages, setPages] = React.useState("");
  const [resume, setResume] = React.useState(true);
  const [filter, setFilter] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [refreshStats, setRefreshStats] = React.useState<RefreshStats | null>(null);
  const [refreshState, setRefreshState] = React.useState<RefreshState | null>(null);
  const [refreshDays, setRefreshDays] = React.useState("");
  const [refreshLimit, setRefreshLimit] = React.useState("");
  const [refreshBusy, setRefreshBusy] = React.useState(false);

  const load = React.useCallback(() => {
    api
      .refreshStats()
      .then(setRefreshStats)
      .catch(() => setRefreshStats(null));
    api
      .refreshProgress()
      .then(setRefreshState)
      .catch(() => setRefreshState(null));
    api
      .collectProgress()
      .then(setProgress)
      .catch((e) => notify((e as Error).message, "error"));
    api
      .collectResume()
      .then(setCheckpoint)
      .catch(() => setCheckpoint(null));
  }, [notify]);

  React.useEffect(() => {
    load();
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [load]);

  const pageCount = pages === "" ? undefined : Number(pages);
  const fullSweep = pageCount === 0;

  const run = async (
    body: { pages?: number; source_ids?: number[]; names?: string[]; resume?: boolean },
    message: (state: CollectProgress) => string
  ) => {
    setBusy(true);
    try {
      const state = await api.collectStart(body);
      setProgress(state);
      if (state.error) notify(`采集失败：${state.error}`, "error");
      else notify(message(state));
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const startAll = () =>
    run(
      { pages: pageCount, resume: fullSweep ? resume : undefined },
      (state) =>
        `采集已启动：${state.total_sources} 个站点，后台进行中` +
        (fullSweep && resume && checkpoint?.exists && !checkpoint.done ? "（从断点续传）" : "")
    );

  const clearCheckpoint = async () => {
    try {
      await api.collectResumeClear();
      setCheckpoint({
        exists: false,
        done: false,
        started_at: null,
        updated_at: null,
        sources: 0,
        finished: 0,
        detail: [],
        pending: [],
      });
      notify("断点已清除，下次全量从头开始");
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const collectOne = (s: SourceProgress, full = false) =>
    run(
      { source_ids: [s.source_id], pages: full ? 0 : pageCount, resume: full ? resume : undefined },
      () => `已启动 ${s.name} 的${full ? "全量" : ""}采集`
    );

  const retryFailed = () => {
    const ids = (progress?.sources ?? []).filter((s) => s.status === "failed").map((s) => s.source_id);
    if (ids.length === 0) {
      notify("没有失败的站点", "error");
      return;
    }
    run({ source_ids: ids }, () => `重试 ${ids.length} 个失败站点，后台进行中`);
  };

  const runRefresh = async (dryRun = false) => {
    setRefreshBusy(true);
    try {
      const state = await api.refresh({
        days: refreshDays ? Number(refreshDays) : undefined,
        limit: refreshLimit ? Number(refreshLimit) : undefined,
        dry_run: dryRun,
      });
      setRefreshState(state);
      notify(dryRun ? "补扫预览已启动，后台进行中" : "定向补扫已启动，后台进行中");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setRefreshBusy(false);
    }
  };

  const rows = (progress?.sources ?? [])
    .filter((s) => {
      if (!filter) return true;
      if (filter === "idle") return !s.current;
      if (filter === "disabled") return !s.enabled;
      return s.current && s.status === filter;
    })
    .sort(
      (a, b) =>
        Number(b.current) - Number(a.current) ||
        Number(b.enabled) - Number(a.enabled) ||
        (STATUS_ORDER[a.status] ?? 4) - (STATUS_ORDER[b.status] ?? 4) ||
        a.source_id - b.source_id
    );

  const overall = progress
    ? Math.round((progress.done_sources / Math.max(1, progress.total_sources)) * 100)
    : 0;

  return (
    <section>
      <div className="toolbar">
        <select className="select select-sm" value={pages} onChange={(e) => setPages(e.target.value)}>
          <option value="">默认页数（每站 max_pages）</option>
          <option value="1">1 页</option>
          <option value="3">3 页</option>
          <option value="5">5 页</option>
          <option value="0">全量（每站真实页数）</option>
        </select>
        <button className="btn btn-sm btn-primary" onClick={startAll} disabled={busy || progress?.running}>
          {busy || progress?.running ? "采集中…" : "采集全部"}
        </button>
        {pages === "0" && (
          <>
            <label className="label cursor-pointer gap-2 py-0">
              <input
                type="checkbox"
                className="checkbox checkbox-sm"
                checked={resume}
                disabled={busy || progress?.running}
                onChange={(e) => setResume(e.target.checked)}
              />
              <span className="label-text text-xs">断点续传</span>
            </label>
            <span className="muted">全量按每个站真实报告的所有页遍历，常见站点 3000～8000 页，一轮可能几十分钟</span>
          </>
        )}
        {progress?.failed_sources ? (
          <button className="btn btn-sm btn-outline" onClick={retryFailed} disabled={busy || progress?.running}>
            重试失败的 {progress.failed_sources} 个
          </button>
        ) : null}
        <button className="btn btn-sm btn-outline" onClick={load}>刷新</button>
        <span className="muted">
          {progress?.running ? "后台采集进行中" : progress?.total_sources ? "上次采集" : "尚未采集"}
        </span>
      </div>

      <p className="cli-hint muted">
        不想开网页：在服务器上跑 <code>./server.sh full</code>（默认断点续传，中断后再跑会接着扫），
        日志 <code>data/collect.log</code>，进度 <code>./server.sh full-status</code>，停止 <code>./server.sh full-stop</code>
      </p>

      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <h2 className="card-title">定向补扫（老片子的播放源）</h2>
          <p className="muted">
            普通采集只走每个站的前几页，名字没变、播放源变了的老片子躺在几百页之后，扫不到。
            定向补扫用 <code>ac=detail&amp;ids=</code> 按 vod_id 直接抓详情，只核对最老的、或播放源已经为空的那部分。
          </p>
          <div className="toolbar">
            <label className="label mr-2">
              <span className="muted">超过多少天没核对</span>
              <input
                className="input input-bordered input-sm"
                type="number"
                min="0"
                value={refreshDays}
                placeholder={String(refreshStats?.overdue_days ?? 30)}
                onChange={(e) => setRefreshDays(e.target.value)}
              />
            </label>
            <label className="label mr-2">
              <span className="muted">本轮核对多少条</span>
              <input
                className="input input-bordered input-sm"
                type="number"
                min="0"
                value={refreshLimit}
                placeholder="5000"
                onChange={(e) => setRefreshLimit(e.target.value)}
              />
            </label>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => runRefresh(false)}
              disabled={refreshBusy || progress?.running}
            >
              {refreshBusy ? "启动中…" : "定向补扫"}
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => runRefresh(true)}
              disabled={refreshBusy || progress?.running}
            >
              预览（不写库）
            </button>
            <span className="muted">
              {refreshState?.running
                ? `补扫进行中：已核对 ${refreshState.checked} 条，更新 ${refreshState.updated} 条，覆盖 ${refreshState.sources} 个站点`
                : refreshState?.finished_at
                ? `上次补扫 ${new Date(refreshState.finished_at).toLocaleString()}：核对 ${refreshState.checked} 条，更新 ${refreshState.updated} 条，上游已删 ${refreshState.gone} 条`
                : "尚未补扫"}
            </span>
          </div>
          <p className="muted mt-2">
            待核对 {refreshStats?.never_checked ?? 0} 条（其中播放源为空 {refreshStats?.empty_plays ?? 0} 条），
            最老的一条更新于 {refreshStats?.oldest_video_updated_at ? new Date(refreshStats.oldest_video_updated_at).toLocaleString() : "—"}
          </p>
        </div>
      </div>

      {checkpoint?.exists ? (
        <p className={checkpoint.done ? "cli-hint muted" : "cli-hint"}>
          <span className={checkpoint.done ? "muted" : "text-warning"}>断点续传：</span>
          {checkpoint.done
            ? `上次全量已跑完（${checkpoint.finished}/${checkpoint.sources} 个站点），下次全量会重新开始`
            : `上次全量没跑完，${checkpoint.finished}/${checkpoint.sources} 个站点已完成，其余会从记录页继续`}
          {!checkpoint.done && resume ? "（本轮勾了断点续传）" : ""}
          {!checkpoint.done && !resume ? "（本轮未勾断点续传，会从第 1 页重扫）" : ""}
          <button className="btn btn-xs btn-ghost" onClick={clearCheckpoint} disabled={busy || progress?.running}>
            清除断点
          </button>
          {!checkpoint.done && checkpoint.pending.length ? (
            <span className="muted">
              · 待续传：
              {checkpoint.pending
                .slice(0, 4)
                .map((d) => `${d.name}(第${d.next_page}页)`)
                .join("、")}
              {checkpoint.pending.length > 4 ? ` 等 ${checkpoint.pending.length} 个` : ""}
            </span>
          ) : null}
        </p>
      ) : null}

      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <div className="sweep-summary">
            <div className="sweep-bar" aria-hidden="true">
              <i style={{ width: `${overall}%` }} />
            </div>
            <span className="progress-count">
              {progress ? `本轮 ${progress.done_sources}/${progress.total_sources} 个站点` : "0/0"}
            </span>
            <span className="badge badge-success">新增 {progress?.inserted ?? 0}</span>
            <span className="badge badge-outline">更新 {progress?.updated ?? 0}</span>
            <span className="badge badge-outline">跳过 {progress?.skipped ?? 0}</span>
            {progress?.failed_sources ? (
              <span className="badge badge-error">失败 {progress.failed_sources}</span>
            ) : null}
            {progress?.started_at && (
              <span className="muted">开始 {new Date(progress.started_at).toLocaleString()}</span>
            )}
          </div>

          <div className="toolbar">
            <select className="select select-sm" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">全部站点</option>
              <option value="running">采集中</option>
              <option value="pending">待采集</option>
              <option value="done">完成</option>
              <option value="failed">失败</option>
              <option value="idle">本轮未采集</option>
              <option value="disabled">已禁用</option>
            </select>
            <span className="muted">{rows.length} 个站点</span>
          </div>

          <table className="table">
            <thead>
              <tr>
                <th>站点</th>
                <th>状态</th>
                <th>进度</th>
                <th>新增</th>
                <th>更新</th>
                <th>跳过</th>
                <th>耗时</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.source_id} className={s.current ? "" : "opacity-60"}>
                  <td>{s.name}</td>
                  <td>
                    {!s.enabled ? (
                      <span className="badge badge-ghost">已禁用</span>
                    ) : s.current ? (
                      <span className={STATUS[s.status]?.badge ?? "badge badge-ghost"}>
                        {STATUS[s.status]?.label ?? s.status}
                      </span>
                    ) : (
                      <span className="badge badge-ghost">未采集</span>
                    )}
                  </td>
                  <td>
                    <div className="source-progress">
                      <div className="progress-bar" aria-hidden="true">
                        <i style={{ width: `${s.current ? percent(s) : 0}%` }} />
                      </div>
                      <span className="muted">{s.current ? `${s.pages}/${totalPages(s) || "?"} 页` : "-"}</span>
                    </div>
                    {s.current && (s.stopped_reason || s.warnings) && (
                      <div className="progress-note muted">
                        {s.stopped_reason ?? `跳过 ${s.warnings} 页`}
                      </div>
                    )}
                  </td>
                  <td>{s.inserted}</td>
                  <td>{s.updated}</td>
                  <td>{s.skipped}</td>
                  <td className="muted">{s.elapsed_ms ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : "-"}</td>
                  <td>
                    <div className="flex gap-1">
                      <button
                        className="btn btn-xs btn-outline"
                        onClick={() => collectOne(s)}
                        disabled={progress?.running || !s.enabled}
                        title="采集该站点（默认页数）"
                      >
                        采集
                      </button>
                      <button
                        className="btn btn-xs btn-ghost"
                        onClick={() => collectOne(s, true)}
                        disabled={progress?.running || !s.enabled}
                        title="遍历该站点所有页"
                      >
                        全量
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td className="muted">暂无采集进度，点「采集全部」开始</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
