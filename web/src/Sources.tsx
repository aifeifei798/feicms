import React from "react";
import {
  api,
  downloadFile,
  type CollectProgress,
  type Source,
  type SourceImportResult,
  type Subscription,
  type SyncResult,
} from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

const HEALTH_BADGE: Record<string, string> = {
  ok: "badge badge-success",
  slow: "badge badge-warning",
  dead: "badge badge-error",
  unknown: "badge badge-ghost",
};

export default function Sources({ notify }: { notify: Notify }) {
  const [sources, setSources] = React.useState<Source[]>([]);
  const [q, setQ] = React.useState("");
  const [health, setHealth] = React.useState("");
  const [onlyEnabled, setOnlyEnabled] = React.useState(false);
  const [pages, setPages] = React.useState("");
  const [editing, setEditing] = React.useState<Source | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [progress, setProgress] = React.useState<CollectProgress | null>(null);

  React.useEffect(() => {
    api.collectProgress().then(setProgress).catch(() => undefined);
  }, []);

  const load = React.useCallback(async () => {
    const params: Record<string, string | boolean> = {};
    if (q) params.q = q;
    if (health) params.health = health;
    if (onlyEnabled) params.enabled = true;
    setSources(await api.sources(params));
  }, [q, health, onlyEnabled]);

  React.useEffect(() => {
    load().catch((e) => notify((e as Error).message, "error"));
  }, [load, notify]);

  const toggle = async (s: Source) => {
    await api.toggleSource(s.id, !s.enabled);
    notify(`${s.name} ${s.enabled ? "已禁用" : "已启用"}`);
    load();
  };

  const collect = async (s: Source, full = false) => {
    setBusy(true);
    try {
      const r = await api.collectOne(s.id, full ? 0 : s.max_pages);
      notify(
        `${s.name}: 采集 ${r.pages}/${r.pagecount || "?"} 页 · 新增 ${r.inserted} · 更新 ${r.updated} · 跳过 ${r.skipped}${r.error ? ` · ${r.error}` : ""}`,
        r.error ? "error" : "success"
      );
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
      load();
    }
  };

  const collectAllSources = async () => {
    setBusy(true);
    try {
      // The sweep runs in the background: a single blocking request for 50+
      // sites would run for minutes and the browser would give up ("Failed to fetch").
      let state = await api.collectStart({ pages: pages === "" ? undefined : Number(pages) });
      setProgress(state);
      if (state.error) {
        notify(`采集失败：${state.error}`, "error");
        return;
      }
      notify(`采集已启动：${state.total_sources} 个站点，后台进行中`);
      while (state.running) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        state = await api.collectProgress();
        setProgress(state);
      }
      notify(
        `采集完成：${state.done_sources}/${state.total_sources} 个站点 · 新增 ${state.inserted} · 更新 ${state.updated}${state.failed_sources ? ` · ${state.failed_sources} 个失败` : ""}`,
        state.failed_sources ? "error" : "success"
      );
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
      load();
    }
  };

  const remove = async (s: Source) => {
    if (!confirm(`删除 ${s.name}？该站点的所有采集记录也会被清理。`)) return;
    await api.deleteSource(s.id);
    notify(`${s.name} 已删除`);
    load();
  };

  return (
    <section>
      <SubscriptionPanel notify={notify} onSynced={load} />

      <div className="toolbar">
        <input
          className="input input-bordered"
          placeholder="搜索站点名称"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="select input-bordered" value={health} onChange={(e) => setHealth(e.target.value)}>
          <option value="">全部健康状态</option>
          <option value="ok">ok</option>
          <option value="slow">slow</option>
          <option value="dead">dead</option>
          <option value="unknown">unknown</option>
        </select>
        <label className="label mr-2">
          <input type="checkbox" checked={onlyEnabled} onChange={(e) => setOnlyEnabled(e.target.checked)} />
          <span className="ml-1">仅启用</span>
        </label>
        <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>新增站点</button>
        <select className="select input-bordered" value={pages} onChange={(e) => setPages(e.target.value)} title="采集页数">
          <option value="">默认页数（每站 max_pages）</option>
          <option value="10">10 页</option>
          <option value="20">20 页</option>
          <option value="50">50 页</option>
          <option value="0">全量（遍历所有页）</option>
        </select>
        <button className="btn btn-sm btn-outline" onClick={collectAllSources} disabled={busy || progress?.running}>
          {busy || progress?.running ? "采集中…" : "采集全部"}
        </button>
        <span className="muted">{sources.length} 个站点</span>
        {progress && (
          <span className="muted">
            {progress.running ? "进度" : "上次采集"} {progress.done_sources}/{progress.total_sources} · 新增
            {progress.inserted} · 更新 {progress.updated}
            {progress.failed_sources ? ` · ${progress.failed_sources} 失败` : ""}
          </span>
        )}
      </div>

      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <table className="table">
            <thead>
              <tr>
                <th>站点</th>
                <th>健康</th>
                <th>延迟</th>
                <th>条目</th>
                <th>页数上限</th>
                <th>最近采集</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id} className={s.enabled ? "" : "opacity-50"}>
                  <td>
                    <div className="font-semibold">{s.name}</div>
                    <div className="mono muted">{s.url}</div>
                    {s.last_error && <div className="muted text-error">{s.last_error}</div>}
                  </td>
                  <td>
                    <span className={HEALTH_BADGE[s.health_status] ?? "badge"}>{s.health_status}</span>
                    {s.fail_streak > 0 && <span className="muted ml-1">连续失败 {s.fail_streak}</span>}
                  </td>
                  <td>{s.latency_ms ?? "-"}</td>
                  <td>{s.item_count}</td>
                  <td>{s.max_pages}</td>
                  <td className="muted">
                    {s.last_collect_at ? new Date(s.last_collect_at).toLocaleString() : "-"}
                  </td>
                  <td>
                    <div className="flex gap-1">
                      <button className="btn btn-xs" onClick={() => collect(s)}>采集</button>
                      <button className="btn btn-xs btn-outline" onClick={() => collect(s, true)} title="遍历该站点所有页">
                        全量
                      </button>
                      <button className="btn btn-xs btn-outline" onClick={() => setEditing(s)}>编辑</button>
                      <button className="btn btn-xs btn-outline" onClick={() => toggle(s)}>
                        {s.enabled ? "禁用" : "启用"}
                      </button>
                      <button className="btn btn-xs btn-ghost text-error" onClick={() => remove(s)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <SourceImportPanel notify={notify} onImported={load} />

      {(editing || creating) && (
        <SourceForm
          source={editing}
          onClose={() => {
            setEditing(null);
            setCreating(false);
            load();
          }}
          notify={notify}
        />
      )}
    </section>
  );
}

function SourceImportPanel({ notify, onImported }: { notify: Notify; onImported: () => Promise<void> }) {
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState("");
  const [maxPages, setMaxPages] = React.useState("");
  const [enabled, setEnabled] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<SourceImportResult | null>(null);

  const run = async () => {
    if (!text.trim()) {
      notify("先粘贴站点列表", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await api.importSources(
        text,
        maxPages === "" ? undefined : Number(maxPages),
        enabled
      );
      setResult(r);
      notify(
        `解析 ${r.parsed} 条：新增 ${r.created}，更新 ${r.updated}${r.skipped.length ? `，跳过 ${r.skipped.length}` : ""}`
      );
      await onImported();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const exportList = async (fmt: string) => {
    try {
      await downloadFile(`/api/sources/export?fmt=${fmt}`, `sources.${fmt}`);
      notify(`已导出站点列表（${fmt}）`);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <div className="card bg-base-100 border border-base-300 mt-3">
      <div className="card-body">
        <h2 className="card-title">
          站点列表
          <button className="btn btn-sm btn-ghost ml-2" onClick={() => setOpen((v) => !v)}>
            {open ? "收起导入" : "批量导入"}
          </button>
        </h2>
        <p className="muted">
          仓库里不包含站点列表，站点由你自己维护。这里可以批量导入，也可以把当前列表导出备份。
        </p>
        <div className="flex flex-wrap gap-2 mt-2">
          <button className="btn btn-sm btn-outline" onClick={() => exportList("yaml")}>导出 YAML</button>
          <button className="btn btn-sm btn-outline" onClick={() => exportList("json")}>导出 JSON</button>
          <button className="btn btn-sm btn-outline" onClick={() => exportList("csv")}>导出 CSV</button>
        </div>

        {open && (
          <div className="mt-3">
            <textarea
              className="textarea input-bordered mono"
              rows={8}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={"站点名|https://example.com|auto|100\nhttps://another.example.com"}
            ></textarea>
            <p className="muted mt-1">
              每行一条：<code>名称|url|格式|可用率</code>，格式可写 <code>xml</code> / <code>json</code> / <code>auto</code>；
              只写 URL 也可以。已存在的 URL 会被更新而不是重复添加。
            </p>
            <div className="flex flex-wrap gap-2 mt-2">
              <label className="label mr-2">
                <span className="muted">统一页数上限</span>
                <input className="input input-bordered input-sm" value={maxPages} onChange={(e) => setMaxPages(e.target.value)} placeholder="留空 = 每站默认" />
              </label>
              <label className="label mr-2">
                <span className="muted">导入后启用</span>
                <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              </label>
              <button className="btn btn-sm btn-primary" onClick={run} disabled={busy}>
                {busy ? "导入中…" : "导入"}
              </button>
            </div>
            {result && (
              <div className="mt-2">
                <span className="badge badge-success">新增 {result.created}</span>
                <span className="badge badge-info">更新 {result.updated}</span>
                {result.skipped.length ? <span className="badge badge-warning">跳过 {result.skipped.length}</span> : null}
                {result.skipped.length ? (
                  <ul className="muted mt-1">
                    {result.skipped.slice(0, 20).map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SubscriptionPanel({ notify, onSynced }: { notify: Notify; onSynced: () => Promise<void> }) {
  const [subs, setSubs] = React.useState<Subscription[]>([]);
  const [url, setUrl] = React.useState("");
  const [syncing, setSyncing] = React.useState(false);
  const [results, setResults] = React.useState<SyncResult[]>([]);

  const load = React.useCallback(async () => {
    setSubs(await api.subscriptions());
  }, []);

  React.useEffect(() => {
    load().catch((e) => notify((e as Error).message, "error"));
  }, [load, notify]);

  const add = async () => {
    const value = url.trim();
    if (!value) return;
    try {
      await api.createSubscription({ url: value });
      setUrl("");
      notify(`订阅已添加：${value}`);
      await load();
      await sync();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const sync = async (id?: number) => {
    setSyncing(true);
    try {
      const out = await api.syncSubscriptions(id);
      setResults(out);
      const added = out.reduce((sum, r) => sum + r.added, 0);
      const updated = out.reduce((sum, r) => sum + r.updated, 0);
      const failed = out.filter((r) => r.error).length;
      notify(
        `订阅同步完成：新增 ${added} 个站点 · 更新 ${updated} 个${failed ? ` · ${failed} 个失败` : ""}`,
        failed ? "error" : "success"
      );
      await onSynced();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setSyncing(false);
    }
  };

  const remove = async (sub: Subscription) => {
    if (!confirm(`删除订阅 ${sub.url}？已添加的站点不会被删除。`)) return;
    await api.deleteSubscription(sub.id);
    notify("订阅已删除");
    load();
  };

  return (
    <div className="card bg-base-100 border border-base-300 mb-4">
      <div className="card-body">
        <h2 className="card-title">源订阅</h2>
        <p className="muted">
          订阅资源站列表 feed（JSON / TXT），同步后自动新增或刷新站点，并列出 feed 里已经消失的站点。
        </p>

        <div className="flex gap-2 flex-wrap mt-2">
          <input
            className="input input-bordered flex-1 min-w-64"
            placeholder="https://…/interfaces.json"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button className="btn btn-primary btn-sm" onClick={add}>
            添加订阅
          </button>
          <button className="btn btn-outline btn-sm" onClick={() => sync()} disabled={syncing}>
            {syncing ? "同步中…" : "同步全部"}
          </button>
        </div>

        <table className="table mt-3">
          <thead>
            <tr>
              <th>feed</th>
              <th>类型</th>
              <th>条目</th>
              <th>新增</th>
              <th>更新</th>
              <th>最近同步</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {subs.map((sub) => (
              <tr key={sub.id} className={sub.enabled ? "" : "opacity-50"}>
                <td>
                  <div className="mono muted">{sub.url}</div>
                  {sub.last_error && <div className="muted text-error">{sub.last_error}</div>}
                </td>
                <td>{sub.kind}</td>
                <td>{sub.last_entries}</td>
                <td>{sub.last_added}</td>
                <td>{sub.last_updated}</td>
                <td className="muted">
                  {sub.last_sync_at ? new Date(sub.last_sync_at).toLocaleString() : "-"}
                </td>
                <td>
                  <div className="flex gap-1">
                    <button className="btn btn-xs" onClick={() => sync(sub.id)} disabled={syncing}>
                      同步
                    </button>
                    <button className="btn btn-xs btn-ghost text-error" onClick={() => remove(sub)}>
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {subs.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">还没有订阅，添加一个 feed 地址。</td>
              </tr>
            )}
          </tbody>
        </table>

        {results.length > 0 && (
          <div className="mt-2">
            {results.map((r) => (
              <div key={r.subscription_id} className="muted">
                {r.url} → {r.entries} 条，新增 {r.added}，更新 {r.updated}，无变化 {r.unchanged}
                {r.missing.length > 0 && `，feed 中已消失：${r.missing.join(", ")}`}
                {r.error && <span className="text-error">，错误：{r.error}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SourceForm({
  source,
  onClose,
  notify,
}: {
  source: Source | null;
  onClose: () => void;
  notify: Notify;
}) {
  const [form, setForm] = React.useState({
    name: source?.name ?? "",
    url: source?.url ?? "",
    api_format: source?.api_format ?? "auto",
    max_pages: source?.max_pages ?? 5,
    priority: source?.priority ?? 0,
    tags: source?.tags ?? "",
    enabled: source?.enabled ?? true,
  });

  const set = (key: string, value: string | number | boolean) => setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    try {
      if (source) {
        await api.updateSource(source.id, form);
        notify(`${form.name} 已更新`);
      } else {
        await api.createSource(form);
        notify(`${form.name} 已添加`);
      }
      onClose();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <div className="modal modal-open">
      <div className="card bg-base-100 w-96 max-w-full border border-base-300">
        <div className="card-body">
          <h2 className="card-title">{source ? "编辑站点" : "新增站点"}</h2>

          <div className="form-control">
            <label className="label"><span className="label-text">名称</span></label>
            <input className="input input-bordered" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>

          <div className="form-control">
            <label className="label"><span className="label-text">API URL</span></label>
            <input className="input input-bordered" value={form.url} onChange={(e) => set("url", e.target.value)} />
            <span className="muted mt-1">例：https://example.com/api.php</span>
          </div>

          <div className="grid grid-cols-3 gap-2 mt-2">
            <div className="form-control">
              <label className="label"><span className="label-text">格式</span></label>
              <select className="select input-bordered" value={form.api_format} onChange={(e) => set("api_format", e.target.value)}>
                <option value="auto">auto</option>
                <option value="json">json</option>
                <option value="xml">xml</option>
              </select>
            </div>
            <div className="form-control">
              <label className="label"><span className="label-text">页数</span></label>
              <input
                type="number"
                className="input input-bordered"
                value={form.max_pages}
                onChange={(e) => set("max_pages", Number(e.target.value))}
              />
            </div>
            <div className="form-control">
              <label className="label"><span className="label-text">优先级</span></label>
              <input
                type="number"
                className="input input-bordered"
                value={form.priority}
                onChange={(e) => set("priority", Number(e.target.value))}
              />
            </div>
          </div>

          <div className="form-control mt-2">
            <label className="label"><span className="label-text">标签</span></label>
            <input className="input input-bordered" value={form.tags} onChange={(e) => set("tags", e.target.value)} />
          </div>

          <div className="mt-3 flex gap-2">
            <button className="btn btn-primary" onClick={save}>保存</button>
            <button className="btn btn-ghost" onClick={onClose}>取消</button>
          </div>
        </div>
      </div>
    </div>
  );
}
