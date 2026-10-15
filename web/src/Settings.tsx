import React from "react";
import { api, type ApiKey, type Config } from "./api";

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

      <ApiKeyPanel notify={notify} />
    </section>
  );
}

function ApiKeyPanel({ notify }: { notify: Notify }) {
  const [keys, setKeys] = React.useState<ApiKey[]>([]);
  const [name, setName] = React.useState("ai-client");
  const [scopes, setScopes] = React.useState("read");
  const [expiresDays, setExpiresDays] = React.useState("");
  const [secret, setSecret] = React.useState("");

  const load = () => {
    api.apiKeys().then(setKeys).catch((e) => notify((e as Error).message, "error"));
  };
  React.useEffect(load, []);

  const create = async () => {
    try {
      const created = await api.createApiKey({
        name: name || "key",
        scopes,
        expires_days: expiresDays ? Number(expiresDays) : null,
      });
      setSecret(created.key);
      notify(`已创建 key：${created.key}`);
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const toggle = async (key: ApiKey) => {
    try {
      await api.updateApiKey(key.id, { enabled: !key.enabled });
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const remove = async (id: number) => {
    try {
      await api.deleteApiKey(id);
      notify(`已删除 key ${id}`);
      load();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <div className="card bg-base-100 border border-base-300 mt-1.5">
      <div className="card-body">
        <h2 className="card-title">API Key 与 MCP 接入</h2>
        <p className="muted">
          给脚本和 AI 客户端（Claude / OpenCode 等）发放长期访问密钥。数据库里只存 sha256 哈希，明文只在创建时显示一次。
        </p>

        <div className="mt-2 flex flex-wrap gap-2">
          <input className="input input-sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="key 名称" />
          <select className="select select-sm" value={scopes} onChange={(e) => setScopes(e.target.value)}>
            <option value="read">read（只读）</option>
            <option value="write">write（可采集/改源）</option>
            <option value="read,write">read,write</option>
          </select>
          <input
            className="input input-sm"
            value={expiresDays}
            onChange={(e) => setExpiresDays(e.target.value)}
            placeholder="有效期（天，留空=永久）"
          />
          <button className="btn btn-sm btn-primary" onClick={create}>创建 key</button>
        </div>

        {secret && (
          <div className="alert alert-info mt-2">
            <span className="mono">{secret}</span>
            <button
              className="btn btn-sm btn-outline mt-1"
              onClick={() => navigator.clipboard?.writeText(secret)}
            >
              复制
            </button>
          </div>
        )}

        <table className="table mt-2">
          <thead>
            <tr><th>名称</th><th>key</th><th>权限</th><th>状态</th><th>使用次数</th><th>有效期</th><th></th></tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id}>
                <td>{key.name}</td>
                <td className="mono">{key.prefix}…</td>
                <td className="mono">{key.scopes}</td>
                <td>
                  <button className={`btn btn-sm ${key.enabled ? "btn-success" : "btn-ghost"}`} onClick={() => toggle(key)}>
                    {key.enabled ? "启用" : "停用"}
                  </button>
                </td>
                <td>{key.usage_count}</td>
                <td>{key.expires_at ? new Date(key.expires_at).toLocaleDateString() : "永久"}</td>
                <td>
                  <button className="btn btn-sm btn-outline" onClick={() => remove(key.id)}>删除</button>
                </td>
              </tr>
            ))}
            {!keys.length && <tr><td colSpan={7} className="muted">还没有 key。创建一个只读 key 给 AI 用。</td></tr>}
          </tbody>
        </table>

        <div className="mt-3">
          <p className="muted">MCP 端点：<span className="mono">POST /mcp</span>（HTTP），或本地 stdio：<span className="mono">uv run feicms mcp</span></p>
          <pre className="mono bg-base-200 rounded-box p-3 mt-2 whitespace-pre-wrap">
{JSON.stringify(
  {
    mcpServers: {
      feicms: {
        type: "http",
        url: `${window.location.origin}/mcp`,
        headers: { "X-API-Key": "fk_..." },
      },
    },
  },
  null,
  2
)}
          </pre>
        </div>
      </div>
    </div>
  );
}
