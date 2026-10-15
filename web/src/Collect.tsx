import React from "react";
import { api, type CollectProgress, type SourceProgress } from "./api";

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
  const [pages, setPages] = React.useState("");
  const [filter, setFilter] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    api
      .collectProgress()
      .then(setProgress)
      .catch((e) => notify((e as Error).message, "error"));
  }, [notify]);

  React.useEffect(() => {
    load();
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [load]);

  const pageCount = pages === "" ? undefined : Number(pages);

  const run = async (body: { pages?: number; source_ids?: number[] }, message: (state: CollectProgress) => string) => {
    setBusy(true);
    try {
      const state = await api.collectStart(body);
      setProgress(state);
      if (state.error) notify(`采集失败：${state.error}`, "error");
      else notify(message(state));
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const startAll = () =>
    run({ pages: pageCount }, (state) => `采集已启动：${state.total_sources} 个站点，后台进行中`);

  const collectOne = (s: SourceProgress, full = false) =>
    run(
      { source_ids: [s.source_id], pages: full ? 0 : pageCount },
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
          <option value="0">全量扫描所有页</option>
        </select>
        <button className="btn btn-sm btn-primary" onClick={startAll} disabled={busy || progress?.running}>
          {busy || progress?.running ? "采集中…" : "采集全部"}
        </button>
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
