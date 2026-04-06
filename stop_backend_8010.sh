#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8010}"

PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN || true)"

if [[ -z "${PIDS}" ]]; then
  echo "[INFO] 未发现监听 ${PORT} 端口的进程"
  exit 0
fi

echo "[INFO] 即将停止 ${PORT} 端口进程: ${PIDS}"
kill ${PIDS}
sleep 1

LEFT="$(lsof -tiTCP:${PORT} -sTCP:LISTEN || true)"
if [[ -n "${LEFT}" ]]; then
  echo "[WARN] 进程仍在运行，强制终止: ${LEFT}"
  kill -9 ${LEFT}
fi

echo "[OK] ${PORT} 端口实例已停止"
