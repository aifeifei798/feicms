#!/usr/bin/env bash
# feicms 服务端管理脚本
#
#   ./server.sh start          启动（后台，日志写到 data/server.log）
#   ./server.sh stop           停止
#   ./server.sh restart        重启
#   ./server.sh status         查看运行状态 / 健康 / 是否开启登录
#   ./server.sh logs [-f]     查看日志（-f 持续跟踪）
#   ./server.sh dev            前台启动，带 --reload（开发用）
#   ./server.sh build          构建前端 (pnpm/npm)
#   ./server.sh init           init-db + seed
#   ./server.sh collect [--full] [--pages N]
#   ./server.sh health         探测所有站点
#   ./server.sh images scan|download|stats|repair|prune
#   ./server.sh passwd [--user admin] [--password xxx]
#   ./server.sh token          打印一个 API token（curl 用）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/data/server.pid"
LOG_FILE="$ROOT/data/server.log"

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
  host="${FEICMS_HOST:-0.0.0.0}"
  port="${FEICMS_PORT:-8000}"

  if command -v uv >/dev/null 2>&1; then
    CMD=(uv run uvicorn feicms.api:app --host "$host" --port "$port")
  else
    CMD=("$ROOT/.venv/bin/uvicorn" feicms.api:app --host "$host" --port "$port")
  fi

  nohup "${CMD[@]}" > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "启动中 (pid $!) -> http://127.0.0.1:$port"

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
  echo "日志: $LOG_FILE"
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

usage() {
  sed -n '2,16p' "$0"
}

command="${1:-help}"
shift || true

case "$command" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs "$@" ;;
  dev) cmd_dev ;;
  build) cmd_build ;;
  init) run_py feicms init-db && run_py feicms seed ;;
  collect) run_py feicms collect "$@" ;;
  health) run_py feicms health ;;
  stats) run_py feicms stats ;;
  images) run_py feicms images "$@" ;;
  passwd) run_py feicms passwd "$@" ;;
  token) run_py feicms token ;;
  help|-h|--help) usage ;;
  *) echo "未知命令: $command"; usage; exit 1 ;;
esac
