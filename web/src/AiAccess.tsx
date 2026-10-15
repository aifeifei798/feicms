import React from "react";
import { api, type ApiKey, type McpTool } from "./api";

type Notify = (msg: string, kind?: "success" | "error") => void;

function copy(text: string, notify: Notify) {
  if (!navigator.clipboard) {
    notify("浏览器不支持剪贴板，请手动复制", "error");
    return;
  }
  navigator.clipboard
    .writeText(text)
    .then(() => notify("已复制到剪贴板"))
    .catch((e) => notify((e as Error).message, "error"));
}

export default function AiAccess({ notify }: { notify: Notify }) {
  const [keys, setKeys] = React.useState<ApiKey[]>([]);
  const [tools, setTools] = React.useState<McpTool[]>([]);
  const [name, setName] = React.useState("ai-client");
  const [scopes, setScopes] = React.useState("read");
  const [expiresDays, setExpiresDays] = React.useState("30");
  const [secret, setSecret] = React.useState("");

  const origin = window.location.origin;

  const loadKeys = () => {
    api
      .apiKeys()
      .then(setKeys)
      .catch((e) => notify((e as Error).message, "error"));
  };

  const loadTools = () => {
    api
      .mcpTools()
      .then(setTools)
      .catch((e) => notify(`读取 MCP 工具列表失败：${(e as Error).message}`, "error"));
  };

  React.useEffect(() => {
    loadKeys();
    loadTools();
  }, []);

  const create = async () => {
    try {
      const created = await api.createApiKey({
        name: name || "key",
        scopes,
        expires_days: expiresDays ? Number(expiresDays) : null,
      });
      setSecret(created.key);
      notify(`已创建 key（明文只显示一次）`);
      loadKeys();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const toggle = async (key: ApiKey) => {
    try {
      await api.updateApiKey(key.id, { enabled: !key.enabled });
      loadKeys();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const remove = async (id: number) => {
    try {
      await api.deleteApiKey(id);
      notify(`已删除 key #${id}`);
      loadKeys();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const httpConfig = JSON.stringify(
    {
      mcpServers: {
        feicms: {
          type: "http",
          url: `${origin}/mcp`,
          headers: { "X-API-Key": "fk_把这里换成你创建的 key" },
        },
      },
    },
    null,
    2
  );

  const stdioConfig = JSON.stringify(
    {
      mcpServers: {
        feicms: {
          command: "uv",
          args: ["run", "--directory", "/path/to/feicms", "feicms", "mcp"],
        },
      },
    },
    null,
    2
  );

  const opencodeConfig = JSON.stringify(
    {
      mcp: {
        feicms: {
          type: "remote",
          url: `${origin}/mcp`,
          headers: { "X-API-Key": "fk_把这里换成你创建的 key" },
        },
      },
    },
    null,
    2
  );

  const probe = async () => {
    try {
      const info = await api.mcpInfo();
      notify(`MCP 服务正常：${info.tools.length} 个工具（${info.name} ${info.version}）`);
      loadTools();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <section>
      {/* ------------------------------------------------ API key 管理 */}
      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <h2 className="card-title">对外访问密钥（API Key）</h2>
          <p className="muted">
            同一个 key 既能访问 <span className="mono">/api/*</span>，也能访问 MCP 服务 <span className="mono">/mcp</span>。
            数据库只存 sha256 哈希，明文只在创建时显示一次，丢了只能重新创建。
          </p>

          <div className="mt-2 flex flex-wrap gap-2">
            <input
              className="input input-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="key 名称（用途）"
            />
            <select className="select select-sm" value={scopes} onChange={(e) => setScopes(e.target.value)}>
              <option value="read">read（只读：查询/搜索）</option>
              <option value="write">write（可采集 / 改源 / 下载海报）</option>
              <option value="read,write">read,write</option>
            </select>
            <input
              className="input input-sm"
              value={expiresDays}
              onChange={(e) => setExpiresDays(e.target.value)}
              placeholder="有效期（天，留空=永久）"
            />
            <button className="btn btn-sm btn-primary" onClick={create}>
              创建 key
            </button>
          </div>

          {secret && (
            <div className="alert alert-info mt-2">
              <span className="mono">{secret}</span>
              <button className="btn btn-sm btn-outline" onClick={() => copy(secret, notify)}>
                复制
              </button>
            </div>
          )}

          <table className="table mt-2">
            <thead>
              <tr>
                <th>#</th>
                <th>名称</th>
                <th>key</th>
                <th>权限</th>
                <th>状态</th>
                <th>使用次数</th>
                <th>最近使用</th>
                <th>有效期</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id}>
                  <td>{key.id}</td>
                  <td>{key.name}</td>
                  <td className="mono">{key.prefix}…</td>
                  <td className="mono">{key.scopes}</td>
                  <td>
                    <button
                      className={`btn btn-sm ${key.enabled ? "btn-success" : "btn-ghost"}`}
                      onClick={() => toggle(key)}
                    >
                      {key.enabled ? "启用" : "停用"}
                    </button>
                  </td>
                  <td>{key.usage_count}</td>
                  <td>{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "-"}</td>
                  <td>{key.expires_at ? new Date(key.expires_at).toLocaleDateString() : "永久"}</td>
                  <td>
                    <button className="btn btn-sm btn-outline" onClick={() => remove(key.id)}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!keys.length && (
                <tr>
                  <td colSpan={9} className="muted">
                    还没有 key。给 AI 客户端创建一个只读 key 即可。
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <p className="muted mt-2">
            命令行等价操作：<span className="mono">uv run feicms apikey create --name ai --scopes read</span> ·
            <span className="mono"> apikey list</span> · <span className="mono"> apikey revoke &lt;id&gt;</span>
          </p>
        </div>
      </div>

      {/* ------------------------------------------------ MCP 使用说明 */}
      <div className="card bg-base-100 border border-base-300 mt-1.5">
        <div className="card-body">
          <h2 className="card-title">MCP server 使用说明</h2>
          <p className="muted">
            MCP（Model Context Protocol）让 Claude / OpenCode / Cursor 这类 AI 客户端直接查询你的采集库：搜索影片、
            查看播放源、列出资源站、触发采集。服务有两种传输方式，工具集完全相同。
          </p>

          <ol className="mt-2">
            <li>
              在上面创建一个 key（AI 用建议 <span className="mono">read</span>，避免误触发采集）。
            </li>
            <li>
              把 key 填进客户端配置。HTTP 方式需要 header <span className="mono">X-API-Key: fk_…</span>（也支持
              <span className="mono">Authorization: Bearer fk_…</span>）。
            </li>
            <li>
              验证：<button className="btn btn-sm btn-outline" onClick={probe}>测试 MCP 连接</button>
            </li>
          </ol>

          <div className="grid grid-cols-2 gap-1.5 mt-3">
            <div>
              <h3 className="muted">方式 A：远程 HTTP（推荐，任何能联网的客户端）</h3>
              <pre className="mono bg-base-200 rounded-box p-3 whitespace-pre-wrap">{httpConfig}</pre>
              <button className="btn btn-sm btn-outline mt-1" onClick={() => copy(httpConfig, notify)}>
                复制配置
              </button>
            </div>
            <div>
              <h3 className="muted">方式 B：本地 stdio（Claude Desktop 等）</h3>
              <pre className="mono bg-base-200 rounded-box p-3 whitespace-pre-wrap">{stdioConfig}</pre>
              <button className="btn btn-sm btn-outline mt-1" onClick={() => copy(stdioConfig, notify)}>
                复制配置
              </button>
              <p className="muted mt-1">
                stdio 模式直接读同一个 <span className="mono">.env</span> 与数据库，属于本地可信进程；
                远程访问请用方式 A + API key。
              </p>
            </div>
          </div>

          <div className="mt-3">
            <h3 className="muted">OpenCode 配置（<span className="mono">opencode.json</span>）</h3>
            <pre className="mono bg-base-200 rounded-box p-3 whitespace-pre-wrap">{opencodeConfig}</pre>
          </div>

          <div className="mt-3">
            <h3 className="muted">手动验证（curl）</h3>
            <pre className="mono bg-base-200 rounded-box p-3 whitespace-pre-wrap">
              {`# 工具列表
curl -s -X POST ${origin}/mcp \\
  -H "X-API-Key: fk_..." -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 搜索影片
curl -s -X POST ${origin}/mcp \\
  -H "X-API-Key: fk_..." -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_videos","arguments":{"query":"流浪地球","limit":5}}}'`}
            </pre>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------ 工具列表 */}
      <div className="card bg-base-100 border border-base-300 mt-1.5">
        <div className="card-body">
          <h2 className="card-title">MCP 工具（实时读取 /mcp）</h2>
          <table className="table mt-1">
            <thead>
              <tr>
                <th>工具</th>
                <th>说明</th>
                <th>需要的权限</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => (
                <tr key={tool.name}>
                  <td className="mono">{tool.name}</td>
                  <td>{tool.description}</td>
                  <td className="mono">{tool.scopes.join(", ")}</td>
                </tr>
              ))}
              {!tools.length && <tr><td colSpan={3} className="muted">读取失败，点上面的「测试 MCP 连接」重试。</td></tr>}
            </tbody>
          </table>
          <p className="muted mt-2">
            只读 key 调用采集类工具会返回权限错误；只有管理员账号（<span className="mono">FEICMS_ADMIN_USER</span>）
            能创建/删除 key。
          </p>
        </div>
      </div>
    </section>
  );
}
