#!/bin/bash
# 停止前后端开发服务
for port in 3000 8000; do
  pid=$(lsof -ti :$port 2>/dev/null || true)
  if [ -n "$pid" ]; then
    kill $pid 2>/dev/null || true
    echo "[ok] 已停止端口 $port (pid $pid)"
  else
    echo "[skip] 端口 $port 未在运行"
  fi
done
