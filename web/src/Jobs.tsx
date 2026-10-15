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

export default function Jobs({ notify }: { notify: Notify }) {
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

  const start = async () => {
    setBusy(true);
    try {
      const state = await api.collectStart({ pages: pages === "" ? undefined : Number(pages) });
      setProgress(state);
      if (state.error) notify(`采集失败：${state.error}`, "error");
      else notify(`采集已启动：${state.total_sources} 个站点，后台进行中`);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const rows = (progress?.sources ?? [])
    .filter((s) => !filter || s.status === filter)
    .sort(
      (a, b) =>
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
        <button className="btn btn-sm btn-primary" onClick={start} disabled={busy || progress?.running}>
          {busy || progress?.running ? "采集中…" : "采集全部"}
        </button>
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
              {progress ? `${progress.done_sources}/${progress.total_sources} 个站点` : "0/0"}
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
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.source_id}>
                  <td>{s.name}</td>
                  <td>
                    <span className={STATUS[s.status]?.badge ?? "badge badge-ghost"}>
                      {STATUS[s.status]?.label ?? s.status}
                    </span>
                  </td>
                  <td>
                    <div className="source-progress">
                      <div className="progress-bar" aria-hidden="true">
                        <i style={{ width: `${percent(s)}%` }} />
                      </div>
                      <span className="muted">{s.pages}/{totalPages(s) || "?"} 页</span>
                    </div>
                  </td>
                  <td>{s.inserted}</td>
                  <td>{s.updated}</td>
                  <td>{s.skipped}</td>
                  <td className="muted">{s.elapsed_ms ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : "-"}</td>
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
