#!/usr/bin/env bash
# feicms 服务端管理脚本
#
#   ./server.sh add <name> <url> [--format auto] [--pages 5] [--tags 电影]
#   ./server.sh adult [--on|--off|--refresh|--show N]   前台成人内容过滤
#   ./server.sh apikey create|list|revoke|update        API key 管理
#   ./server.sh build          构建前端 (pnpm/npm)
#   ./server.sh collect [--full] [--pages N]
#   ./server.sh compact-raw    清理冗余 raw play_url 并 VACUUM 收缩数据库
#   ./server.sh db-size        数据库体积体检：文件大小 / plays 压缩率 / 重复索引
#   ./server.sh dev            前台启动，带 --reload（开发用）
#   ./server.sh doctor         体检：服务状态 + 库统计 + 待补扫 + 站点健康 + 图片
#   ./server.sh embed          构建语义搜索向量索引（embeddings）
#   ./server.sh export [--out backup.jsonl] [--limit N] [--q 关键词]
#   ./server.sh full [--wait] [--fresh]  后台全量扫描（每站真实页数，默认断点续传, 先做健康检查）
#   ./server.sh full --no-health       跳过先置的健康检查（更快，可能盲扫到坏源）
#   ./server.sh full-status    实时进度 + 断点（读 data/collect_status.json）
#   ./server.sh full-stop      停止后台全量扫描（断点保留）
#   ./server.sh health         探测所有站点
#   ./server.sh images scan|download|stats|repair|prune
#   ./server.sh init           init-db + seed
#   ./server.sh logs [-f]     查看日志（-f 持续跟踪）
#   ./server.sh maintain [--apply]   一键维护：repair-plays + 去重预览 + reindex + optimize
#                                    （加 --apply 才真正合并重复与 prune，这两个会删数据）
#   ./server.sh mcp            以 stdio 方式跑 MCP server（给 AI 客户端）
#   ./server.sh mcp-config [--url http://localhost:8000/mcp]
#   ./server.sh merge-duplicates [--dry-run]   合并同标题的重复影片
#   ./server.sh optimize       一键瘦身：删重复索引 + 去重源文本 + 清冗余 play_url + 压缩 plays + VACUUM
#   ./server.sh pack-plays     把播放源 JSON 压缩存储（zlib，通常缩小到 1/6）
#   ./server.sh passwd [--user admin] [--password xxx]
#   ./server.sh prune          删除没有播放源的记录 / 孤儿影片
#   ./server.sh reindex        重建标题搜索索引（FTS5，一次性扫全库）
#   ./server.sh refresh [--days N] [--limit N] [--name 站点] [--dry-run]
#                        后台定向补扫：按 vod_id 抓详情，刷新增量采集扫不到的老记录
#   ./server.sh refresh-status   补扫进度（读 data/refresh.log）
#   ./server.sh refresh-stop     停止补扫
#   ./server.sh refresh-stats    还有多少条待补扫
#   ./server.sh repair-plays   用当前解析规则重建 plays（不联网）
#   ./server.sh restart        重启
#   ./server.sh resume [--clear] [--detail]  查看 / 清除断点续传记录
#   ./server.sh seed [--file sources.yaml]   导入站点列表（幂等）
#   ./server.sh sources [--enabled-only] [--limit N]   列出站点与健康状态
#   ./server.sh start          启动（后台，日志写到 data/server.log）
#   ./server.sh start --user admin --password xxx   先写入 .env 的账号再启动
#   ./server.sh start --host 0.0.0.0 --port 8000    临时覆盖监听地址（默认 0.0.0.0）
#   ./server.sh status         查看运行状态 / 健康 / 是否开启登录
#   ./server.sh stop           停止
#   ./server.sh subscribe [--add URL|--remove URL|--sync|--url URL]
#   ./server.sh systemd        打印 systemd 服务配置（开机自启）
#   ./server.sh token          打印一个 API token（curl 用）
#   ./server.sh url            打印本机 + 局域网访问地址
set -euo pipefail
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/data/server.pid"
LOG_FILE="$ROOT/data/server.log"
COLLECT_LOG="$ROOT/data/collect.log"
COLLECT_PID="$ROOT/data/collect.pid"
COLLECT_LOCK="$ROOT/data/collect.lock"
COLLECT_STATUS="$ROOT/data/collect_status.json"
COLLECT_RESUME="$ROOT/data/collect_resume.json"
REFRESH_LOG="$ROOT/data/refresh.log"
REFRESH_PID="$ROOT/data/refresh.pid"

load_env() {
  if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
  fi
}

run_py() {
  if command -v uv >/dev/null 2>&1; then
    uv run "$@"
  elif [ -x "$ROOT/.venv/bin/$1" ]; then
    "$ROOT/.venv/bin/$1" "${@:2}"
  else
    echo "未找到 uv，也没有 .venv。先运行 ./install.sh"
    exit 1
  fi
}

port_pid() {
  load_env
  local port="${FEICMS_PORT:-8000}"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" 2>/dev/null | head -1
  fi
}

lan_ips() {
  if command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null
  elif command -v ip >/dev/null 2>&1; then
    ip -4 -o addr scope global 2>/dev/null | awk '{print $4}'
  fi
}

# 默认 FEICMS_HOST=0.0.0.0，所以局域网/其它机器也能访问；打印本机 + 局域网地址
print_urls() {
  local host="${FEICMS_HOST:-0.0.0.0}" port="${FEICMS_PORT:-8000}" ip
  echo "前台:     http://127.0.0.1:$port/  (影视库浏览)"
  echo "后台:     http://127.0.0.1:$port/admin  (管理面板)"
  if [ "$host" = "0.0.0.0" ] || [ "$host" = "::" ] || [ -z "$host" ]; then
    for ip in $(lan_ips); do
      echo "局域网:   http://$ip:$port/"
    done
  fi
}

is_running() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    rm -f "$PID_FILE"
  fi
  return 1
}

cmd_start() {
  # 可选：./server.sh start --user admin --password xxx   -> 先写 .env 再启动
  #       ./server.sh start --host 0.0.0.0 --port 8000    -> 临时覆盖监听地址
  local user="" password="" host_override="" port_override=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --user) user="${2:-}"; shift 2 ;;
      --user=*) user="${1#--user=}"; shift ;;
      --password) password="${2:-}"; shift 2 ;;
      --password=*) password="${1#--password=}"; shift ;;
      --host) host_override="${2:-}"; shift 2 ;;
      --host=*) host_override="${1#--host=}"; shift ;;
      --port) port_override="${2:-}"; shift 2 ;;
      --port=*) port_override="${1#--port=}"; shift ;;
      *) shift ;;
    esac
  done

  if [ -n "$user" ] || [ -n "$password" ]; then
    if [ -z "$password" ]; then
      run_py feicms passwd --user "${user:-admin}"
    else
      run_py feicms passwd --user "${user:-admin}" --password "$password"
    fi
    if is_running; then
      cmd_stop
    fi
  fi

  if is_running; then
    echo "服务已在运行 (pid $(cat "$PID_FILE"))"
    return 0
  fi
  load_env
  if [ ! -d "$ROOT/.venv" ] && ! command -v uv >/dev/null 2>&1; then
    echo "缺少依赖环境，先运行 ./install.sh"
    exit 1
  fi
  mkdir -p "$ROOT/data"
  host="${host_override:-${FEICMS_HOST:-0.0.0.0}}"
  port="${port_override:-${FEICMS_PORT:-8000}}"

  if command -v uv >/dev/null 2>&1; then
    CMD=(uv run uvicorn feicms.api:app --host "$host" --port "$port")
  else
    CMD=("$ROOT/.venv/bin/uvicorn" feicms.api:app --host "$host" --port "$port")
  fi

  nohup "${CMD[@]}" > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "启动中 (pid $!) -> $host:$port"

  for _ in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      echo "服务就绪"
      cmd_status
      return 0
    fi
    sleep 1
  done
  echo "启动超时，查看日志: ./server.sh logs"
  return 1
}

cmd_stop() {
  if is_running; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "已停止 (pid $pid)"
  else
    pid="$(port_pid || true)"
    if [ -n "${pid:-}" ]; then
      kill "$pid" 2>/dev/null || true
      echo "已停止占用端口的进程 (pid $pid)"
    else
      echo "服务未运行"
    fi
  fi
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  load_env
  port="${FEICMS_PORT:-8000}"
  if is_running; then
    pid="$(cat "$PID_FILE")"
    uptime="$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    echo "运行中: pid $pid, 运行时长 ${uptime:-未知}"
  else
    pid="$(port_pid || true)"
    if [ -n "${pid:-}" ]; then
      echo "端口 $port 被 pid $pid 占用（不是本脚本启动的）"
    else
      echo "未运行"
      return 1
    fi
  fi
  echo "健康: $(curl -s "http://127.0.0.1:$port/health" || echo '无法访问')"
  echo "认证: $(curl -s "http://127.0.0.1:$port/api/auth/status" || echo '无法访问')"
  echo "监听: ${FEICMS_HOST:-0.0.0.0}:$port"
  print_urls
  echo "日志: $LOG_FILE"
}

cmd_url() {
  load_env
  print_urls
}

cmd_systemd() {
  load_env
  py="$ROOT/.venv/bin/uvicorn"
  [ -x "$py" ] || py="$ROOT/.venv/bin/python"
  cat <<EOF
# /etc/systemd/system/feicms.service
[Unit]
Description=feicms 资源站聚合服务
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$py feicms.api:app --host ${FEICMS_HOST:-0.0.0.0} --port ${FEICMS_PORT:-8000}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  echo
  echo "保存后执行: sudo systemctl daemon-reload && sudo systemctl enable --now feicms"
}

cmd_logs() {
  if [ ! -f "$LOG_FILE" ]; then
    echo "没有日志文件（服务还没启动过）"
    return 0
  fi
  if [ "${1:-}" = "-f" ] || [ "${1:-}" = "--follow" ]; then
    tail -f "$LOG_FILE"
  else
    tail -n "${1:-40}" "$LOG_FILE"
  fi
}

cmd_dev() {
  load_env
  host="${FEICMS_HOST:-127.0.0.1}"
  port="${FEICMS_PORT:-8000}"
  if command -v uv >/dev/null 2>&1; then
    uv run uvicorn feicms.api:app --host "$host" --port "$port" --reload
  else
    "$ROOT/.venv/bin/uvicorn" feicms.api:app --host "$host" --port "$port" --reload
  fi
}

cmd_build() {
  if [ -d web/node_modules ] && [ -x web/node_modules/.bin/vite ]; then
    (cd web && pnpm run build)
  elif command -v pnpm >/dev/null 2>&1; then
    (cd web && pnpm install --ignore-scripts && pnpm run build)
  elif command -v npm >/dev/null 2>&1; then
    (cd web && npm install && npm run build)
  else
    echo "未找到 pnpm/npm"
    exit 1
  fi
}

# 全量扫描：把每个站点真实报告的页数全部遍历一遍，后台跑，不用开网页。
#   ./server.sh full                          所有启用的站点
#   ./server.sh full --name "360资源站"       只扫一个站点
#   ./server.sh full --delay 0.5 --concurrency 8
py_cmd() {
  if command -v uv >/dev/null 2>&1; then
    echo "uv run feicms"
  elif [ -x "$ROOT/.venv/bin/feicms" ]; then
    echo "$ROOT/.venv/bin/feicms"
  else
    echo ""
  fi
}

cmd_full() {
  load_env
  local wait=0 fresh=0 args="" a
  for a in "$@"; do
    case "$a" in
      --wait) wait=1; continue ;;
      --fresh|--restart) fresh=1; continue ;;
      --resume) continue ;;   # 默认就是续传，兼容显式写出来的用法
      --no-health|--no-health-first) args="$args $(printf '%q' "--no-health-first")"; continue ;;
    esac
    args="$args $(printf '%q' "$a")"
  done

  if [ "$fresh" = 1 ]; then
    args="$args --no-resume"
  fi

  if [ "$wait" = 1 ]; then
    while lock_held_by_live; do
      echo "等待当前采集轮结束：$(cat "$COLLECT_LOCK")"
      sleep 30
    done
  elif lock_held_by_live; then
    echo "另一轮采集正在进行中：$(cat "$COLLECT_LOCK")"
    echo "看进度: ./server.sh full-status"
    echo "想让后台扫描独占采集：.env 里设 FEICMS_AUTO_COLLECT=false 后 ./server.sh restart"
    echo "或者用 ./server.sh full --wait，它会自动排队"
    return 0
  elif [ -f "$COLLECT_LOCK" ]; then
    echo "发现上次扫描残留的锁（那个进程已经不在），本轮直接接管"
  fi

  if [ "$fresh" = 1 ]; then
    echo "已指定 --fresh：忽略断点，从第 1 页重新开始"
  elif [ -f "$COLLECT_RESUME" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$COLLECT_RESUME" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
sources = d.get("sources") or {}
done = [e for e in sources.values() if isinstance(e, dict) and e.get("status") == "done"]
if d.get("done"):
    print("上次全量已跑完，本轮重新开始")
elif sources:
    print(f"断点续传：上次记录 {len(sources)} 个站点，其中 {len(done)} 个已跑完（跳过），其余从记录页继续")
PY
  fi

  local cmd
  cmd="$(py_cmd)"
  if [ -z "$cmd" ]; then
    echo "未找到 uv，也没有 .venv。先运行 ./install.sh"
    exit 1
  fi
  # a new sweep starts a fresh log; the live numbers live in $COLLECT_STATUS
  : > "$COLLECT_LOG"
  if command -v setsid >/dev/null 2>&1; then
    PYTHONUNBUFFERED=1 setsid nohup bash -c "$cmd collect --full$args" >> "$COLLECT_LOG" 2>&1 &
  else
    PYTHONUNBUFFERED=1 nohup bash -c "$cmd collect --full$args" >> "$COLLECT_LOG" 2>&1 &
  fi
  echo $! > "$COLLECT_PID"
  echo "全量扫描已启动 (pid $!)，日志: $COLLECT_LOG"
  echo "看进度: ./server.sh full-status   或 tail -f $COLLECT_LOG"
  echo "停止:   ./server.sh full-stop   （断点会保留，再跑 ./server.sh full 自动续传）"
}

# 采集锁是否还由一个活着的进程持有：崩溃/重启留下的锁不算，直接接管
lock_held_by_live() {
  [ -f "$COLLECT_LOCK" ] || return 1
  local pid
  pid="$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("pid", ""))
except Exception:
    print("")' "$COLLECT_LOCK" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    return 0   # 读不出持有者，保守认为还在跑
  fi
  kill -0 "$pid" 2>/dev/null
}

cmd_full_status() {
  load_env
  if [ -f "$COLLECT_PID" ]; then
    pid="$(cat "$COLLECT_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "扫描进程运行中 (pid $pid)"
    else
      echo "扫描进程已退出 (pid $pid)"
    fi
  else
    echo "没有记录到扫描进程"
  fi
  if [ -f "$COLLECT_LOCK" ]; then
    echo "锁: $(cat "$COLLECT_LOCK")"
  else
    echo "锁已释放（本轮已结束）"
  fi
  if [ -f "$COLLECT_STATUS" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$COLLECT_STATUS" <<'PY'
import json, sys

try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"状态文件读取失败: {exc}")
    raise SystemExit(0)

print(f"本轮状态: {'进行中' if d.get('running') else '已结束'}")
print(f"本轮开始: {d.get('started_at', '?')}  最近更新: {d.get('updated_at', '?')}")
print(f"进度: {d.get('done_sources', 0)}/{d.get('total_sources', 0)} 个站点完成，"
      f"共 {d.get('pages', 0)} 页，新增 {d.get('inserted', 0)}，更新 {d.get('updated', 0)}，"
      f"跳过 {d.get('skipped', 0)} 条重复")
sources = d.get("sources", [])
if sources:
    print("各站点（按已采集页数）：")
    for s in sources[:25]:
        total = s.get("pagecount") or "?"
        note = s.get("note") or ""
        print(f"  {s['name']}: {s.get('pages', 0)}/{total} 页  新增 {s.get('inserted', 0)} 更新 {s.get('updated', 0)} {note}")
    if len(sources) > 25:
        print(f"  ... 其余 {len(sources) - 25} 个站点见 {sys.argv[1]}")
PY
  elif [ -f "$COLLECT_LOG" ]; then
    echo "最近 20 行 ($COLLECT_LOG):"
    tail -20 "$COLLECT_LOG"
  fi
  if [ -f "$COLLECT_RESUME" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$COLLECT_RESUME" <<'PY'
import json, sys

try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
sources = {k: v for k, v in (d.get("sources") or {}).items() if isinstance(v, dict)}
if not sources:
    raise SystemExit(0)
done = [v for v in sources.values() if v.get("status") == "done"]
unfinished = [v for v in sources.values() if v.get("status") != "done"]
state = "已跑完" if d.get("done") else "未跑完（再跑 ./server.sh full 会续传）"
print(f"断点续传: {state}  记录 {len(sources)} 个站点，已完成 {len(done)}，未完成 {len(unfinished)}")
for v in sorted(unfinished, key=lambda e: str(e.get("name") or ""))[:10]:
    print(f"  ↻ {v.get('name')}: 从第 {v.get('next_page', '?')} 页继续"
          f"（已采 {v.get('pages', 0)} 页，共 {v.get('pagecount') or '?'}）")
if len(unfinished) > 10:
    print(f"  ... 其余 {len(unfinished) - 10} 个站点见 {sys.argv[1]}")
PY
  fi
}

cmd_full_stop() {
  local stopped=0
  if [ -f "$COLLECT_PID" ]; then
    pid="$(cat "$COLLECT_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      if command -v setsid >/dev/null 2>&1; then
        kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      else
        kill -TERM "$pid" 2>/dev/null
      fi
      echo "已停止 (pid $pid)"
      stopped=1
    else
      echo "扫描进程已退出"
    fi
    rm -f "$COLLECT_PID"
  fi
  if pkill -f "feicms collect --full" 2>/dev/null; then
    stopped=1
  fi
  if [ -f "$COLLECT_LOCK" ]; then
    holder="$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("pid", ""))
except Exception:
    print("")' "$COLLECT_LOCK" 2>/dev/null)"
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null && [ "$stopped" = 0 ]; then
      echo "没有后台扫描进程可停：锁由服务自己的自动采集持有 (pid $holder)"
      echo "想让后台扫描独占：.env 设 FEICMS_AUTO_COLLECT=false 后 ./server.sh restart"
      return 0
    fi
    rm -f "$COLLECT_LOCK"
    echo "锁已释放"
  fi
}

cmd_refresh() {
  load_env
  if [ -f "$REFRESH_PID" ] && kill -0 "$(cat "$REFRESH_PID")" 2>/dev/null; then
    echo "补扫已在运行 (pid $(cat "$REFRESH_PID"))，看进度: ./server.sh refresh-status"
    return 0
  fi
  if lock_held_by_live; then
    echo "另一轮采集正在进行：$(cat "$COLLECT_LOCK")"
    echo "补扫与采集共用采集锁，这一轮结束后再跑（后台扫描可用 ./server.sh full-stop 停掉）"
    return 0
  fi

  local cmd args="" a
  cmd="$(py_cmd)"
  if [ -z "$cmd" ]; then
    echo "未找到 uv，也没有 .venv。先运行 ./install.sh"
    exit 1
  fi
  for a in "$@"; do
    args="$args $(printf '%q' "$a")"
  done

  : > "$REFRESH_LOG"
  if command -v setsid >/dev/null 2>&1; then
    PYTHONUNBUFFERED=1 setsid nohup bash -c "$cmd refresh$args" >> "$REFRESH_LOG" 2>&1 &
  else
    PYTHONUNBUFFERED=1 nohup bash -c "$cmd refresh$args" >> "$REFRESH_LOG" 2>&1 &
  fi
  echo $! > "$REFRESH_PID"
  echo "定向补扫已启动 (pid $!)，日志: $REFRESH_LOG"
  echo "看进度: ./server.sh refresh-status   或 tail -f $REFRESH_LOG"
  echo "停止:   ./server.sh refresh-stop"
}

cmd_refresh_status() {
  load_env
  if [ -f "$REFRESH_PID" ]; then
    pid="$(cat "$REFRESH_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "补扫进程运行中 (pid $pid)"
    else
      echo "补扫进程已退出 (pid $pid)"
    fi
  else
    echo "没有记录到补扫进程"
  fi
  if [ -f "$REFRESH_LOG" ]; then
    echo "最近进度（$REFRESH_LOG）："
    tail -12 "$REFRESH_LOG"
  fi
  run_py feicms refresh-stats
}

cmd_refresh_stop() {
  if [ -f "$REFRESH_PID" ]; then
    pid="$(cat "$REFRESH_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      if command -v setsid >/dev/null 2>&1; then
        kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      else
        kill -TERM "$pid" 2>/dev/null
      fi
      echo "已停止补扫 (pid $pid)"
    else
      echo "补扫进程已退出"
    fi
    rm -f "$REFRESH_PID"
  fi
}

cmd_maintain() {
  load_env
  local apply=0 a
  for a in "$@"; do
    case "$a" in
      --apply|--yes) apply=1 ;;
    esac
  done

  echo "== 重新解析播放源结构（不联网） =="
  run_py feicms repair-plays

  echo
  echo "== 同标题重复影片预览 =="
  run_py feicms merge-duplicates --dry-run

  if [ "$apply" = 1 ]; then
    echo
    echo "== 合并重复影片 =="
    run_py feicms merge-duplicates
    echo
    echo "== 删除没有播放源的记录 =="
    run_py feicms prune
  else
    echo
    echo "（默认不删数据。要真正合并重复 + 清理无播放源记录：./server.sh maintain --apply）"
  fi

  echo
  echo "== 重建标题搜索索引 =="
  run_py feicms reindex

  echo
  echo "== 清理冗余 raw 播放字段 + 压缩播放源 + VACUUM =="
  cmd_optimize
}

cmd_optimize() {
  load_env
  if [ -f "$COLLECT_PID" ] && kill -0 "$(cat "$COLLECT_PID")" 2>/dev/null; then
    echo "后台全量扫描正在运行，VACUUM 可能拿不到锁。先 ./server.sh full-stop 再跑 optimize。"
  elif [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "服务正在运行（VACUUM 期间前台请求可能变慢，但数据安全）。"
  fi
  run_py feicms optimize
  echo
  run_py feicms db-size
}

cmd_doctor() {
  load_env
  echo "== 服务状态 =="
  cmd_status
  echo
  echo "== 数据库统计 =="
  run_py feicms stats
  echo
  echo "== 数据库体积 =="
  run_py feicms db-size
  echo
  echo "== 定向补扫待核对 =="
  run_py feicms refresh-stats
  echo
  echo "== 站点健康（前 15 个） =="
  run_py feicms sources --limit 15
  echo
  echo "== 海报图片 =="
  run_py feicms images stats
}

usage() {
  awk 'NR > 1 { if (substr($0, 1, 1) != "#") exit; print substr($0, 2) }' "$0"
}

command="${1:-help}"
shift || true

case "$command" in
  start) cmd_start "$@" ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  url) cmd_url ;;
  logs) cmd_logs "$@" ;;
  dev) cmd_dev ;;
  build) cmd_build ;;
  init) run_py feicms init-db && run_py feicms seed ;;
  collect) run_py feicms collect "$@" ;;
  full) cmd_full "$@" ;;
  full-status) cmd_full_status ;;
  full-stop) cmd_full_stop ;;
  resume) run_py feicms resume "$@" ;;
  refresh) cmd_refresh "$@" ;;
  refresh-status) cmd_refresh_status ;;
  refresh-stop) cmd_refresh_stop ;;
  refresh-stats) run_py feicms refresh-stats ;;
  health) run_py feicms health ;;
  stats) run_py feicms stats ;;
  doctor) cmd_doctor ;;
  maintain) cmd_maintain "$@" ;;
  sources) run_py feicms sources "$@" ;;
  add) run_py feicms add "$@" ;;
  seed) run_py feicms seed "$@" ;;
  subscribe) run_py feicms subscribe "$@" ;;
  reindex) run_py feicms reindex ;;
  embed) run_py feicms embed "$@" ;;
  merge-duplicates) run_py feicms merge-duplicates "$@" ;;
  prune) run_py feicms prune ;;
  repair-plays) run_py feicms repair-plays ;;
  compact-raw) run_py feicms compact-raw ;;
  pack-plays) run_py feicms pack-plays "$@" ;;
  optimize) cmd_optimize ;;
  db-size) run_py feicms db-size ;;
  adult) run_py feicms adult "$@" ;;
  export) run_py feicms export "$@" ;;
  apikey) run_py feicms apikey "$@" ;;
  mcp) run_py feicms mcp ;;
  mcp-config) run_py feicms mcp-config "$@" ;;
  images) run_py feicms images "$@" ;;
  passwd) run_py feicms passwd "$@" ;;
  token) run_py feicms token ;;
  systemd) cmd_systemd ;;
  help|-h|--help) usage ;;
  *) echo "未知命令: $command"; usage; exit 1 ;;
esac
