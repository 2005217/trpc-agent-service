#!/usr/bin/env bash
# 运行单元测试覆盖率
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=.venv/Scripts/python.exe
if [ ! -f "$PYTHON" ]; then
  PYTHON=python
fi
"$PYTHON" -m pytest --cov=trpc_service --cov-report=term-missing "$@"