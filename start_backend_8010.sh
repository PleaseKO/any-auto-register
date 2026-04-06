#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8010}"
APP_RELOAD="${APP_RELOAD:-0}"

echo "[INFO] 启动第二个实例: http://${HOST}:${PORT}"
echo "[INFO] 项目目录: $PROJECT_ROOT"

exec ./.venv/bin/python main.py
