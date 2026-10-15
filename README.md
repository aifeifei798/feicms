# feicms

<p align="center">
  <img src="assets/logo-banner.png" alt="feicms" width="640" />
</p>

[![test](https://github.com/aifeifei798/feicms/actions/workflows/test.yml/badge.svg)](https://github.com/aifeifei798/feicms/actions/workflows/test.yml)

资源站（采集站）聚合系统：把多个 `api.php/provide/vod` 类型的站点统一采集进一个数据库，提供 FastAPI 查询接口、CLI 运维工具和一个 DaisyUI 管理面板。

```
资源站 API  ──fetch──►  解析 / 归一化  ──upsert──►  SQLite / MySQL  ──API──►  前端 / CMS
                                   ▲                              │
                                   └──── 定时采集 · 健康检查 · 源订阅 ────┘
```

> **⚠️ 本仓库不包含任何资源站列表、采集数据或图片。**
> 仓库里只有代码、测试和一个模板文件 `sources.example.yaml`。站点列表要么自己写，要么用「源订阅」自动同步（见下文）。

---

## 1. 它做什么

| 能力 | 说明 |
|---|---|
| 多源采集 | 支持 JSON / XML 两种响应格式，自动识别；同一影片跨站点按标题归一化合并 |
| 增量采集 | 每页若没有新增或更新记录就提前停止，避免重复扫全表 |
| 源健康监控 | 定时探测每个站点（HTTP 状态、延迟、返回条目数），连续失败自动禁用 |
| 源订阅 | 注册「资源站列表 feed」（JSON 或 TXT），定时同步，站点列表自动增删 |
| 海报本地化 | `vod_pic` 图片下载到本地，按内容 hash 分桶存储，见 [第 6 节](#6-海报图片本地化目录分桶策略) |
| 对外提供 | `GET /provide/vod` 输出标准资源站格式，下游 CMS 可以直接采集 |
| 管理面板 | React + Vite + Tailwind + DaisyUI：站点 CRUD、批量导入/导出站点、采集、健康、影片搜索、图片库、任务历史、自动任务开关、备份导出、管理员账号、API key、MCP 工具试跑、配置 |

技术栈：Python 3.10+ / FastAPI / SQLAlchemy 2.0 (async) / `curl_cffi`（浏览器 TLS 指纹） / `lxml` / typer + rich；前端 React 18 + Vite 5 + Tailwind 4 + DaisyUI 5。

---

## 2. 快速开始

```bash
./install.sh            # 建 venv + 装依赖 + 构建前端 + 生成 .env + init-db + 自动启动
./server.sh status      # 运行状态 / 健康 / 是否开启登录
./server.sh url         # 本机 + 局域网访问地址
```

`install.sh` 会自动：

1. 检查 Python 3.10+；有 `uv` 用 `uv sync`，没有则 `python3 -m venv .venv` + `pip install -r requirements.txt`；
2. 构建前端（有 `pnpm` 用 pnpm，否则 `npm`；都没有则跳过，API 仍然可用）；
3. 生成 `.env`（若不存在，从 `.env.example` 复制）并**随机生成管理员密码**；
4. 复制 `sources.example.yaml` → `sources.yaml`（仓库不带真实站点列表，见第 3 节），建表并 seed；
5. **启动服务**（后台，监听 `0.0.0.0:8000`，日志写到 `data/server.log`）。

常用选项：

```bash
./install.sh --no-frontend     # 只装后端
./install.sh --mysql           # 额外安装 aiomysql
./install.sh --force-env       # 覆盖已有 .env（重新生成密码）
./install.sh --no-start        # 安装后不自动启动
./install.sh --systemd         # 注册 systemd 服务并开机自启（需要 root/sudo）
```

手动安装（等价）：

```bash
uv sync --extra dev
cp .env.example .env
uv run feicms init-db && uv run feicms seed
cd web && pnpm install && pnpm run build     # 产物 web/dist，由 FastAPI 挂载
```

打开：

- `http://localhost:8000/` — 管理面板（需要登录，见第 9 节）
- `http://localhost:8000/docs` — OpenAPI 交互式文档
- `http://localhost:8000/api/stats` — 总览统计

默认监听 `0.0.0.0:8000`，所以局域网里的其它设备（手机/其它电脑）也能直接访问 `http://<机器IP>:8000/`。
`./server.sh url` 会打印本机 + 局域网地址；只想本机访问：`./server.sh start --host 127.0.0.1`。

---

## 3. 站点列表从哪里来

仓库**不附带**站点列表。三种方式：

**a) 手写 `sources.yaml`**（模板见 `sources.example.yaml`）

```yaml
sources:
  - name: 示例资源站
    url: https://example.com/api.php/provide/vod/
    api_format: json          # json | xml | auto
    enabled: true
    priority: 1000
    max_pages: 5
    tags: general
    uptime: 99.9
```

也支持简写一行式：`"名称|url|json|99.9"`。

**b) 命令行逐个添加**

```bash
uv run feicms add "示例站" https://example.com/api.php/provide/vod/ --format json --pages 5
```

**c) 源订阅自动同步**（推荐，列表会自动保持最新）

```bash
uv run feicms subscribe --add https://<your-feed>/interfaces.json
uv run feicms subscribe --sync
```

`FEICMS_SUBSCRIPTIONS` 里配置的 feed（逗号分隔）会在启动后按 `FEICMS_SUBSCRIBE_INTERVAL` 分钟自动同步；面板的「源订阅」卡片也能做同样的事。feed 格式支持 JSON（`[{name, url, ...}]`）和 TXT（每行 `名称|url|格式|uptime`）。

**d) 管理面板批量导入 / 导出**

「资源站 → 站点列表 → 批量导入」粘贴一份列表（每行 `名称|url|格式|可用率`，只写 URL 也行，`#` 注释会被忽略，YAML/JSON 也接受），已存在的 URL 会更新而不是重复添加；同一卡片可以导出当前列表为 YAML / JSON / CSV，导出结果可以直接喂回 `feicms seed` 或另一台实例。对应接口：

```bash
POST /api/sources/import        # {"text": "...", "max_pages": 10, "enabled": true}
GET  /api/sources/export?fmt=yaml|json|csv
```

---

## 4. 采集与页数语义

| 参数 | 含义 |
|---|---|
| `--pages N` | 每个站点抓 N 页 |
| `--pages 0` / `--full` | 真·全量：遍历该站**真实报告**的页数（`pagecount`，没有则用 `total / FEICMS_PAGE_LIMIT` 推算，都没有则一直抓到空页为止），不做提前停止；`FEICMS_FULL_PAGE_CAP` 只是保险闸，默认 `0` = 无上限 |
| 省略 | 使用 `FEICMS_PAGES`（默认 5），或站点自己的 `max_pages` |

采集结果会打印 `采集页/总页 new= updated= skipped= total= 耗时`，并写入 `collect_jobs` 表用于审计。

「全量」不是固定页数：每轮都会按该站**真实报告**的 `pagecount` 遍历（站点只给 `total` 时用 `total ÷ 该页实际返回的条目数` 推算，因为很多站强制每页 20 条；两个都没有就一直抓到空页为止，并有一个 10000 页的防失控兜底）。如果某个站忽略分页参数（每页返回同一批条目），采集会自动停止，不会空跑几千页。`FEICMS_FULL_PAGE_CAP` 现在默认 `0`（无上限），只作为保险闸：想防止某个 3651 页的站跑太久，可以设成例如 `500`。全量一轮请求量很大（常见站点 3000～8000 页），建议只对需要完整库的站点用 `全量`，日常增量用默认页数。

采集并发默认 `FEICMS_CONCURRENCY=0`，即**全部站点同时开**：每个资源站是独立站点，各自一个连接、各自翻页，互不排队。只有当你的网络或上游扛不住时才设成数字（例如 `8`）。SQLite 已开启 WAL 并在写冲突时自动重试整页，几十个站同时写不会报 "database is locked"。

一轮全量中**单页超时不会中断整轮**：失败的页会被跳过并记录，只有连续 5 页失败才认为该站不可达并停止（进度表会显示「跳过 N 页」和停止原因）。第 1 页失败才算该站采集失败。

管理面板的「采集全部」是**后台任务**：`POST /api/collect/start` 立即返回进度快照，前端轮询 `GET /api/collect/progress`。同一时间只会有一轮采集，重复点击不会并发跑第二轮（`POST /api/collect` 在后台运行时会返回 409）。这样即使 58 个站点跑几分钟也不会因为浏览器/代理超时而失败。

「采集」页面（侧栏 数据 → 采集）是采集的唯一入口：总进度条 + 每个站点一行，状态（采集中 / 待采集 / 完成 / 失败）、进度条（已采页数 / 本次计划页数）、新增 / 更新 / 跳过 / 耗时，每行还有「采集」「全量」按钮可以只跑这一个站点。排序是采集中 → 待采集 → 完成 → 失败，本轮没参与的站点沉到底部并标注「未采集」，已禁用的站点显示「已禁用」且采集按钮不可用。失败站点只显示「失败」徽章，不列出错误文本（原始错误仍写入 `collect_jobs` 表，`GET /api/jobs` 可以查），工具栏的「重试失败的 N 个」只重跑这些站点。

「资源站」页面只负责管理站点本身（新增 / 批量导入 / 编辑 / 启用禁用 / 删除 / 健康检查 / 导出列表），不再有采集按钮和页数选择。

资源站列表按健康状态排序：`ok` → `slow` → 未检查 → `dead` → 已禁用，同组内再按 priority 降序。所以跑一轮健康检查后，不可用的站点会自动沉到列表底部（面板「资源站 → 健康检查」和 `feicms sources` 都是这个顺序）。

同一影片在不同站点标题略有差异（例如带年份 `月亮求抱抱|2026`），旧数据里可能被拆成两条 `videos`。`feicms merge-duplicates`（或面板「配置 → 数据维护 → 合并重复影片」）会把标题归一化后合并成一条：重复行的采集记录（含全部播放源）迁移到最完整的那条，海报索引重新指向，旧 `signature` 重键，重复行删除。`--dry-run` / `?dry_run=true` 只统计并给出示例，不改动数据。

数据模型：

| 表 | 作用 |
|---|---|
| `sources` | 站点配置与健康状态（url、enabled、priority、max_pages、health_status、latency_ms、fail_streak） |
| `source_items` | 每个站点原始记录，唯一键 `(source_id, source_vod_id)`，播放源存为 JSON |
| `videos` | 跨站点合并后的影片（按标题归一化 signature 去重） |
| `source_types` | 每个站点 `ac=types` 抓到的分类映射 |
| `images` | 海报图片索引（url、status、path、sha256、size、attempts） |
| `collect_jobs` | 每次采集的审计记录 |
| `subscriptions` | 源订阅 feed |
| `settings` | 运行时覆盖的配置 |

---

## 5. CLI

```bash
feicms init-db                     # 建表
feicms seed [--file sources.yaml]  # 导入站点列表（幂等）
feicms sources [--enabled-only]    # 列出站点与健康状态
feicms add <name> <url>            # 添加一个站点
feicms subscribe [--add|--remove|--sync|--url]   # 源订阅管理
feicms collect [--name|--pages|--full|--include-disabled]
feicms health                      # 探测所有启用站点
feicms stats                       # 统计
feicms prune                       # 删除无播放源记录 / 孤儿影片
feicms repair-plays                # 用当前解析规则重建播放源结构 + 清理文本字段（不联网）
feicms merge-duplicates [--dry-run] # 合并同标题的重复影片（旧 signature 带年份造成的拆分）
feicms export [--out backup.jsonl]

feicms images scan                 # 登记所有 pic 字段（不联网）
feicms images download [--limit --concurrency --retry-failed]
feicms images stats                # 状态分布 / 磁盘占用 / 分桶数 / Top 域名
feicms images prune [--delete-files/--keep-files]
feicms images repair               # 校对数据库状态与磁盘文件
feicms images layout [--top 12]    # 打印分桶结构

feicms passwd [--user admin --password xxx]   # 写入 .env 的管理员账号（留空则随机生成）
feicms token [--ttl 3600]          # 打印一个 API token（curl 用）

feicms apikey create --name ai --scopes read [--expires-days 30]
feicms apikey list / update <id> / revoke <id>   # API key 管理
feicms mcp                         # 以 stdio 方式跑 MCP server（给 AI 客户端）
feicms mcp-config [--url http://localhost:8000/mcp]

feicms serve [--host --port]
```

### 面板覆盖：CLI 能做的事，后台页面都有

| CLI | 面板位置 | 接口 |
|---|---|---|
| `seed` / `add` | 资源站 → 站点列表 → 批量导入 / 新增站点 | `POST /api/sources`, `POST /api/sources/import` |
| `sources` / 启用禁用 | 资源站列表（筛选、开关、编辑、删除） | `GET /api/sources`, `PATCH /api/sources/{id}` |
| — | 站点列表 → 导出 YAML / JSON / CSV | `GET /api/sources/export?fmt=` |
| `collect` / `health` | 采集 → 采集全部 / 每站点「采集」「全量」 / 重试失败的；资源站 → 健康检查 | `POST /api/collect/start`, `GET /api/collect/progress`, `POST /api/health-check` |
| `subscribe` | 资源站 → 源订阅 | `GET/POST/DELETE /api/subscriptions` |
| `stats` | 首页总览 | `GET /api/stats` |
| `prune` / `repair-plays` / `merge-duplicates` | 配置 → 数据维护 | `POST /api/prune` 等 |
| `export` | 配置 → 备份与导出 | `GET /api/export?fmt=jsonl\|json` |
| `images scan/download/stats/prune/repair/layout` | 图片库（扫描、下载一批、进度、列表、分桶结构） | `POST /api/images/scan` 等，`GET /api/images/layout` |
| `apikey` | AI / MCP 接入 → 密钥管理 | `GET/POST/DELETE /api/apikeys` |
| `mcp` | AI / MCP 接入 → 工具试跑（用当前登录身份直接调 `POST /mcp`） | `POST /mcp` |
| `passwd` | 配置 → 管理员账号（随机生成 / 写入 `.env`） | `POST /api/admin/password` |
| `token` | 配置 → 管理员账号 → 生成 curl 用的 token | `POST /api/admin/token` |
| `serve` | `./server.sh start/restart/status` | — |

自动任务（自动采集、自动海报）在「配置 → 自动任务」里开关和改间隔，**改完立即生效，不需要重启服务**：`GET /api/scheduler` 读状态，`POST /api/scheduler` 改开关/间隔。

---

## 6. 海报图片本地化（目录分桶策略）

图片数量可能达到几十万，所以不能放在一个平铺目录里。

```
data/images/
  3f/2a/3f2a9c1b…c1.jpg      # <sha[0:2]>/<sha[2:4]>/<sha>.<ext>
  c4/11/c411bd…e2.webp
```

- 文件名是图片**内容字节**的 **sha256**（不是标题，也不是 URL；URL 归一化只用于登记去重）；
- 前 4 个字符分成两级目录 → 256 × 256 = **65536 个桶**，即使 50 万张海报，每个目录平均只有几个文件；
- **数据库是索引**：`images` 表存 url / status / path / sha256 / size，服务永远不遍历文件系统；
- 同一内容不同 URL（同一海报挂在两个 CDN）只存一份，第二条记录标记 `duplicate` 并指向同一个文件；
- 用 magic bytes 判断真实格式（jpg/png/gif/bmp/webp），非图片内容直接标记失败，不会污染目录；
- 访问路径 `GET /images/<id>`：文件存在返回本地文件（带 `Cache-Control`），未下载则 302 跳转到原外链，UI 不会出现破图。

自动下载默认关闭（`FEICMS_IMAGE_AUTO=false`），需要时手动跑：

```bash
feicms images scan
feicms images download --limit 1000 --concurrency 10
feicms images stats
feicms images repair     # 崩溃/并发留下的不一致，校对状态与磁盘
```

同一进程内不会重叠下载（自动循环与手动「下载一批」互斥）；跨进程靠下载前重新检查该行状态。

**下载队列的语义**（面板「图片库」和 CLI 一致）：

- 只有状态是 `pending` 的行会被排队：**已下载**、**重复**、**失败**的都不会再请求；
- 失败的海报不会自动重试，要重试点面板的「重试失败的」或 `feicms images download --retry-failed`（列表里的「尝试」列显示它失败过几次）；
- 磁盘上已有文件但索引状态是 `pending` 的行（崩溃/并发留下的不一致）会被自动修正成 `downloaded`，不会重新下载，计入「跳过」；
- 面板的下载进度卡片默认不列出失败原因，点「失败原因 (N)」才展开。

---

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务健康（无需登录） |
| GET | `/api/auth/status` | 认证是否开启、当前请求是否已登录 |
| POST | `/api/login` | `{username, password}` → token（同时设置 cookie） |
| POST | `/api/logout` | 清除登录 cookie |
| GET / POST / PATCH / DELETE | `/api/apikeys[/{id}]` | API key 管理（仅管理员账号） |
| GET | `/api/mcp/tools` | MCP 工具清单（含每个工具需要的 scope） |
| GET | `/api/sources` | 站点列表（`?enabled=&health=&q=`） |
| POST | `/api/sources` | 新增站点 |
| POST | `/api/sources/import` | 批量导入站点列表（`{text, max_pages, enabled}`），已存在的 URL 更新而非重复添加 |
| GET | `/api/sources/export` | 导出站点列表 `?fmt=yaml\|json\|csv`（`?enabled_only=true` 只导出启用的） |
| GET / PATCH / DELETE | `/api/sources/{id}` | 查看 / 修改 / 删除 |
| POST | `/api/sources/{id}/enable` | 启用或禁用 |
| POST | `/api/sources/{id}/collect` | 立即采集该站点（`?pages=0` 全量） |
| POST | `/api/collect` | 采集所有启用站点 `{pages, source_ids}`（同步；后台采集运行中返回 409） |
| POST | `/api/collect/start` | 后台启动一轮采集，立即返回进度快照 |
| GET | `/api/collect/progress` | 后台采集进度：总数 + 每个站点一条 `sources` 进度（status / pages / pagecount / target / inserted / updated / skipped / elapsed_ms） |
| POST | `/api/health-check` | 探测所有站点 |
| GET | `/api/videos` | 搜索：`q, type_name, year, source_id, sort, limit, offset` |
| GET | `/api/videos/{id}` | 影片详情 + 全部播放源 |
| GET | `/api/videos/{id}/sources` | 该影片的来源明细 |
| GET | `/api/categories` | 分类统计 |
| GET | `/api/stats` | 总览统计 |
| GET | `/api/jobs` | 采集历史 |
| GET | `/api/config` | 当前生效的配置 |
| GET / POST / DELETE | `/api/subscriptions[/{id}]` | 源订阅管理 |
| POST | `/api/subscriptions/sync` | 拉取 feed 并同步站点（可加 `?subscription_id=`） |
| GET | `/api/images/stats` | 图片库统计 |
| POST | `/api/images/scan` | 登记待下载图片 |
| POST | `/api/images/download` | 下载一批 `{limit, concurrency, include_failed}` |
| GET | `/api/images/download/progress` | 当前/最近一批的实时计数（队列、成功、重复、失败、跳过、字节、耗时、错误） |
| GET | `/api/images` | 图片列表（`?status=&q=&limit=&offset=`） |
| GET | `/api/images/{id}` | 单条图片记录 |
| POST | `/api/images/prune` | 删除无引用图片 |
| POST | `/api/images/repair` | 校对索引与磁盘 |
| GET | `/api/images/layout` | 海报目录分桶结构（文件数、已用桶、最忙桶） |
| GET | `/images/{id}` | 返回本地图片文件，未下载则 302 到原 URL |
| POST | `/api/prune` | 清理无播放源记录 / 孤儿影片 |
| POST | `/api/repair-plays` | 用当前解析规则重建播放源结构（不联网） |
| POST | `/api/merge-duplicates` | 合并同标题的重复影片（`?dry_run=true` 只统计） |
| GET | `/api/export` | 导出合并后的影片库 `?fmt=jsonl\|json&limit=&q=`（文件下载） |
| GET | `/api/scheduler` | 自动任务状态：开关、间隔、上次运行时间、当前采集进度 |
| POST | `/api/scheduler` | 运行时开关自动采集 / 自动海报，改间隔（无需重启） |
| POST | `/api/admin/password` | 写入管理员账号到 `.env`（密码留空则随机生成，仅管理员） |
| POST | `/api/admin/token` | 生成签名 token（`?ttl=3600`，仅管理员） |
| GET | `/provide/vod` | 兼容资源站格式，可被下游 CMS 直接采集 |
| GET / POST | `/mcp` | MCP server（JSON-RPC：`initialize` / `tools/list` / `tools/call`） |
| GET | `/docs` · `/openapi.json` | OpenAPI 文档 |

---

## 8. 配置（`.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `FEICMS_DB` | `sqlite+aiosqlite:///data/feicms.db` | 换成 `mysql+aiomysql://user:pass@host/db` 即可（`uv sync --extra mysql`） |
| `FEICMS_SOURCES` | `sources.yaml` | 站点列表文件 |
| `FEICMS_SUBSCRIPTIONS` | （空） | 逗号分隔的源订阅 feed，由你自己提供 |
| `FEICMS_SUBSCRIBE_INTERVAL` | 1440 | 订阅同步间隔（分钟） |
| `FEICMS_PAGES` | 5 | 默认采集页数 |
| `FEICMS_PAGE_LIMIT` | 40 | 每页请求条目数 |
| `FEICMS_FULL_PAGE_CAP` | 0 | 全量扫描的保险闸（0 = 无上限，按站点真实页数遍历） |
| `FEICMS_CONCURRENCY` | 0 | 同时采集的站点数（0 = 全部站点同时开，每个站一个连接） |
| `FEICMS_TIMEOUT` | 20 | 请求超时（秒） |
| `FEICMS_IMPERSONATE` | `chrome124` | curl_cffi 浏览器指纹 |
| `FEICMS_DELAY` | 0.3 | 请求间隔（秒） |
| `FEICMS_RETRIES` | 3 | 重试次数 |
| `FEICMS_AUTO_COLLECT` | true | 后台定时采集 |
| `FEICMS_COLLECT_INTERVAL` | 60 | 采集间隔（分钟） |
| `FEICMS_HEALTH_INTERVAL` | 30 | 健康检查间隔（分钟） |
| `FEICMS_HEALTH_THRESHOLD` | 3 | 连续失败几次后自动禁用 |
| `FEICMS_IMAGE_DIR` | `data/images` | 图片目录 |
| `FEICMS_IMAGE_BATCH` | 300 | 每批下载数量 |
| `FEICMS_IMAGE_CONCURRENCY` | 8 | 图片并发 |
| `FEICMS_IMAGE_DELAY` | 0.15 | 图片请求间隔 |
| `FEICMS_IMAGE_MAX_SIZE` | 2000000 | 单文件最大字节 |
| `FEICMS_IMAGE_AUTO` | false | 后台自动下载图片（默认关闭，手动下载） |
| `FEICMS_IMAGE_INTERVAL` | 30 | 自动下载间隔（分钟） |
| `FEICMS_HOST` / `FEICMS_PORT` | `0.0.0.0` / `8000` | 服务监听 |
| `FEICMS_ADMIN_USER` / `FEICMS_ADMIN_PASSWORD` | （空） | 管理员账号；两者都设置才启用登录 |
| `FEICMS_SECRET` | （空） | token 签名密钥，留空则由 user+password 派生 |
| `FEICMS_TOKEN_TTL` | 86400 | token 有效期（秒） |
| `FEICMS_PUBLIC_PROVIDE` | true | `/provide/vod` 是否允许匿名访问 |

---

## 9. 服务管理（`server.sh`）

```bash
./server.sh start              # 后台启动（PID 文件 data/server.pid，日志 data/server.log）
./server.sh stop               # 停止（PID 文件丢失时会按端口找进程）
./server.sh restart
./server.sh status             # pid / 运行时长 / /health / 认证状态
./server.sh logs               # 最近 40 行
./server.sh logs -f           # 持续跟踪
./server.sh dev                # 前台 + --reload（开发）
./server.sh build              # 构建前端
./server.sh init               # init-db + seed
./server.sh collect --pages 3
./server.sh health
./server.sh images scan|download|stats|repair|prune
./server.sh passwd --user admin --password 你的密码    # 改 .env 里的账号
./server.sh token              # 打印一个 API token（curl 用）
./server.sh url                # 打印本机 + 局域网访问地址
./server.sh systemd            # 打印 systemd 服务配置（开机自启）
```

脚本优先用 `uv`，没有 `uv` 时回退到 `.venv/bin/...`；端口和 host 从 `.env` 读取。

开机自启（Pi / 服务器）：

```bash
./install.sh --systemd                 # 自动写入 /etc/systemd/system/feicms.service 并 enable
# 或手动：
./server.sh systemd | sudo tee /etc/systemd/system/feicms.service
sudo systemctl daemon-reload && sudo systemctl enable --now feicms
systemctl status feicms                # 之后用 systemctl 管理，不要用 ./server.sh start
```

---

## 10. 登录与权限

`.env` 里配置管理员账号，**两者都设置才会启用认证**（留空则 API 完全开放，方便开发）：

```ini
FEICMS_ADMIN_USER=admin
FEICMS_ADMIN_PASSWORD=改成强密码
FEICMS_SECRET=可选：token 签名密钥（留空则由 user+password 派生）
FEICMS_TOKEN_TTL=86400
FEICMS_PUBLIC_PROVIDE=true
```

启用后：

- 所有 `/api/*` 与 `/images/*` 需要登录，未登录返回 `401`；
- 管理面板打开时显示登录页，登录成功后写入 HttpOnly cookie，海报 `<img>` 也能正常加载；
- 三种认证方式都支持：

```bash
# 1) 换 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' | jq -r .token)
curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN"

# 2) Basic 认证（最省事）
curl -s -u admin:密码 http://localhost:8000/api/sources
```

改密码：

```bash
uv run feicms passwd --user admin --password 你的密码   # 写回 .env，重启生效
uv run feicms passwd --user admin                       # 留空则随机生成
```

不想开终端的话：面板「配置 → 管理员账号」可以改用户名/密码（留空则随机生成，明文只显示一次）并生成一个 1 小时有效的 token 给 curl 用；写回 `.env` 后需要 `./server.sh restart` 才生效。

`/provide/vod`（给下游 CMS 采集的兼容接口）默认匿名开放；想保护它设 `FEICMS_PUBLIC_PROVIDE=false`。

> 密码明文保存在 `.env`，而 `.env` 已在 `.gitignore` 里，不会进仓库。

---

## 11. API Key 与 MCP（给 AI 客户端使用）

管理员账号适合“人”登录；脚本和 AI 客户端更适合用 **API key**：长期有效、可分权限、可单独吊销。

```bash
uv run feicms apikey create --name ai --scopes read          # 只读 key
uv run feicms apikey create --name automation --scopes write # 可采集 / 改源
uv run feicms apikey list
uv run feicms apikey revoke 3
uv run feicms apikey update 3 --enabled --scopes read
```

数据库里只存 **sha256 哈希**，明文只在创建时打印一次，丢了只能重新创建。管理面板左侧的 **「AI / MCP 接入」** 页面可以创建 / 停用 / 删除 key、复制客户端配置、实时列出 MCP 工具，还有一个**工具试跑**面板：选工具、填 JSON 参数，直接调 `POST /mcp`（用当前登录身份），返回结果和 AI 客户端看到的完全一致，包括播放源 URL。

使用：

```bash
curl -s http://localhost:8000/api/stats -H "X-API-Key: fk_..."
curl -s http://localhost:8000/api/videos?q=流浪地球 -H "Authorization: Bearer fk_..."
```

权限模型：

| scope | 能做什么 |
| --- | --- |
| `read` | 查询：sources / videos / stats / categories / images |
| `write` | 以上全部 + 采集、健康检查、增删改资源站、下载海报、管理 key |

`POST/PUT/PATCH/DELETE` 对只读 key 返回 `403`；只有管理员账号（`FEICMS_ADMIN_USER`）可以管理 key。

### MCP server（只读 RAG 库）

MCP 这一层**只读**：它把采集好的影片库暴露成「检索 + 文档 + 分块」，给 AI 客户端做 RAG / 问答用。采集、健康检查、海报下载、增删资源站这些维护动作不在 MCP 里，只留在管理面板、CLI 和 `/api/*`（需要管理员账号或 `write` key）。

同一个服务提供两种 MCP 传输，工具集相同：

```bash
# 1) HTTP（streamable）：任何支持远程 MCP 的客户端
curl -s -X POST http://localhost:8000/mcp \
  -H "X-API-Key: fk_..." -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 2) stdio：Claude Desktop / OpenCode 之类
uv run feicms mcp
uv run feicms mcp-config            # 打印可粘贴的客户端配置
```

客户端配置示例：

```json
{
  "mcpServers": {
    "feicms": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "X-API-Key": "fk_..." }
    }
  }
}
```

暴露的工具（全部只读）：

| 工具 | 说明 | 主要参数 |
| --- | --- | --- |
| `search` | 关键词检索（标题 / 简介 / 演员 / 导演 / 分类 / 地区 / 年份 全文匹配），返回 id + 简介摘要 + 播放源数量 / 总集数 / `first_url` | `query`, `type_name`, `year`, `area`, `sort`, `limit`, `offset` |
| `get_document` | 单条影片作为纯文本文档（标题、分类、年份、导演、主演、简介、备注 + **播放源 URL**），适合喂模型或 embedding | `video_id`, `max_episodes`（每个线路最多列出几集，默认 60）, `include_urls` |
| `get_plays` | 某条影片的全部播放源，结构化返回：站点名、线路名、集数、每集 `name` + `url`（单集和多集都支持） | `video_id`, `limit` |
| `browse` | 按分类 / 年份 / 地区分页浏览，没有关键词时用这个 | `type_name`, `year`, `area`, `sort`, `limit`, `offset` |
| `related` | 标题相关的条目（同系列 / 同名），扩展检索上下文 | `video_id`, `limit` |
| `read_chunks` | 批量导出文档分块，灌进向量库 / RAG 索引（默认只带线路名 + 集数摘要，`include_urls=true` 带每集 URL） | `type_name`, `limit`（≤500）, `offset`, `include_urls`, `max_episodes` |
| `list_types` | 分类及每个分类的条数 | - |
| `get_stats` | 库规模、分类分布、最后更新时间 | - |

另外有三个 MCP resources：`feicms://stats`、`feicms://types`、`feicms://instructions`（使用说明）。

把整库灌进向量库（每批最多 500 条）：

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "X-API-Key: fk_..." -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_chunks","arguments":{"limit":500,"offset":0}}}'
```

stdio 模式直接读同一个 `.env` 和数据库，属于本地可信进程；远程访问请用 HTTP 端点 + API key。

---

## 12. 测试

```bash
uv sync --extra dev
uv run pytest -q
```

测试覆盖：URL/响应归一化、采集增量逻辑、订阅解析、图片分桶与状态机、API 端点。全部测试不联网（用注入的假 fetcher）。

---

## 13. Docker

```bash
docker build -t feicms .
docker run -p 8000:8000 -v ./data:/app/data \
  -e FEICMS_ADMIN_USER=admin -e FEICMS_ADMIN_PASSWORD=改成强密码 \
  feicms
```

容器内需要自己的 `sources.yaml`（挂载或 `FEICMS_SOURCES` 指向），或用 `feicms subscribe --sync` 从 feed 导入。

---

## 14. 已知限制

- 部分站点忽略 `pg` / `page` 参数（翻页返回相同内容），部分站点强制 `limit=20`；
- 上游站点域名变动频繁，健康检查会自动禁用失效站点，但不会自动替换；
- 网络环境（代理、DNS、CDN 屏蔽）会直接影响采集成功率；
- 标题归一化去重是启发式的，跨站点同名不同影片可能合并，需要人工校对。

## 15. Logo 与图标

品牌色 `#4f7cff`，图形含义是「多个资源站 → 汇聚 → 一个可播放的库」。

| 文件 | 用途 |
|---|---|
| `assets/logo-mark.svg` | 图标（渐变圆角方块），浅色主题 / favicon 使用 |
| `assets/logo-mark-dark.svg` | 深色主题图标（青蓝渐变） |
| `assets/logo-mark-synthwave.svg` | Synthwave 主题图标（粉橙→紫渐变） |
| `assets/logo-mark-mono.svg` | 单色图标（透明背景），文档/浅色背景使用 |
| `assets/logo.svg` / `assets/logo-dark.svg` | 图标 + 文字（浅色 / 深色背景） |
| `assets/logo-banner.svg` / `.png` | README 头图（1200×400） |
| `assets/icon-{16,32,48,128,180,192,256,512}.png` | 各尺寸位图（`-dark` / `-synthwave` 后缀为对应主题版本） |
| `web/public/favicon*.svg` / `favicon*.png` | 浏览器图标，面板切主题时自动换 |

管理面板的配色全部走 daisyUI 主题变量（`--color-base-100/200/300`、`--color-primary`、`--color-error` 等），
切到 dark / synthwave 时卡片、代码块、缩略图、导航高亮都会同步变化；主题选择保存在 `localStorage`
（`feicms_theme`），首次访问跟随系统 `prefers-color-scheme`。

重新生成位图（改完 SVG 后）：

```bash
uv sync --extra icons
uv run python scripts/make_icons.py
```

## 16. 合规声明

本项目只是**采集与聚合的技术实现**，不包含任何站点列表或内容。使用时请自行确认：上游站点是否授权数据抓取、`robots.txt` 与服务条款是否允许、以及下游分发是否合规。由此产生的法律责任由使用者承担。

## License

MIT
