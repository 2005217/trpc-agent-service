#!/usr/bin/env bash
# 启动服务并记录 PID
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=.venv/Scripts/python.exe
if [ ! -f "$PYTHON" ]; then
  PYTHON=python
fi
"$PYTHON" -m trpc_service.web.app &
echo $! > .server.pid
echo "服务已启动，PID: $(cat .server.pid)，访问 http://127.0.0.1:8000"