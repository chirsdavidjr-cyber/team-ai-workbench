#!/bin/bash
# 一键启动前后端开发服务
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"

# 后端：首次运行自动建虚拟环境并安装依赖
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "[setup] 创建后端虚拟环境并安装依赖…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
if lsof -ti :8000 >/dev/null 2>&1; then
  echo "[skip] 后端 8000 端口已在运行"
else
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$LOG_DIR/backend.log" 2>&1 &
  echo "[ok] 后端已启动 http://127.0.0.1:8000  (logs/backend.log)"
fi

# 前端
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "[setup] 安装前端依赖…"
  npm install --silent
fi
if lsof -ti :3000 >/dev/null 2>&1; then
  echo "[skip] 前端 3000 端口已在运行"
else
  # 清理上次 dev server 的残留状态（进程已死但 .next/dev 仍记着旧实例时，next dev 会拒绝启动）
  rm -rf "$ROOT/frontend/.next/dev"
  nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
  echo "[ok] 前端已启动 http://localhost:3000   (logs/frontend.log)"
fi

echo ""
echo "打开 http://localhost:3000 开始使用"
echo "管理员账号（见 backend/.env）：admin@team.local / admin123456"
