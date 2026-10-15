import React from "react";
import { api, authorizedFetch, type Config } from "./api";

interface Param {
  name: string;
  type: string;
  default?: string;
  note: string;
}

interface Endpoint {
  method: string;
  path: string;
  summary: string;
  params?: Param[];
  body?: string;
  example: string;
  tryable?: boolean;
}

interface Group {
  title: string;
  note?: string;
  endpoints: Endpoint[];
}

const GROUPS: Group[] = [
  {
    title: "认证",
    note:
      "配置 FEICMS_ADMIN_USER + FEICMS_ADMIN_PASSWORD 后，所有 /api/* 与 /images/* 都需要登录；" +
      "支持 Bearer token、Basic 认证和登录 cookie（/api/login 自动写入）。未配置则全部开放。",
    endpoints: [
      {
        method: "GET",
        path: "/api/auth/status",
        summary: "认证是否开启、当前请求是否已登录",
        example: "/api/auth/status",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/login",
        summary: "用户名/密码换取 token（同时设置 HttpOnly cookie）",
        params: [
          { name: "username", type: "string", note: "FEICMS_ADMIN_USER" },
          { name: "password", type: "string", note: "FEICMS_ADMIN_PASSWORD" },
        ],
        example: "/api/auth/status",
        tryable: false,
      },
      {
        method: "POST",
        path: "/api/logout",
        summary: "清除登录 cookie",
        example: "/api/auth/status",
        tryable: false,
      },
      {
        method: "GET",
        path: "/api/apikeys",
        summary: "列出 API key（只显示前缀，明文不可恢复）",
        example: "/api/apikeys",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/apikeys",
        summary: "创建 API key（明文只返回一次）",
        params: [
          { name: "name", type: "string", note: "用途名称" },
          { name: "scopes", type: "string", default: "read", note: "read | write | read,write" },
          { name: "expires_days", type: "int", note: "留空 = 永久" },
        ],
        body: '{"name": "ai", "scopes": "read"}',
        example: "curl -X POST -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{\"name\":\"ai\",\"scopes\":\"read\"}' http://127.0.0.1:8000/api/apikeys",
        tryable: false,
      },
      {
        method: "PATCH",
        path: "/api/apikeys/{id}",
        summary: "改名 / 改权限 / 启用停用 / 改有效期",
        example: "curl -X PATCH -H 'Authorization: Bearer <token>' -d '{\"enabled\":false}' http://127.0.0.1:8000/api/apikeys/1",
        tryable: false,
      },
      {
        method: "DELETE",
        path: "/api/apikeys/{id}",
        summary: "删除（吊销）一个 key",
        example: "curl -X DELETE -H 'Authorization: Bearer <token>' http://127.0.0.1:8000/api/apikeys/1",
        tryable: false,
      },
    ],
  },
  {
    title: "MCP（给 AI 客户端的只读 RAG 库）",
    note:
      "MCP 这一层只读：search / get_document / browse / related / read_chunks / list_types / get_stats。" +
      "采集、健康检查、海报下载等维护动作不在 MCP 里，只在管理面板、CLI 和 /api/*（需要管理员账号或 write key）。" +
      "两种传输：HTTP 的 POST /mcp（JSON-RPC），以及本地 stdio（uv run feicms mcp）。" +
      "用 API key 认证：X-API-Key: fk_... 或 Authorization: Bearer fk_...。",
    endpoints: [
      {
        method: "GET",
        path: "/mcp",
        summary: "MCP 服务信息 + 工具列表（read_only: true）",
        example: "curl -H 'X-API-Key: fk_...' http://127.0.0.1:8000/mcp",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/mcp/tools",
        summary: "MCP 工具清单（含每个工具需要的 scope）",
        example: "/api/mcp/tools",
        tryable: true,
      },
      {
        method: "POST",
        path: "/mcp",
        summary: "JSON-RPC：initialize / tools/list / tools/call / resources/list / resources/read",
        body: '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"query":"流浪地球","limit":5}}}',
        example: "curl -X POST -H 'X-API-Key: fk_...' -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}' http://127.0.0.1:8000/mcp",
        tryable: false,
      },
    ],
  },
  {
    title: "资源站管理",
    note: "增删改查采集源，启用/禁用单个源。",
    endpoints: [
      {
        method: "GET",
        path: "/api/sources",
        summary: "列出资源站（按优先级排序）",
        params: [
          { name: "enabled", type: "bool", note: "只列出启用 / 禁用站点" },
          { name: "health", type: "string", note: "ok | slow | dead | unknown" },
          { name: "q", type: "string", note: "站点名称模糊搜索" },
          { name: "limit", type: "int", default: "200", note: "1–1000" },
          { name: "offset", type: "int", default: "0", note: "分页偏移" },
        ],
        example: "/api/sources?enabled=true&health=ok&limit=20",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/sources/{id}",
        summary: "单个资源站详情",
        example: "/api/sources/1",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/sources",
        summary: "新增资源站（名称重复返回 409）",
        body: `{
  "name": "新资源站",
  "url": "https://api.example.com/api.php",
  "api_format": "xml",
  "enabled": true,
  "priority": 5,
  "max_pages": 5,
  "tags": "电影,剧集"
}`,
        example: "curl -X POST http://127.0.0.1:8000/api/sources -H 'Content-Type: application/json' -d '{\"name\":\"测试站\",\"url\":\"https://api.example.com/api.php\",\"api_format\":\"xml\"}'",
      },
      {
        method: "PATCH",
        path: "/api/sources/{id}",
        summary: "局部更新（只传需要改的字段）",
        body: `{ "url": "https://new-host.com/api.php", "enabled": false }`,
        example: "curl -X PATCH http://127.0.0.1:8000/api/sources/1 -H 'Content-Type: application/json' -d '{\"enabled\":false}'",
      },
      {
        method: "DELETE",
        path: "/api/sources/{id}",
        summary: "删除资源站（返回 204）",
        example: "curl -X DELETE http://127.0.0.1:8000/api/sources/1",
      },
      {
        method: "POST",
        path: "/api/sources/{id}/enable",
        summary: "启用 / 禁用某个源",
        body: `{ "enabled": true }`,
        example: "curl -X POST http://127.0.0.1:8000/api/sources/1/enable -H 'Content-Type: application/json' -d '{\"enabled\":true}'",
      },
    ],
  },
  {
    title: "采集与健康检查",
    note: "采集是增量式的：同一 (源, vod_id) 已存在则更新；连续失败会自动禁用。默认只抓每站 max_pages 页，且某一页没有新增/更新时会提前停止（全量模式 pages=0 关闭该提前停止）。",
    endpoints: [
      {
        method: "POST",
        path: "/api/sources/{id}/collect",
        summary: "只采集一个源，返回新增/更新/跳过统计（含该站 pagecount/total）",
        params: [
          {
            name: "pages",
            type: "int",
            note: "省略 = 该站 max_pages；>0 = 指定页数；0 = 真·全量，遍历该站真实报告的所有页（FEICMS_FULL_PAGE_CAP 只是保险闸，默认 0 = 无上限）",
          },
        ],
        example: "curl -X POST 'http://127.0.0.1:8000/api/sources/1/collect?pages=0'",
      },
      {
        method: "POST",
        path: "/api/collect",
        summary: "批量采集（同步等待，返回每源统计；站点多时会很久，浏览器可能超时，面板用的是 /api/collect/start）。若后台采集正在运行则返回 409",
        body: `{ "names": ["如意资源站", "白嫖资源站"], "pages": 3, "all": false }`,
        example: "curl -X POST http://127.0.0.1:8000/api/collect -H 'Content-Type: application/json' -d '{\"pages\":2}'",
      },
      {
        method: "POST",
        path: "/api/collect/start",
        summary: "在后台启动一轮采集，立即返回进度快照（UI 的「采集全部」和每站点「采集」都用这个；`source_ids` 只跑指定站点，`all: true` 也采集禁用站点）",
        body: `{ "names": [], "pages": null, "all": false }`,
        params: [
          {
            name: "pages",
            type: "int",
            note: "省略 = 每站 max_pages；>0 = 指定页数；0 = 全量遍历所有页",
          },
          { name: "source_ids", type: "int[]", note: "只采集这些站点（UI 每行「采集」「全量」按钮）" },
        ],
        example: "curl -X POST http://127.0.0.1:8000/api/collect/start -H 'Content-Type: application/json' -d '{\"source_ids\": [1, 2]}'",
      },
      {
        method: "GET",
        path: "/api/collect/progress",
        summary: "查看后台采集进度：running / total_sources / done_sources / inserted / updated / skipped / failed_sources，以及每个站点一条 `sources` 进度（status、pages、pagecount、target、inserted、updated、skipped、elapsed_ms、current）",
        example: "curl http://127.0.0.1:8000/api/collect/progress",
      },
      {
        method: "POST",
        path: "/api/health-check",
        summary: "对所有源做一次连通性 + 数据解析检查，写回 health_status / latency_ms",
        params: [{ name: "only_enabled", type: "bool", default: "true", note: "false 时也检查禁用源" }],
        example: "curl -X POST 'http://127.0.0.1:8000/api/health-check?only_enabled=true'",
      },
      {
        method: "GET",
        path: "/api/jobs",
        summary: "采集任务历史（每次采集一条记录）",
        params: [{ name: "limit", type: "int", default: "50", note: "1–200" }],
        example: "/api/jobs?limit=20",
        tryable: true,
      },
    ],
  },
  {
    title: "源订阅（自动更新源列表）",
    note: "订阅 JSON/TXT 资源站列表 feed，同步时按 URL 匹配：新增 → 插入，已存在 → 更新，feed 中消失 → 报告 missing（不自动删除）。",
    endpoints: [
      {
        method: "GET",
        path: "/api/subscriptions",
        summary: "列出订阅 feed（首次访问会自动写入 .env 里的默认订阅）",
        example: "/api/subscriptions",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/subscriptions",
        summary: "添加订阅（URL 重复返回 409）",
        body: `{ "url": "https://ziyuanzu.com/api/zy.php", "kind": "auto", "enabled": true }`,
        example: "curl -X POST http://127.0.0.1:8000/api/subscriptions -H 'Content-Type: application/json' -d '{\"url\":\"https://ziyuanzu.com/api/zy.php\"}'",
      },
      {
        method: "DELETE",
        path: "/api/subscriptions/{id}",
        summary: "删除订阅（返回 204）",
        example: "curl -X DELETE http://127.0.0.1:8000/api/subscriptions/1",
      },
      {
        method: "POST",
        path: "/api/subscriptions/sync",
        summary: "拉取 feed 并同步源列表，返回 added / updated / unchanged / missing",
        params: [{ name: "subscription_id", type: "int", note: "省略则同步所有启用的订阅" }],
        example: "curl -X POST http://127.0.0.1:8000/api/subscriptions/sync",
      },
    ],
  },
  {
    title: "影片库",
    note: "跨源去重后的合并结果：相同标题合并，播放源保留在 source_items。",
    endpoints: [
      {
        method: "GET",
        path: "/api/videos",
        summary: "分页搜索影片，返回 { total, page, limit, items }",
        params: [
          { name: "q", type: "string", note: "标题模糊搜索" },
          { name: "type_name", type: "string", note: "分类名，如 电影" },
          { name: "year", type: "string", note: "年份" },
          { name: "source_id", type: "int", note: "只看某个源贡献的影片" },
          { name: "has_playable", type: "bool", default: "false", note: "只看有播放源的影片" },
          { name: "sort", type: "string", default: "updated", note: "updated | name | source_count" },
          { name: "limit", type: "int", default: "20", note: "1–200" },
          { name: "offset", type: "int", default: "0", note: "分页偏移" },
        ],
        example: "/api/videos?q=流浪&sort=source_count&limit=10",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/videos/{id}",
        summary: "影片详情 + 所有播放源（plays 数组）",
        example: "/api/videos/1",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/videos/{id}/sources",
        summary: "该影片来自哪些源、每个源的原始 vod_id 与剧集列表",
        example: "/api/videos/1/sources",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/categories",
        summary: "分类及条目数量",
        example: "/api/categories",
        tryable: true,
      },
    ],
  },
  {
    title: "图片库（海报本地化）",
    note: "采集时只记录 pic 外链；扫描后把海报下载到本地，按 sha256 前 4 位分桶存放（data/images/3f/2a/3f2a…jpg），内容相同的不同 URL 只存一份。影片列表/详情里的 local_pic 字段就是 /images/<id>。",
    endpoints: [
      {
        method: "POST",
        path: "/api/images/scan",
        summary: "扫描所有采集记录/影片的 pic 字段，登记待下载图片（不联网，很快）",
        example: "curl -X POST http://127.0.0.1:8000/api/images/scan",
      },
      {
        method: "POST",
        path: "/api/images/download",
        summary: "下载一批待下载图片，返回 downloaded / duplicates / failed / skipped / bytes",
        body: `{ "limit": 300, "concurrency": 8, "retry_failed": false }`,
        example: "curl -X POST http://127.0.0.1:8000/api/images/download -H 'Content-Type: application/json' -d '{\"limit\":100}'",
      },
      {
        method: "GET",
        path: "/api/images/download/progress",
        summary: "当前（或最近一批）下载批次的实时计数：队列 / 成功 / 重复 / 失败 / 跳过 / 字节 / 耗时 / 错误",
        example: "/api/images/download/progress",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/images",
        summary: "列出图片记录（状态、域名、大小、路径）",
        params: [
          { name: "status", type: "string", note: "downloaded | pending | duplicate | failed" },
          { name: "q", type: "string", note: "按图片域名过滤" },
          { name: "limit", type: "int", default: "50", note: "1–500" },
          { name: "offset", type: "int", default: "0", note: "分页偏移" },
        ],
        example: "/api/images?status=downloaded&limit=10",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/images/{id}",
        summary: "单条图片记录（元数据 JSON，不是图片本身）",
        example: "/api/images/1",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/images/stats",
        summary: "图片库统计：总数、按状态、磁盘占用、已使用分桶数、Top 域名",
        example: "/api/images/stats",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/images/prune",
        summary: "删除已无采集记录引用的图片行（delete_files=true 时同时删文件）",
        example: "curl -X POST 'http://127.0.0.1:8000/api/images/prune?delete_files=true'",
      },
      {
        method: "POST",
        path: "/api/images/repair",
        summary: "校对索引与磁盘：文件存在但状态是 pending/failed 的记录修正为 downloaded，文件丢失的重新排队，清理 .tmp 残留",
        example: "curl -X POST http://127.0.0.1:8000/api/images/repair",
      },
      {
        method: "GET",
        path: "/api/images/layout",
        summary: "海报目录的分桶结构：文件总数、已使用的桶、最忙的桶（对应 CLI 的 feicms images layout）",
        params: [{ name: "top", type: "int", default: "12", note: "列出最忙的几个分桶" }],
        example: "/api/images/layout?top=10",
        tryable: true,
      },
      {
        method: "GET",
        path: "/images/{id}",
        summary: "返回本地图片文件（Content-Type 按实际格式）；未下载时 302 跳转到原外链",
        example: "/images/1",
      },
    ],
  },
  {
    title: "统计、配置与维护",
    endpoints: [
      {
        method: "GET",
        path: "/api/stats",
        summary: "总览：源数量、健康数、影片数、采集记录、分类分布、站点贡献",
        example: "/api/stats",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/config",
        summary: "当前生效的配置（来自 .env / 环境变量）",
        example: "/api/config",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/prune",
        summary: "清理无播放源的采集记录与孤儿影片",
        example: "curl -X POST http://127.0.0.1:8000/api/prune",
      },
      {
        method: "POST",
        path: "/api/repair-plays",
        summary: "用当前解析规则重建播放源结构（单集 / 多集）并清理文本字段，不重新采集",
        example: "curl -X POST http://127.0.0.1:8000/api/repair-plays",
      },
      {
        method: "POST",
        path: "/api/merge-duplicates",
        summary: "合并同标题的重复影片：把重复行的采集记录（含播放源）迁移到最完整的一条，再删除重复行",
        params: [{ name: "dry_run", type: "bool", default: "false", note: "true 只统计并返回示例，不改动数据" }],
        example: "curl -X POST 'http://127.0.0.1:8000/api/merge-duplicates?dry_run=true'",
      },
    ],
  },
  {
    title: "导入、导出与自动任务",
    note: "站点列表不在仓库里，管理面板的「资源站 → 站点列表」和「配置 → 备份与导出 / 自动任务」对应这些接口。",
    endpoints: [
      {
        method: "POST",
        path: "/api/sources/import",
        summary: "批量导入站点列表（name|url|format|uptime 行、裸 URL，或 YAML/JSON），已存在的 URL 更新而不是重复添加",
        body: '{"text": "站点名|https://example.com|auto|100", "max_pages": 10, "enabled": true}',
        example: "curl -X POST -H 'Content-Type: application/json' -d '{\"text\":\"A|https://a.com|auto\"}' http://127.0.0.1:8000/api/sources/import",
      },
      {
        method: "GET",
        path: "/api/sources/export",
        summary: "导出当前站点列表（yaml / json / csv），作为文件下载",
        params: [
          { name: "fmt", type: "string", default: "yaml", note: "yaml | json | csv" },
          { name: "enabled_only", type: "bool", default: "false", note: "只导出启用的站点" },
        ],
        example: "/api/sources/export?fmt=yaml",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/export",
        summary: "导出合并后的影片库（JSONL / JSON），含每个站点的播放源，用于备份或迁移",
        params: [
          { name: "limit", type: "int", default: "10000", note: "最多导出条数" },
          { name: "q", type: "string", note: "标题过滤" },
          { name: "fmt", type: "string", default: "jsonl", note: "jsonl | json" },
        ],
        example: "/api/export?limit=100&fmt=json",
        tryable: true,
      },
      {
        method: "GET",
        path: "/api/scheduler",
        summary: "后台自动任务状态：自动采集 / 海报开关、间隔、上次运行时间、当前采集进度",
        example: "/api/scheduler",
        tryable: true,
      },
      {
        method: "POST",
        path: "/api/scheduler",
        summary: "运行时开关自动采集 / 自动海报，或改间隔（不需要重启服务）",
        body: '{"auto_collect": true, "collect_interval_minutes": 30}',
        example: "curl -X POST -H 'Content-Type: application/json' -d '{\"auto_collect\":false}' http://127.0.0.1:8000/api/scheduler",
      },
      {
        method: "POST",
        path: "/api/admin/password",
        summary: "写入管理员账号到 .env（密码留空则随机生成），明文只返回一次",
        body: '{"username": "admin", "password": "改成强密码"}',
        example: "curl -X POST -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{\"username\":\"admin\"}' http://127.0.0.1:8000/api/admin/password",
      },
      {
        method: "POST",
        path: "/api/admin/token",
        summary: "生成一个签名 token（给 curl / 脚本用），默认 1 小时",
        params: [{ name: "ttl", type: "int", default: "3600", note: "秒" }],
        example: "curl -X POST -H 'Authorization: Bearer <token>' 'http://127.0.0.1:8000/api/admin/token?ttl=3600'",
      },
    ],
  },
  {
    title: "对外提供 API（聚合站形态）",
    note: "把本地库以「资源站」格式对外输出，下游采集程序可直接把 feicms 当作一个源。",
    endpoints: [
      {
        method: "GET",
        path: "/provide/vod",
        summary: "与上游资源站一致的 list 接口：code / page / pagecount / total / list",
        params: [
          { name: "ac", type: "string", default: "list", note: "list" },
          { name: "q", type: "string", note: "标题搜索" },
          { name: "t", type: "string", note: "分类名" },
          { name: "pg", type: "int", default: "1", note: "页码" },
          { name: "limit", type: "int", default: "20", note: "1–100" },
        ],
        example: "/provide/vod?q=流浪&pg=1&limit=5",
        tryable: true,
      },
    ],
  },
  {
    title: "其他",
    endpoints: [
      {
        method: "GET",
        path: "/docs",
        summary: "FastAPI 自动生成的交互式 OpenAPI 文档（Swagger UI）",
        example: "/docs",
      },
      {
        method: "GET",
        path: "/openapi.json",
        summary: "OpenAPI 3 规范 JSON，可导入 Postman / 生成客户端",
        example: "/openapi.json",
        tryable: true,
      },
      {
        method: "GET",
        path: "/",
        summary: "后台界面（静态 SPA）",
        example: "/",
      },
    ],
  },
];

const METHOD_BADGE: Record<string, string> = {
  GET: "badge-info",
  POST: "badge-success",
  PATCH: "badge-warning",
  DELETE: "badge-error",
};

export default function ApiDocs({ notify }: { notify: (text: string, kind?: "success" | "error") => void }) {
  const [config, setConfig] = React.useState<Config | null>(null);
  const [open, setOpen] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<{ key: string; text: string; error?: string } | null>(null);

  React.useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);

  const origin = window.location.origin;

  const run = async (endpoint: Endpoint) => {
    const key = `${endpoint.method} ${endpoint.path}`;
    setResult({ key, text: "请求中…" });
    try {
      const res = await authorizedFetch(endpoint.example.startsWith("http") ? endpoint.example : origin + endpoint.example);
      const text = await res.text();
      let pretty = text;
      try {
        pretty = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        /* keep raw */
      }
      setResult({ key, text: `${res.status} ${res.statusText}\n\n${pretty.slice(0, 4000)}` });
    } catch (e) {
      setResult({ key, text: (e as Error).message, error: (e as Error).message });
      notify((e as Error).message, "error");
    }
  };

  return (
    <section>
      <div className="card bg-base-100 border border-base-300 mb-3">
        <div className="card-body">
          <h2 className="card-title">API 说明</h2>
          <p className="muted">
            所有接口都是 JSON，无需鉴权（单机/内网使用）。Base URL：
            <code className="mono">{origin}</code>
          </p>
          <div className="flex flex-wrap gap-2 mt-2">
            <span className="badge badge-outline">数据库 {config?.database_url ?? "-"}</span>
            <span className="badge badge-outline">并发 {config?.concurrency ?? "-"}</span>
            <span className="badge badge-outline">默认页数 {config?.default_pages ?? "-"}</span>
            <span className="badge badge-outline">自动采集 {config?.auto_collect ? `每 ${config?.collect_interval_minutes} 分钟` : "关闭"}</span>
            <span className="badge badge-outline">订阅同步 {config?.subscribe_interval_minutes ?? "-"} 分钟</span>
            <span className="badge badge-outline">图片目录 {config?.image_dir ?? "-"}</span>
            <span className="badge badge-outline">图片自动下载 {config?.image_auto ? `每 ${config?.image_interval_minutes} 分钟 ${config?.image_batch} 张` : "关闭"}</span>
          </div>
          <p className="muted mt-2">
            交互式文档（可直接试请求）：<a href="/docs" className="link">/docs</a> · 规范文件：<a href="/openapi.json" className="link">/openapi.json</a>
          </p>
        </div>
      </div>

      {GROUPS.map((group) => (
        <div className="card bg-base-100 border border-base-300 mb-3" key={group.title}>
          <div className="card-body">
            <h3 className="card-title">{group.title}</h3>
            {group.note && <p className="muted">{group.note}</p>}
            <table className="table mt-2">
              <thead>
                <tr>
                  <th>方法</th>
                  <th>路径</th>
                  <th>说明</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {group.endpoints.map((endpoint) => {
                  const key = `${endpoint.method} ${endpoint.path}`;
                  const expanded = open === key;
                  return (
                    <tr key={key}>
                      <td>
                        <span className={`badge ${METHOD_BADGE[endpoint.method] ?? "badge-neutral"}`}>{endpoint.method}</span>
                      </td>
                      <td className="mono">{endpoint.path}</td>
                      <td>
                        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(expanded ? null : key)}>
                          {endpoint.summary}
                        </button>
                      </td>
                      <td>
                        {endpoint.tryable && (
                          <button className="btn btn-sm btn-outline" onClick={() => run(endpoint)}>
                            试
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {open &&
              GROUPS.flatMap((g) => g.endpoints).filter((e) => `${e.method} ${e.path}` === open).map((endpoint) => (
                <div className="mt-3 flex flex-col gap-2">
                  {endpoint.params?.length ? (
                    <div>
                      <b className="muted">参数</b>
                      <table className="table">
                        <thead>
                          <tr><th>名称</th><th>类型</th><th>默认</th><th>说明</th></tr>
                        </thead>
                        <tbody>
                          {endpoint.params.map((p) => (
                            <tr key={p.name}>
                              <td className="mono">{p.name}</td>
                              <td className="mono">{p.type}</td>
                              <td className="mono">{p.default ?? "-"}</td>
                              <td className="muted">{p.note}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}

                  {endpoint.body && (
                    <div>
                      <b className="muted">请求体</b>
                      <pre className="code-block">{endpoint.body}</pre>
                    </div>
                  )}

                  <div>
                    <b className="muted">示例</b>
                    <pre className="code-block">{endpoint.example}</pre>
                  </div>

                  {result && `${result.key}` === open && (
                    <div>
                      <b className="muted">响应</b>
                      <pre className={`code-block ${result.error ? "code-block-error" : ""}`}>{result.text}</pre>
                    </div>
                  )}
                </div>
              ))}
          </div>
        </div>
      ))}

      <div className="card bg-base-100 border border-base-300">
        <div className="card-body">
          <h3 className="card-title">字段速查</h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <b className="muted">Source</b>
              <p className="mono">id, name, url, api_format, enabled, priority, max_pages, tags, uptime, origin, subscription_url, health_status, latency_ms, last_collect_at, last_health_at, last_error, fail_streak, item_count</p>
            </div>
            <div>
              <b className="muted">Video</b>
              <p className="mono">id, name, type_name, year, area, lang, pic, local_pic, blurb, actor, director, remarks, source_count, last_updated, plays[]</p>
            </div>
            <div>
              <b className="muted">Image</b>
              <p className="mono">id, url, host, status, path, ext, sha256, size_bytes, attempts, last_error, first_seen, downloaded_at</p>
            </div>
            <div>
              <b className="muted">Subscription</b>
              <p className="mono">id, url, kind, enabled, last_sync_at, last_added, last_updated, last_entries, last_error</p>
            </div>
            <div>
              <b className="muted">CollectJob</b>
              <p className="mono">id, source_id, started_at, finished_at, pages, inserted, updated, skipped, error</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
