#!/bin/bash
# 一次性安装：把 dev 前后端注册为 macOS launchd 系统服务（开机自启 + 崩溃自动重启）
# 用法：在【你自己的终端】运行一次  bash setup_services.sh
# 之后无需再跑 dev.sh；彻底停掉见文件末尾注释。
set -e
UID_=$(id -u)
PLIST_DIR="$HOME/Library/LaunchAgents"
ROOT="$(cd "$(dirname "$0")" && pwd)"

# 1) 清掉端口上的旧进程（避免占位）
kill $(lsof -ti :3000 :8000) 2>/dev/null || true
sleep 1

# 2) 注册并立即启动（之前已注册过则先卸载重来，脚本可重复运行）
for SVC in com.luyawen.wb-backend com.luyawen.wb-frontend; do
  launchctl bootout gui/$UID_/$SVC 2>/dev/null || true
done
launchctl bootstrap gui/$UID_ "$PLIST_DIR/com.luyawen.wb-backend.plist"
launchctl bootstrap gui/$UID_ "$PLIST_DIR/com.luyawen.wb-frontend.plist"

# 3) 等待就绪并验证
echo "启动中…"
for i in $(seq 1 30); do
  B=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/auth/config 2>/dev/null || echo 000)
  F=$(curl -s -m 2 -o /dev/null -w "%{http_code}" http://localhost:3000/ 2>/dev/null || echo 000)
  [ "$B" = "200" ] && [ "$F" = "200" ] && break
  sleep 2
done
echo "后端 8000: $B   前端 3000: $F"
if [ "$B" = "200" ] && [ "$F" = "200" ]; then
  echo ""
  echo "✅ 服务已由系统托管：开机自启、崩溃自动重启，以后直接打开 http://localhost:3000"
  echo "   登录：admin@team.local / admin123456   （日志在 $ROOT/logs/）"
else
  echo "⚠️ 未就绪，查看日志：tail -20 $ROOT/logs/backend.log $ROOT/logs/frontend.log"
fi
# 彻底停掉（如需）：
#   launchctl bootout gui/$(id -u)/com.luyawen.wb-backend
#   launchctl bootout gui/$(id -u)/com.luyawen.wb-frontend
