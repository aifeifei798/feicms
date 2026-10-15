import React from "react";
import { api, type Config } from "./api";

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
            <div className="mt-3 flex gap-2">
              <button className="btn btn-sm btn-outline" onClick={prune}>清理无播放源记录</button>
            </div>
            <p className="muted mt-2">改完 .env 后重启服务生效：uv run feicms serve</p>
          </div>
        </div>
      </div>
    </section>
  );
}
