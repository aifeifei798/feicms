import React from "react";
import { api, setToken, type AuthStatus, type CollectProgress, type Stats } from "./api";
import Sources from "./Sources";
import Videos from "./Videos";
import Images from "./Images";
import Jobs from "./Jobs";
import Settings from "./Settings";
import ApiDocs from "./ApiDocs";
import AiAccess from "./AiAccess";

type View = "dashboard" | "sources" | "videos" | "images" | "jobs" | "api" | "ai" | "settings";

const THEMES: { key: string; label: string }[] = [
  { key: "light", label: "浅色" },
  { key: "dark", label: "深色" },
  { key: "synthwave", label: "Synthwave" },
];

// 品牌图标跟随主题（形状不变，只换渐变）
const BRAND_MARK: Record<string, string> = {
  light: "/favicon.svg",
  dark: "/favicon-dark.svg",
  synthwave: "/favicon-synthwave.svg",
};

function initialTheme(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("theme");
  if (fromUrl && THEMES.some((t) => t.key === fromUrl)) return fromUrl;
  const saved = localStorage.getItem("feicms_theme");
  if (saved && THEMES.some((t) => t.key === saved)) return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const NAV: { key: View; label: string; group: string }[] = [
  { key: "dashboard", label: "总览", group: "数据" },
  { key: "sources", label: "资源站", group: "数据" },
  { key: "videos", label: "影片库", group: "数据" },
  { key: "images", label: "图片库", group: "数据" },
  { key: "jobs", label: "采集进度", group: "数据" },
  { key: "api", label: "API 说明", group: "接入" },
  { key: "ai", label: "AI / MCP", group: "接入" },
  { key: "settings", label: "配置", group: "系统" },
];

// 24x24 描边图标，避免 unicode 字符在不同字体下渲染不一致
const ICONS: Record<View, string> = {
  dashboard: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  sources: "M12 3l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5",
  videos: "M8 5v14l11-7z",
  images: "M4 5h16v14H4zM8 15l3-4 3 3 3-3",
  jobs: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l4 2",
  api: "M8 6l-4 6 4 6M16 6l4 6-4 6",
  ai: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
  settings: "M4 7h16M4 12h16M4 17h16M9 5v4M15 10v4M11 15v4",
};

function Icon({ name }: { name: View }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

export default function App() {
  const initialView: View = (() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return NAV.some((item) => item.key === requested) ? (requested as View) : "dashboard";
  })();
  const [view, setView] = React.useState<View>(initialView);
  const [toasts, setToasts] = React.useState<{ id: number; text: string; kind: "success" | "error" }[]>([]);
  const [theme, setTheme] = React.useState(initialTheme);
  const [auth, setAuth] = React.useState<AuthStatus | null>(null);
  const [stats, setStats] = React.useState<Stats | null>(null);
  const [collect, setCollect] = React.useState<CollectProgress | null>(null);

  const notify = React.useCallback((text: string, kind: "success" | "error" = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const refreshAuth = React.useCallback(() => {
    api.authStatus().then(setAuth).catch((e) => notify((e as Error).message, "error"));
  }, [notify]);

  const refreshStats = React.useCallback(() => {
    api.stats().then(setStats).catch(() => setStats(null));
  }, []);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("feicms_theme", theme);
    const icon = document.querySelector('link[rel="icon"]');
    if (icon) icon.setAttribute("href", BRAND_MARK[theme] ?? BRAND_MARK.light);
  }, [theme]);

  React.useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  React.useEffect(() => {
    if (!auth || (auth.auth_required && !auth.authenticated)) return;
    refreshStats();
    api.collectProgress().then(setCollect).catch(() => setCollect(null));
  }, [auth, refreshStats]);

  // Poll the background sweep while it is running.
  React.useEffect(() => {
    if (!collect?.running) return;
    const timer = setInterval(() => {
      api
        .collectProgress()
        .then((state) => {
          setCollect(state);
          if (!state.running) {
            notify(
              `采集完成：新增 ${state.inserted} · 更新 ${state.updated} · ${state.done_sources} 个站点${
                state.failed_sources ? ` · ${state.failed_sources} 个失败` : ""
              }`
            );
            refreshStats();
          }
        })
        .catch(() => setCollect(null));
    }, 1500);
    return () => clearInterval(timer);
  }, [collect?.running, notify, refreshStats]);

  if (auth && auth.auth_required && !auth.authenticated) {
    return <Login notify={notify} onDone={refreshAuth} theme={theme} setTheme={setTheme} />;
  }

  const logout = async () => {
    try {
      setToken(null);
      await api.logout();
      refreshAuth();
      notify("已退出登录");
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const checked = stats ? stats.by_source.filter((s) => s.health !== "unknown").length : 0;
  const healthRatio = checked > 0 ? stats!.healthy_sources / checked : null;
  const statusTone = healthRatio === null ? "unknown" : healthRatio >= 0.8 ? "ok" : healthRatio >= 0.5 ? "warn" : "bad";
  const statusText = stats
    ? stats.enabled_sources === 0
      ? "未配置资源站"
      : checked === 0
        ? `${stats.enabled_sources} 站点 · 尚未健康检查`
        : `${stats.healthy_sources}/${checked} 站点健康 · ${stats.videos} 条目`
    : "连接中…";

  const collectPct = collect?.total_sources
    ? Math.round((collect.done_sources / collect.total_sources) * 100)
    : 0;

  return (
    <div className="app-shell">
      <header className="app-nav">
        <div className="brand">
          <img
            src={BRAND_MARK[theme] ?? BRAND_MARK.light}
            alt="feicms logo"
            className="brand-mark"
            width="34"
            height="34"
          />
          <div className="brand-text">
            <span className="brand-name">feicms</span>
            <span className="brand-sub">资源站聚合 · 采集 · RAG 库</span>
          </div>
        </div>

        <div className="nav-divider" aria-hidden="true" />

        <div className="nav-status" title="站点健康状态">
          <span className={`status-dot ${statusTone}`} />
          <span className="status-text">{statusText}</span>
        </div>

        {collect?.running && (
          <div className="nav-progress" title="采集进度">
            <span className="progress-text">采集中 {collect.done_sources}/{collect.total_sources}</span>
            <span className="progress-bar" aria-hidden="true">
              <i style={{ width: `${collectPct}%` }} />
            </span>
            <span className="progress-count">新增 {collect.inserted} · 更新 {collect.updated}</span>
          </div>
        )}

        <div className="nav-actions">
          <ThemePicker theme={theme} setTheme={setTheme} />
          {auth?.auth_required && (
            <div className="user-chip">
              <span className="avatar">{(auth.user || "?").slice(0, 1).toUpperCase()}</span>
              <span className="user-name">{auth.user || "未登录"}</span>
              <button className="btn btn-sm btn-ghost" onClick={logout}>退出</button>
            </div>
          )}
        </div>
      </header>

      <div className="grid grid-cols-[220px_1fr] gap-1.5 p-4">
        <aside className="side-nav">
          {["数据", "接入", "系统"].map((group) => (
            <div key={group} className="nav-group">
              <span className="nav-group-label">{group}</span>
              {NAV.filter((item) => item.group === group).map((item) => (
                <button
                  key={item.key}
                  className={`nav-item ${view === item.key ? "active" : ""}`}
                  onClick={() => setView(item.key)}
                >
                  <Icon name={item.key} />
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </aside>

        <main>
          {view === "dashboard" && <Dashboard stats={stats} />}
          {view === "sources" && <Sources notify={notify} />}
          {view === "videos" && <Videos notify={notify} />}
          {view === "images" && <Images notify={notify} />}
          {view === "jobs" && <Jobs notify={notify} />}
          {view === "api" && <ApiDocs notify={notify} />}
          {view === "ai" && <AiAccess notify={notify} />}
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

function ThemePicker({ theme, setTheme }: { theme: string; setTheme: (t: string) => void }) {
  return (
    <div className="theme-picker" role="group" aria-label="主题">
      {THEMES.map((t) => (
        <button
          key={t.key}
          className={theme === t.key ? "active" : ""}
          onClick={() => setTheme(t.key)}
          title={t.key}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Login({
  notify,
  onDone,
  theme,
  setTheme,
}: {
  notify: (text: string, kind?: "success" | "error") => void;
  onDone: () => void;
  theme: string;
  setTheme: (t: string) => void;
}) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const result = await api.login(username, password);
      if (result.token) setToken(result.token);
      notify(`登录成功：${result.user}`);
      onDone();
    } catch (err) {
      notify((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="card bg-base-100 border border-base-300 w-96 shadow">
        <div className="card-body">
          <div className="flex items-center gap-2">
            <img
              src={BRAND_MARK[theme] ?? BRAND_MARK.light}
              alt="feicms logo"
              className="brand-mark"
              width="34"
              height="34"
            />
            <h2 className="card-title">feicms 管理面板</h2>
          </div>
          <p className="muted">需要 .env 里的 FEICMS_ADMIN_USER / FEICMS_ADMIN_PASSWORD</p>
          <form onSubmit={submit}>
            <label className="label">
              <span className="label-text">用户名</span>
            </label>
            <input
              type="text"
              className="input input-bordered w-full"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              autoComplete="username"
            />
            <label className="label mt-2">
              <span className="label-text">密码</span>
            </label>
            <input
              type="password"
              className="input input-bordered w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <button type="submit" className="btn btn-primary mt-3 w-full" disabled={busy}>
              {busy ? "登录中…" : "登录"}
            </button>
          </form>
          <div className="mt-3 flex items-center justify-between">
            <span className="muted">主题</span>
            <ThemePicker theme={theme} setTheme={setTheme} />
          </div>
          <p className="muted mt-3">
            忘了密码？运行 <code>uv run feicms passwd --user admin --password 你的密码</code> 后重启服务。
          </p>
        </div>
      </div>
    </div>
  );
}

function Dashboard({ stats }: { stats: Stats | null }) {
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
