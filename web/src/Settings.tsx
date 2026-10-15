import React from "react";
import { api, downloadFile, type Config, type MergeResult, type SchedulerState } from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

const ENV_TEMPLATE = `# feicms 配置（复制为 .env 后修改）
FEICMS_DB=sqlite+aiosqlite:///data/feicms.db
FEICMS_SOURCES=sources.yaml
FEICMS_PAGES=5
FEICMS_PAGE_LIMIT=40
FEICMS_FULL_PAGE_CAP=0
FEICMS_CONCURRENCY=0
FEICMS_TIMEOUT=20
FEICMS_RETRIES=3
FEICMS_AUTO_COLLECT=true
FEICMS_COLLECT_INTERVAL=60
FEICMS_HEALTH_INTERVAL=30
FEICMS_HEALTH_THRESHOLD=3
FEICMS_IMAGE_DIR=data/images
FEICMS_IMAGE_BATCH=300
FEICMS_IMAGE_AUTO=false
FEICMS_ADMIN_USER=admin
FEICMS_ADMIN_PASSWORD=改成强密码
FEICMS_SECRET=可选：token 签名密钥
FEICMS_TOKEN_TTL=86400
FEICMS_PUBLIC_PROVIDE=true
FEICMS_HOST=0.0.0.0
FEICMS_PORT=8000`;

export default function Settings({ notify }: { notify: Notify }) {
  const [config, setConfig] = React.useState<Config | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [mergePreview, setMergePreview] = React.useState<MergeResult | null>(null);
  const [scheduler, setScheduler] = React.useState<SchedulerState | null>(null);
  const [intervals, setIntervals] = React.useState({ collect: "", health: "", image: "" });
  const [adminUser, setAdminUser] = React.useState("");
  const [adminPassword, setAdminPassword] = React.useState("");
  const [token, setToken] = React.useState("");

  React.useEffect(() => {
    api.config().then(setConfig).catch((e) => notify((e as Error).message, "error"));
    api
      .scheduler()
      .then((s) => {
        setScheduler(s);
        setIntervals({
          collect: String(s.collect_interval_minutes),
          health: String(s.health_interval_minutes),
          image: String(s.image_interval_minutes),
        });
      })
      .catch((e) => notify((e as Error).message, "error"));
  }, [notify]);

  const saveScheduler = async () => {
    setBusy(true);
    try {
      const body: Record<string, number> = {};
      if (intervals.collect) body.collect_interval_minutes = Number(intervals.collect);
      if (intervals.health) body.health_interval_minutes = Number(intervals.health);
      if (intervals.image) body.image_interval_minutes = Number(intervals.image);
      const s = await api.setScheduler(body);
      setScheduler(s);
      notify("自动任务间隔已更新（立即生效）");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const toggleScheduler = async (key: "auto_collect" | "auto_collect_full" | "auto_images") => {
    try {
      const s = await api.setScheduler({ [key]: !scheduler?.[key] });
      setScheduler(s);
      const label =
        key === "auto_collect" ? "自动采集" : key === "auto_collect_full" ? "自动全量采集" : "自动海报下载";
      notify(`${label} ${s[key] ? "已开启" : "已关闭"}`);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const exportLibrary = async (fmt: "jsonl" | "json") => {
    setBusy(true);
    try {
      await downloadFile(`/api/export?fmt=${fmt}&limit=100000`, `feicms-export.${fmt}`);
      notify("影片库备份已下载");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const changePassword = async () => {
    setBusy(true);
    try {
      const r = await api.setAdminPassword({
        username: adminUser || undefined,
        password: adminPassword || undefined,
      });
      setAdminPassword(r.password);
      setAdminUser(r.user);
      notify(
        `管理员账号已写入 .env：${r.user} / ${r.password}${r.restart_required ? "（重启服务后生效）" : ""}`
      );
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const makeToken = async () => {
    try {
      const r = await api.adminToken(3600);
      setToken(r.token);
      notify("已生成 1 小时有效的 token");
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const prune = async () => {
    try {
      const r = await api.prune();
      notify(`清理 ${r.removed} 条无播放源记录`);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const repairPlays = async () => {
    setBusy(true);
    try {
      const r = await api.repairPlays();
      notify(`重建 ${r.changed} 条播放源（共 ${r.total} 条原始记录）`);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const previewMerge = async () => {
    try {
      const r = await api.mergeDuplicates(true);
      setMergePreview(r);
      notify(
        r.groups
          ? `发现 ${r.groups} 组重复标题，涉及 ${r.removed} 条（另有 ${r.fixed_signatures} 条旧 signature 需重键）`
          : "没有重复标题"
      );
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const mergeDuplicates = async () => {
    setBusy(true);
    try {
      const r = await api.mergeDuplicates(false);
      setMergePreview(null);
      notify(`合并 ${r.groups} 组 · 删除 ${r.removed} 条重复影片 · 迁移 ${r.moved_items} 条原始记录`);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="grid grid-cols-2 gap-1.5">
        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">当前生效配置</h2>
            <p className="muted">读取自项目根目录的 <span className="mono">.env</span> / 环境变量</p>
            <table className="table mt-2">
              <thead><tr><th>变量</th><th>值</th></tr></thead>
              <tbody>
                {config && (
                  <>
                    <tr><td>FEICMS_DB</td><td className="mono">{config.database_url}</td></tr>
                    <tr><td>FEICMS_SOURCES</td><td className="mono">{config.sources_file}</td></tr>
                    <tr>
                      <td>FEICMS_SUBSCRIPTIONS</td>
                      <td className="mono">{config.subscriptions.join(" · ")}</td>
                    </tr>
                    <tr><td>FEICMS_SUBSCRIBE_INTERVAL</td><td>{config.subscribe_interval_minutes} 分钟</td></tr>
                    <tr><td>FEICMS_PAGES</td><td>{config.default_pages}</td></tr>
                    <tr><td>FEICMS_PAGE_LIMIT</td><td>{config.page_limit}</td></tr>
                    <tr><td>FEICMS_FULL_PAGE_CAP</td><td>{config.full_page_cap > 0 ? config.full_page_cap : "无上限（按站点真实页数）"}</td></tr>
                    <tr><td>FEICMS_CONCURRENCY</td><td>{config.concurrency > 0 ? config.concurrency : "全部站点同时"}</td></tr>
                    <tr><td>FEICMS_TIMEOUT</td><td>{config.request_timeout}</td></tr>
                    <tr><td>FEICMS_IMPERSONATE</td><td>{config.impersonate}</td></tr>
                    <tr><td>FEICMS_DELAY</td><td>{config.request_delay}</td></tr>
                    <tr><td>FEICMS_RETRIES</td><td>{config.max_retries}</td></tr>
                    <tr><td>FEICMS_AUTO_COLLECT</td><td>{config.auto_collect ? "true" : "false"}</td></tr>
                    <tr><td>FEICMS_AUTO_COLLECT_FULL</td><td>{config.auto_collect_full ? "true（每轮走全部页数）" : "false"}</td></tr>
                    <tr><td>FEICMS_COLLECT_INTERVAL</td><td>{config.collect_interval_minutes} 分钟</td></tr>
                    <tr><td>FEICMS_HEALTH_INTERVAL</td><td>{config.health_interval_minutes} 分钟</td></tr>
                    <tr><td>FEICMS_HEALTH_THRESHOLD</td><td>{config.health_fail_threshold}</td></tr>
                    <tr><td>FEICMS_IMAGE_DIR</td><td className="mono">{config.image_dir}</td></tr>
                    <tr><td>FEICMS_IMAGE_BATCH</td><td>{config.image_batch}</td></tr>
                    <tr><td>FEICMS_IMAGE_AUTO</td><td>{config.image_auto ? "true" : "false"}</td></tr>
                    <tr><td>FEICMS_ADMIN_USER</td><td className="mono">{config.admin_user || "（未设置）"}</td></tr>
                    <tr>
                      <td>登录认证</td>
                      <td>
                        <span className={`badge ${config.auth_required ? "badge-success" : "badge-warning"}`}>
                          {config.auth_required ? "已启用" : "未启用"}
                        </span>
                      </td>
                    </tr>
                    <tr><td>FEICMS_HOST / PORT</td><td>{config.host}:{config.port}</td></tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">.env 模板</h2>
            <pre className="mono bg-base-200 rounded-box p-3 whitespace-pre-wrap">{ENV_TEMPLATE}</pre>
            <p className="muted mt-2">改完 .env 后重启服务生效：uv run feicms serve</p>
            <p className="muted mt-1">
              对外 API key 与 MCP 接入在左侧「AI / MCP 接入」页面管理。
            </p>
          </div>
        </div>
      </div>

      <div className="card bg-base-100 border border-base-300 mt-3">
        <div className="card-body">
          <h2 className="card-title">数据维护</h2>
          <p className="muted">
            跨站点合并按标题归一化（去掉标点与空格）。旧数据里 signature 带年份，同一影片可能被拆成多条；
            下面的工具会把它们合并成一条，播放源全部保留。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn btn-sm btn-outline" onClick={previewMerge} disabled={busy}>
              检查重复影片
            </button>
            <button className="btn btn-sm btn-primary" onClick={mergeDuplicates} disabled={busy}>
              {busy ? "处理中…" : "合并重复影片"}
            </button>
            <button className="btn btn-sm btn-outline" onClick={repairPlays} disabled={busy}>
              重建播放源
            </button>
            <button className="btn btn-sm btn-outline" onClick={prune} disabled={busy}>
              清理无播放源记录
            </button>
          </div>
          {mergePreview && (
            <div className="mt-3">
              <p className="muted">
                共 {mergePreview.groups} 组重复标题（合并后删除 {mergePreview.removed} 条）。示例：
              </p>
              <table className="table mt-1">
                <thead>
                  <tr>
                    <th>影片</th>
                    <th>保留</th>
                    <th>合并进</th>
                  </tr>
                </thead>
                <tbody>
                  {mergePreview.examples.map((ex) => (
                    <tr key={ex.keep}>
                      <td>{ex.name}</td>
                      <td className="mono">#{ex.keep}</td>
                      <td className="mono">{ex.merge.map((id) => `#${id}`).join(" ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 mt-3">
        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">自动任务</h2>
            <p className="muted">
              后台循环（自动采集、健康检查、海报下载）可以在这里开关，改间隔不需要重启服务。
              「自动全量采集」开启后每轮都会走每个站点的全部页数（等同于 `./server.sh full`），
              与后台扫描进程共用采集锁，不会同时跑两轮。
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                className={`btn btn-sm ${scheduler?.auto_collect ? "btn-primary" : "btn-outline"}`}
                onClick={() => toggleScheduler("auto_collect")}
                disabled={busy}
              >
                自动采集 {scheduler?.auto_collect ? "已开启" : "已关闭"}
              </button>
              <button
                className={`btn btn-sm ${scheduler?.auto_collect_full ? "btn-primary" : "btn-outline"}`}
                onClick={() => toggleScheduler("auto_collect_full")}
                disabled={busy}
              >
                自动全量采集 {scheduler?.auto_collect_full ? "已开启" : "已关闭"}
              </button>
              <button
                className={`btn btn-sm ${scheduler?.auto_images ? "btn-primary" : "btn-outline"}`}
                onClick={() => toggleScheduler("auto_images")}
                disabled={busy}
              >
                自动海报 {scheduler?.auto_images ? "已开启" : "已关闭"}
              </button>
              {scheduler?.collect_running && <span className="badge badge-primary">采集进行中</span>}
            </div>
            <div className="flex flex-wrap gap-2 mt-2">
              <label className="label mr-2">
                <span className="muted">采集间隔（分钟）</span>
                <input className="input input-bordered input-sm" value={intervals.collect} onChange={(e) => setIntervals({ ...intervals, collect: e.target.value })} />
              </label>
              <label className="label mr-2">
                <span className="muted">健康检查间隔</span>
                <input className="input input-bordered input-sm" value={intervals.health} onChange={(e) => setIntervals({ ...intervals, health: e.target.value })} />
              </label>
              <label className="label mr-2">
                <span className="muted">海报间隔</span>
                <input className="input input-bordered input-sm" value={intervals.image} onChange={(e) => setIntervals({ ...intervals, image: e.target.value })} />
              </label>
              <button className="btn btn-sm btn-outline" onClick={saveScheduler} disabled={busy}>
                保存间隔
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <span className="muted">上次采集：{scheduler?.last_collect_at ? new Date(scheduler.last_collect_at).toLocaleString() : "—"}</span>
              <span className="muted">上次健康检查：{scheduler?.last_health_at ? new Date(scheduler.last_health_at).toLocaleString() : "—"}</span>
              <span className="muted">上次海报：{scheduler?.last_image_at ? new Date(scheduler.last_image_at).toLocaleString() : "—"}</span>
            </div>
            {scheduler?.last_collect_result && (
              <p className="muted mt-1">
                最近一轮：{scheduler.last_collect_result.sources} 个站点 · 新增 {scheduler.last_collect_result.inserted} ·
                更新 {scheduler.last_collect_result.updated} · 失败 {scheduler.last_collect_result.failed}
              </p>
            )}
          </div>
        </div>

        <div className="card bg-base-100 border border-base-300">
          <div className="card-body">
            <h2 className="card-title">备份与导出</h2>
            <p className="muted">
              影片库导出为 JSONL / JSON（含每个站点的播放源），站点列表导出为 YAML / JSON / CSV，
              可以直接喂给 <span className="mono">feicms seed</span> 或另一台实例。
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button className="btn btn-sm btn-outline" onClick={() => exportLibrary("jsonl")} disabled={busy}>
                导出影片库 JSONL
              </button>
              <button className="btn btn-sm btn-outline" onClick={() => exportLibrary("json")} disabled={busy}>
                导出影片库 JSON
              </button>
              <button
                className="btn btn-sm btn-outline"
                onClick={() => downloadFile("/api/sources/export?fmt=yaml", "sources.yaml").then(() => notify("站点列表已导出")).catch((e) => notify((e as Error).message, "error"))}
              >
                导出站点列表
              </button>
            </div>
            <p className="muted mt-2">
              导出的是合并后的影片库，不含上游站点的原始抓取缓存。
            </p>
          </div>
        </div>
      </div>

      <div className="card bg-base-100 border border-base-300 mt-3">
        <div className="card-body">
          <h2 className="card-title">管理员账号</h2>
          <p className="muted">
            修改管理员账号会写入项目根目录的 <span className="mono">.env</span>；
            留空密码则随机生成。改完后重启服务生效（<span className="mono">./server.sh restart</span>）。
          </p>
          <div className="flex flex-wrap gap-2 mt-2">
            <input className="input input-bordered input-sm" value={adminUser} onChange={(e) => setAdminUser(e.target.value)} placeholder="管理员用户名" />
            <input className="input input-bordered input-sm" value={adminPassword} onChange={(e) => setAdminPassword(e.target.value)} placeholder="新密码（留空 = 随机）" />
            <button className="btn btn-sm btn-primary" onClick={changePassword} disabled={busy}>
              {busy ? "处理中…" : "保存账号"}
            </button>
            <button
              className="btn btn-sm btn-outline"
              onClick={() => setAdminPassword(randomPassword())}
            >
              随机生成密码
            </button>
            <button className="btn btn-sm btn-outline" onClick={makeToken}>生成 curl 用的 token</button>
          </div>
          {token && (
            <div className="code-block mt-2">
              <span className="mono">{token}</span>
            </div>
          )}
        </div>
      </div>

    </section>
  );
}

function randomPassword(): string {
  const bytes = new Uint8Array(12);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
