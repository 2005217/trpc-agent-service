#!/usr/bin/env bash
# 停止服务（按 PID 文件）
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .server.pid ]; then
  PID=$(cat .server.pid)
  kill "$PID" 2>/dev/null && echo "已停止进程 $PID" || echo "进程 $PID 不存在或已停止"
  rm -f .server.pid
else
  echo "未找到 .server.pid，可能服务未通过 start.sh 启动"
fi