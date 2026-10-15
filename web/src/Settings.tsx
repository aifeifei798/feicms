import React from "react";
import { api, type Config, type MergeResult } from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

const ENV_TEMPLATE = `# feicms 配置（复制为 .env 后修改）
FEICMS_DB=sqlite+aiosqlite:///data/feicms.db
FEICMS_SOURCES=sources.yaml
FEICMS_PAGES=5
FEICMS_PAGE_LIMIT=40
FEICMS_FULL_PAGE_CAP=200
FEICMS_CONCURRENCY=6
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

  React.useEffect(() => {
    api.config().then(setConfig).catch((e) => notify((e as Error).message, "error"));
  }, [notify]);

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
                    <tr><td>FEICMS_FULL_PAGE_CAP</td><td>{config.full_page_cap}</td></tr>
                    <tr><td>FEICMS_CONCURRENCY</td><td>{config.concurrency}</td></tr>
                    <tr><td>FEICMS_TIMEOUT</td><td>{config.request_timeout}</td></tr>
                    <tr><td>FEICMS_IMPERSONATE</td><td>{config.impersonate}</td></tr>
                    <tr><td>FEICMS_DELAY</td><td>{config.request_delay}</td></tr>
                    <tr><td>FEICMS_RETRIES</td><td>{config.max_retries}</td></tr>
                    <tr><td>FEICMS_AUTO_COLLECT</td><td>{config.auto_collect ? "true" : "false"}</td></tr>
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

    </section>
  );
}
