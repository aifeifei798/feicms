# feicms

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
| 管理面板 | React + Vite + Tailwind + DaisyUI：站点 CRUD、采集、健康、影片搜索、图片库、任务历史、配置 |

技术栈：Python 3.10+ / FastAPI / SQLAlchemy 2.0 (async) / `curl_cffi`（浏览器 TLS 指纹） / `lxml` / typer + rich；前端 React 18 + Vite 5 + Tailwind 4 + DaisyUI 5。

---

## 2. 快速开始

```bash
# 后端
uv sync --extra dev                 # 或 pip install -r requirements.txt
cp .env.example .env
cp sources.example.yaml sources.yaml   # 仓库不带真实站点列表，见第 3 节
uv run feicms init-db
uv run feicms seed                  # 从 sources.yaml 导入
uv run feicms health                # 探测站点是否可用
uv run feicms collect               # 采集一次
uv run feicms serve                 # API + 面板
```

```bash
# 前端（管理面板）
cd web && pnpm install && pnpm run build     # 产物 web/dist，由 FastAPI 挂载
```

打开：

- `http://localhost:8000/` — 管理面板
- `http://localhost:8000/docs` — OpenAPI 交互式文档
- `http://localhost:8000/api/stats` — 总览统计

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

---

## 4. 采集与页数语义

| 参数 | 含义 |
|---|---|
| `--pages N` | 每个站点抓 N 页 |
| `--pages 0` / `--full` | 全量扫描，上限 `FEICMS_FULL_PAGE_CAP`（默认 200 页），不做提前停止 |
| 省略 | 使用 `FEICMS_PAGES`（默认 5），或站点自己的 `max_pages` |

采集结果会打印 `采集页/总页 new= updated= skipped= total= 耗时`，并写入 `collect_jobs` 表用于审计。

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
feicms export [--out backup.jsonl]

feicms images scan                 # 登记所有 pic 字段（不联网）
feicms images download [--limit --concurrency --retry-failed]
feicms images stats                # 状态分布 / 磁盘占用 / 分桶数 / Top 域名
feicms images prune [--delete-files/--keep-files]
feicms images repair               # 校对数据库状态与磁盘文件
feicms images layout [--top 12]    # 打印分桶结构

feicms serve [--host --port]
```

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

---

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务健康 |
| GET | `/api/sources` | 站点列表（`?enabled=&health=&q=`） |
| POST | `/api/sources` | 新增站点 |
| GET / PATCH / DELETE | `/api/sources/{id}` | 查看 / 修改 / 删除 |
| POST | `/api/sources/{id}/enable` | 启用或禁用 |
| POST | `/api/sources/{id}/collect` | 立即采集该站点（`?pages=0` 全量） |
| POST | `/api/collect` | 采集所有启用站点 `{pages, source_ids}` |
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
| GET | `/api/images` | 图片列表（`?status=&host=&limit=`） |
| GET | `/api/images/{id}` | 单条图片记录 |
| POST | `/api/images/prune` | 删除无引用图片 |
| POST | `/api/images/repair` | 校对索引与磁盘 |
| GET | `/images/{id}` | 返回本地图片文件，未下载则 302 到原 URL |
| POST | `/api/prune` | 清理无播放源记录 / 孤儿影片 |
| GET | `/provide/vod` | 兼容资源站格式，可被下游 CMS 直接采集 |
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
| `FEICMS_FULL_PAGE_CAP` | 200 | 全量扫描的页数上限 |
| `FEICMS_CONCURRENCY` | 6 | 站点并发 |
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
| `FEICMS_IMAGE_MAX_ATTEMPTS` | 3 | 单图片最大重试 |
| `FEICMS_IMAGE_AUTO` | false | 后台自动下载图片（默认关闭，手动下载） |
| `FEICMS_IMAGE_INTERVAL` | 30 | 自动下载间隔（分钟） |
| `FEICMS_HOST` / `FEICMS_PORT` | `0.0.0.0` / `8000` | 服务监听 |

---

## 9. 测试

```bash
uv sync --extra dev
uv run pytest -q
```

测试覆盖：URL/响应归一化、采集增量逻辑、订阅解析、图片分桶与状态机、API 端点。全部测试不联网（用注入的假 fetcher）。

---

## 10. Docker

```bash
docker build -t feicms .
docker run -p 8000:8000 -v ./data:/app/data feicms
```

容器内需要自己的 `sources.yaml`（挂载或 `FEICMS_SOURCES` 指向），或用 `feicms subscribe --sync` 从 feed 导入。

---

## 11. 已知限制

- 部分站点忽略 `pg` / `page` 参数（翻页返回相同内容），部分站点强制 `limit=20`；
- 上游站点域名变动频繁，健康检查会自动禁用失效站点，但不会自动替换；
- 网络环境（代理、DNS、CDN 屏蔽）会直接影响采集成功率；
- 标题归一化去重是启发式的，跨站点同名不同影片可能合并，需要人工校对。

## 12. 合规声明

本项目只是**采集与聚合的技术实现**，不包含任何站点列表或内容。使用时请自行确认：上游站点是否授权数据抓取、`robots.txt` 与服务条款是否允许、以及下游分发是否合规。由此产生的法律责任由使用者承担。

## License

MIT
