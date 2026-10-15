#!/usr/bin/env bash
# feicms 服务端管理脚本
#
#   ./server.sh start          启动（后台，日志写到 data/server.log）
#   ./server.sh start --user admin --password xxx   先写入 .env 的账号再启动
#   ./server.sh start --host 0.0.0.0 --port 8000    临时覆盖监听地址（默认 0.0.0.0）
#   ./server.sh url            打印本机 + 局域网访问地址
#   ./server.sh stop           停止
#   ./server.sh restart        重启
#   ./server.sh status         查看运行状态 / 健康 / 是否开启登录
#   ./server.sh logs [-f]     查看日志（-f 持续跟踪）
#   ./server.sh dev            前台启动，带 --reload（开发用）
#   ./server.sh build          构建前端 (pnpm/npm)
#   ./server.sh init           init-db + seed
#   ./server.sh collect [--full] [--pages N]
#   ./server.sh full           后台全量扫描（每站真实页数），日志 data/collect.log
#   ./server.sh full-status    全量扫描进度 + 最近日志
#   ./server.sh full-stop      停止后台全量扫描
#   ./server.sh health         探测所有站点
#   ./server.sh images scan|download|stats|repair|prune
#   ./server.sh passwd [--user admin] [--password xxx]
#   ./server.sh token          打印一个 API token（curl 用）
#   ./server.sh systemd        打印 systemd 服务配置（开机自启）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/data/server.pid"
LOG_FILE="$ROOT/data/server.log"
COLLECT_LOG="$ROOT/data/collect.log"
COLLECT_PID="$ROOT/data/collect.pid"
COLLECT_LOCK="$ROOT/data/collect.lock"
COLLECT_STATUS="$ROOT/data/collect_status.json"

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
  echo "本机:     http://127.0.0.1:$port/"
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
  if command -v pnpm >/dev/null 2>&1; then
    (cd web && pnpm install && pnpm run build)
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
  if [ -f "$COLLECT_LOCK" ]; then
    echo "全量扫描已在进行中：$(cat "$COLLECT_LOCK")"
    echo "看进度: ./server.sh full-status   或 tail -f $COLLECT_LOG"
    return 0
  fi
  local cmd
  cmd="$(py_cmd)"
  if [ -z "$cmd" ]; then
    echo "未找到 uv，也没有 .venv。先运行 ./install.sh"
    exit 1
  fi
  local args="" a
  for a in "$@"; do
    args="$args $(printf '%q' "$a")"
  done
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
  echo "停止:   ./server.sh full-stop"
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
}

cmd_full_stop() {
  if [ -f "$COLLECT_PID" ]; then
    pid="$(cat "$COLLECT_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      if command -v setsid >/dev/null 2>&1; then
        kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      else
        kill -TERM "$pid" 2>/dev/null
      fi
      echo "已停止 (pid $pid)"
    else
      echo "扫描进程已退出"
    fi
    rm -f "$COLLECT_PID"
  fi
  pkill -f "feicms collect --full" 2>/dev/null || true
  rm -f "$COLLECT_LOCK"
  echo "锁已释放"
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
  health) run_py feicms health ;;
  stats) run_py feicms stats ;;
  images) run_py feicms images "$@" ;;
  passwd) run_py feicms passwd "$@" ;;
  token) run_py feicms token ;;
  systemd) cmd_systemd ;;
  help|-h|--help) usage ;;
  *) echo "未知命令: $command"; usage; exit 1 ;;
esac
