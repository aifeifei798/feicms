import React from "react";
import { api, type Stats } from "./api";
import Sources from "./Sources";
import Videos from "./Videos";
import Images from "./Images";
import Jobs from "./Jobs";
import Settings from "./Settings";
import ApiDocs from "./ApiDocs";

type View = "dashboard" | "sources" | "videos" | "images" | "jobs" | "api" | "settings";

const NAV: { key: View; label: string; icon: string }[] = [
  { key: "dashboard", label: "总览", icon: "▦" },
  { key: "sources", label: "资源站", icon: "☑" },
  { key: "videos", label: "影片库", icon: "▶" },
  { key: "images", label: "图片库", icon: "▤" },
  { key: "jobs", label: "采集记录", icon: "≡" },
  { key: "api", label: "API 说明", icon: "⌘" },
  { key: "settings", label: "配置", icon: "⚙" },
];

export default function App() {
  const [view, setView] = React.useState<View>("dashboard");
  const [toasts, setToasts] = React.useState<{ id: number; text: string; kind: "success" | "error" }[]>([]);
  const [theme, setTheme] = React.useState("light");

  const notify = React.useCallback((text: string, kind: "success" | "error" = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div className="app-shell">
      <header className="navbar bg-base-100 border-b border-base-300 px-4">
        <div className="navbar-start">
          <span className="badge badge-lg badge-primary">feicms</span>
          <span className="muted ml-2">资源站采集 · 维护 · 聚合</span>
        </div>
        <div className="navbar-end">
          <button
            className="btn btn-sm btn-outline"
            onClick={() =>
              api
                .healthCheck()
                .then((r) => notify(`健康检查：${r.ok} 正常 / ${r.dead} 不可用`, r.dead ? "error" : "success"))
                .catch((e) => notify((e as Error).message, "error"))
            }
          >
            健康检查
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={() =>
              api
                .collectAll()
                .then((r) =>
                  notify(
                    `采集完成：新增 ${r.reduce((a, b) => a + b.inserted, 0)} · 更新 ${r.reduce((a, b) => a + b.updated, 0)} · 抓取 ${r.reduce((a, b) => a + b.pages, 0)} 页`
                  )
                )
                .catch((e) => notify((e as Error).message, "error"))
            }
          >
            采集全部
          </button>
          <div className="dropdown dropdown-end">
            <button className="btn btn-sm btn-ghost">主题</button>
            <ul className="dropdown-content menu bg-base-100 rounded-box z-[10] w-40 p-2 shadow">
              {["light", "dark", "synthwave"].map((t) => (
                <li key={t}>
                  <button onClick={() => setTheme(t)}>{t}</button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-[220px_1fr] gap-1.5 p-4">
        <aside className="menu bg-base-200 rounded-box w-full">
          {NAV.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${view === item.key ? "active" : ""}`}
              onClick={() => setView(item.key)}
            >
              <span>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </aside>

        <main>
          {view === "dashboard" && <Dashboard />}
          {view === "sources" && <Sources notify={notify} />}
          {view === "videos" && <Videos notify={notify} />}
          {view === "images" && <Images notify={notify} />}
          {view === "jobs" && <Jobs notify={notify} />}
          {view === "api" && <ApiDocs notify={notify} />}
          {view === "settings" && <Settings notify={notify} />}
        </main>
      </div>

      <div className="toast">
        {toasts.map((t) => (
          <div key={t.id} className={`alert ${t.kind === "error" ? "alert-error" : "alert-success"}`}>
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Dashboard() {
  const [stats, setStats] = React.useState<Stats | null>(null);

  React.useEffect(() => {
    api.stats().then(setStats).catch((e) => console.error(e));
  }, []);

  if (!stats) return <div className="muted">加载中…</div>;

  const maxType = Math.max(1, ...stats.by_type.map((t) => t.count));

  return (
    <section>
      <div className="stat-grid">
        <div className="stat-card"><b>{stats.sources}</b><span>资源站总数</span></div>
        <div className="stat-card"><b>{stats.enabled_sources}</b><span>启用站点</span></div>
        <div className="stat-card"><b>{stats.healthy_sources}</b><span>健康站点</span></div>
        <div className="stat-card"><b>{stats.videos}</b><span>影片条目</span></div>
        <div className="stat-card"><b>{stats.source_items}</b><span>采集记录</span></div>
        <div className="stat-card">
          <b>{stats.last_collect_at ? new Date(stats.last_collect_at).toLocaleString() : "-"}</b>
          <span>最近采集</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 mt-4">
        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">分类分布</h2>
            <div className="flex flex-col gap-2">
              {stats.by_type.slice(0, 12).map((t) => (
                <div key={t.type_name || "none"}>
                  <div className="flex justify-between">
                    <span>{t.type_name || "（未分类）"}</span>
                    <span className="muted">{t.count}</span>
                  </div>
                  <div className="bar" style={{ width: `${(t.count / maxType) * 100}%` }} />
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">站点贡献 Top 10</h2>
            <table className="table">
              <thead>
                <tr><th>站点</th><th>健康</th><th>条目</th></tr>
              </thead>
              <tbody>
                {stats.by_source
                  .sort((a, b) => b.count - a.count)
                  .slice(0, 10)
                  .map((s) => (
                    <tr key={s.name}>
                      <td>{s.name}</td>
                      <td>
                        <span className={`badge ${s.health === "ok" ? "badge-success" : s.health === "slow" ? "badge-warning" : "badge-error"}`}>
                          {s.health}
                        </span>
                      </td>
                      <td>{s.count}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
