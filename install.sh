#!/usr/bin/env bash
# feicms 一键安装：
#   1) 创建 Python 虚拟环境并安装依赖（优先用 uv，没有则用 venv + pip）
#   2) 构建前端（pnpm，没有则 npm）
#   3) 生成 .env（含管理员用户名/密码）
#   4) 初始化数据库
#   5) 启动服务（后台，监听 0.0.0.0）
#
# 用法:
#   ./install.sh [--no-frontend] [--mysql] [--no-dev] [--force-env] [--no-start]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_WEB=true
WITH_DEV=true
WITH_MYSQL=false
FORCE_ENV=false
START_SERVICE=true
SYSTEMD=false

usage() {
  cat <<'EOF'
用法: ./install.sh [选项]

  --no-frontend   跳过前端构建（只装后端）
  --no-dev        不装 pytest 等开发依赖
  --mysql         安装 MySQL 驱动 (aiomysql)
  --force-env     覆盖已有的 .env（会重新生成管理员密码）
  --no-start      安装后不自动启动服务
  --systemd       注册 systemd 服务并开机自启（需要 root/sudo）
  -h, --help      显示帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-frontend) WITH_WEB=false ;;
    --no-dev) WITH_DEV=false ;;
    --mysql) WITH_MYSQL=true ;;
    --force-env) FORCE_ENV=true ;;
    --no-start) START_SERVICE=false ;;
    --systemd) SYSTEMD=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1"; usage; exit 1 ;;
  esac
  shift
done

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- python
step "检查 Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10+"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 版本过低（需要 3.10+），当前: $(python3 --version)"
  exit 1
fi
echo "python3 -> $(python3 --version)"

HAS_UV=false
if command -v uv >/dev/null 2>&1; then
  HAS_UV=true
  step "安装依赖 (uv)"
  ARGS=()
  [ "$WITH_DEV" = true ] && ARGS+=(--extra dev)
  [ "$WITH_MYSQL" = true ] && ARGS+=(--extra mysql)
  uv sync ${ARGS[@]+"${ARGS[@]}"}
else
  step "创建虚拟环境并安装依赖 (venv + pip)"
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
  [ "$WITH_DEV" = true ] && .venv/bin/pip install pytest pytest-asyncio
  [ "$WITH_MYSQL" = true ] && .venv/bin/pip install aiomysql
fi

run_py() {
  if [ "$HAS_UV" = true ]; then
    uv run "$@"
  else
    .venv/bin/"$@"
  fi
}

# ---------------------------------------------------------------- frontend
if [ "$WITH_WEB" = true ]; then
  step "构建前端 (DaisyUI 管理面板)"
  if command -v pnpm >/dev/null 2>&1; then
    (cd web && pnpm install && pnpm run build)
  elif command -v npm >/dev/null 2>&1; then
    (cd web && npm install && npm run build)
  else
    echo "未找到 pnpm/npm，跳过前端构建。后端仍可用 /docs 与 API。"
  fi
fi

# ---------------------------------------------------------------- .env
step "配置 .env"
if [ -f .env ] && [ "$FORCE_ENV" = false ]; then
  echo ".env 已存在，保留现有配置"
else
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "已从 .env.example 生成 .env"
  else
    touch .env
  fi
fi

if ! grep -q '^FEICMS_ADMIN_USER=' .env || grep -q '^FEICMS_ADMIN_USER=$' .env; then
  echo "FEICMS_ADMIN_USER 未设置，默认写入 admin"
fi

if grep -q '^FEICMS_ADMIN_PASSWORD=$' .env || ! grep -q '^FEICMS_ADMIN_PASSWORD=' .env; then
  PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  USER="$(grep -m1 '^FEICMS_ADMIN_USER=' .env | cut -d= -f2)"
  [ -z "$USER" ] && USER=admin
  python3 - "$USER" "$PASSWORD" <<'PY'
import sys, pathlib
user, password = sys.argv[1], sys.argv[2]
path = pathlib.Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
wanted = {"FEICMS_ADMIN_USER": user, "FEICMS_ADMIN_PASSWORD": password}
out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in wanted:
        out.append(f"{key}={wanted[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in wanted.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  echo "已生成管理员账号：FEICMS_ADMIN_USER=$USER  FEICMS_ADMIN_PASSWORD=$PASSWORD"
  echo "（改密码：./server.sh passwd --user admin --password 你的密码）"
fi

# ---------------------------------------------------------------- database
step "初始化数据库"
mkdir -p data data/images
if [ ! -f sources.yaml ] && [ -f sources.example.yaml ]; then
  echo "没有 sources.yaml，先复制模板（真实站点列表需要你自己填或用源订阅）"
  cp sources.example.yaml sources.yaml
fi
run_py feicms init-db
run_py feicms seed

# ---------------------------------------------------------------- start
step "启动服务"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
if [ "$START_SERVICE" = true ]; then
  if ./server.sh start; then
    :
  else
    echo "自动启动失败，手动尝试: ./server.sh start（日志: data/server.log）"
  fi
else
  echo "--no-start：跳过自动启动。启动命令: ./server.sh start"
fi

if [ "$SYSTEMD" = true ]; then
  step "注册 systemd 服务（开机自启）"
  UNIT="/etc/systemd/system/feicms.service"
  SUDO=""
  if [ "$(id -u)" != "0" ]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    else
      echo "需要 root 权限，跳过 systemd 注册"
      SYSTEMD=false
    fi
  fi
  if [ "$SYSTEMD" = true ]; then
    PY_BIN="$ROOT/.venv/bin/uvicorn"
    [ -x "$PY_BIN" ] || PY_BIN="$ROOT/.venv/bin/python"
    $SUDO tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=feicms 资源站聚合服务
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$PY_BIN feicms.api:app --host ${FEICMS_HOST:-0.0.0.0} --port ${FEICMS_PORT:-8000}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable feicms.service
    echo "已注册 systemd 服务: $UNIT"
    echo "  systemctl status feicms / journalctl -u feicms -f"
    echo "注意：注册 systemd 后不要用 ./server.sh start（会重复启动）"
  fi
fi

printf '\n\033[1m安装完成\033[0m\n'
echo "面板状态:   ./server.sh status"
echo "查看日志:   ./server.sh logs -f"
echo "默认监听:   0.0.0.0:${FEICMS_PORT:-8000}（局域网其它设备也能访问）"
echo "本机面板:   http://127.0.0.1:${FEICMS_PORT:-8000}/"
for ip in $(hostname -I 2>/dev/null); do
  echo "局域网面板: http://$ip:${FEICMS_PORT:-8000}/"
done
echo "API 文档:   http://127.0.0.1:${FEICMS_PORT:-8000}/docs"
