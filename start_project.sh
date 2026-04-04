#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
API_PORT="${PORT:-8000}"
START_FRONTEND_DEV=0
INSTALL_MISSING=0
SKIP_FRONTEND_BUILD=0
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --frontend-dev)
      START_FRONTEND_DEV=1
      ;;
    --install-missing)
      INSTALL_MISSING=1
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD=1
      ;;
    --check-only)
      CHECK_ONLY=1
      ;;
    -h|--help)
      cat <<'USAGE'
用法: ./start_project.sh [选项]

选项:
  --frontend-dev       同时启动 frontend 的 Vite 开发服务器
  --install-missing    缺少依赖时自动执行 pip/npm 安装
  --skip-frontend-build  不自动构建前端静态文件
  --check-only         仅检查依赖，不实际启动
  -h, --help           查看帮助
USAGE
      exit 0
      ;;
    *)
      echo "[ERROR] 未知参数: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] 未找到虚拟环境 Python: $PYTHON_BIN"
  echo "请先创建 .venv，或改为使用 conda 环境后再调整脚本。"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  NPM_MISSING=1
else
  NPM_MISSING=0
fi

check_python_deps() {
  "$PYTHON_BIN" - <<'PY'
import importlib
import json

required = {
    'fastapi': 'fastapi',
    'uvicorn': 'uvicorn',
    'quart': 'quart',
    'sqlmodel': 'sqlmodel',
    'curl_cffi': 'curl_cffi',
    'requests': 'requests',
    'pysocks': 'socks',
    'playwright': 'playwright',
    'patchright': 'patchright',
    'pydantic': 'pydantic',
    'jwcrypto': 'jwcrypto',
    'cbor2': 'cbor2',
    'camoufox': 'camoufox',
    'aiofiles': 'aiofiles',
    'rich': 'rich',
    'httpx': 'httpx',
    'selectolax': 'selectolax',
}
optional = {'pyinstaller': 'PyInstaller'}
missing_required = []
missing_optional = []
for pkg, mod in required.items():
    try:
        importlib.import_module(mod)
    except Exception as exc:
        missing_required.append({'package': pkg, 'module': mod, 'error': f'{type(exc).__name__}: {exc}'})
for pkg, mod in optional.items():
    try:
        importlib.import_module(mod)
    except Exception as exc:
        missing_optional.append({'package': pkg, 'module': mod, 'error': f'{type(exc).__name__}: {exc}'})
print(json.dumps({'missing_required': missing_required, 'missing_optional': missing_optional}, ensure_ascii=False))
PY
}

check_browser_assets() {
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
from platformdirs import user_cache_dir

playwright_candidates = [
    Path.home() / 'Library/Caches/ms-playwright',
    Path.home() / '.cache/ms-playwright',
]
playwright_dirs = []
for base in playwright_candidates:
    if base.exists():
        playwright_dirs.extend(sorted(p.name for p in base.iterdir() if p.is_dir()))
camoufox_dir = Path(user_cache_dir('camoufox'))
print(json.dumps({
    'playwright_cache_found': bool(playwright_dirs),
    'playwright_entries': playwright_dirs,
    'camoufox_cache_found': camoufox_dir.exists(),
    'camoufox_path': str(camoufox_dir),
}, ensure_ascii=False))
PY
}

maybe_install_python() {
  if [[ "$INSTALL_MISSING" -eq 1 ]]; then
    echo "[INFO] 安装 Python 依赖..."
    "$PYTHON_BIN" -m pip install -r "$PROJECT_ROOT/requirements.txt"
  fi
}

maybe_install_frontend() {
  if [[ "$INSTALL_MISSING" -eq 1 ]]; then
    if [[ "$NPM_MISSING" -eq 1 ]]; then
      echo "[ERROR] 系统未安装 npm，无法自动安装前端依赖。"
      exit 1
    fi
    echo "[INFO] 安装前端依赖..."
    (cd "$FRONTEND_DIR" && npm install)
  fi
}

PY_DEPS_JSON="$(check_python_deps)"
BROWSER_JSON="$(check_browser_assets)"

PY_DEPS_JSON="$PY_DEPS_JSON" BROWSER_JSON="$BROWSER_JSON" PROJECT_ROOT="$PROJECT_ROOT" FRONTEND_DIR="$FRONTEND_DIR" NPM_MISSING="$NPM_MISSING" python3 - <<'PY'
import json
import os
from pathlib import Path

py = json.loads(os.environ['PY_DEPS_JSON'])
browser = json.loads(os.environ['BROWSER_JSON'])
root = Path(os.environ['PROJECT_ROOT'])
frontend_dir = Path(os.environ['FRONTEND_DIR'])
node_modules = frontend_dir / 'node_modules'
static_dir = root / 'static'

print('[CHECK] 依赖状态')
if py['missing_required']:
    print('  - 后端 Python 依赖: 缺失')
    for item in py['missing_required']:
        print(f"    * {item['package']} -> {item['error']}")
else:
    print('  - 后端 Python 依赖: 已安装')

if py['missing_optional']:
    print('  - 可选 Python 依赖: 部分缺失')
    for item in py['missing_optional']:
        print(f"    * {item['package']} -> {item['error']}")
else:
    print('  - 可选 Python 依赖: 已安装')

if os.environ['NPM_MISSING'] == '1':
    print('  - npm: 未安装')
elif node_modules.exists():
    print('  - 前端 npm 依赖: 已安装')
else:
    print('  - 前端 npm 依赖: 未安装')

if (static_dir / 'index.html').exists():
    print('  - 前端静态构建: 已生成 (static/index.html)')
else:
    print('  - 前端静态构建: 未生成')

if browser['playwright_cache_found']:
    sample = ', '.join(browser['playwright_entries'][:3])
    print(f'  - Playwright 浏览器资源: 已检测到 ({sample})')
else:
    print('  - Playwright 浏览器资源: 未检测到')

if browser['camoufox_cache_found']:
    print(f"  - Camoufox 资源: 已检测到 ({browser['camoufox_path']})")
else:
    print('  - Camoufox 资源: 未检测到')
PY

if [[ "$PY_DEPS_JSON" == *'"missing_required": []'* ]]; then
  :
else
  maybe_install_python
  PY_DEPS_JSON="$(check_python_deps)"
  if [[ "$PY_DEPS_JSON" != *'"missing_required": []'* ]]; then
    echo "[ERROR] 后端关键依赖仍不完整，已停止启动。"
    exit 1
  fi
fi

if [[ "$NPM_MISSING" -eq 0 && ! -d "$FRONTEND_DIR/node_modules" ]]; then
  maybe_install_frontend
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "[INFO] 仅检查完成，未启动服务。"
  exit 0
fi

if [[ "$START_FRONTEND_DEV" -eq 1 ]]; then
  if [[ "$NPM_MISSING" -eq 1 ]]; then
    echo "[ERROR] 需要 npm 才能启动前端开发服务器。"
    exit 1
  fi
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "[ERROR] 前端依赖未安装，请先执行: (cd frontend && npm install)"
    exit 1
  fi
else
  if [[ "$SKIP_FRONTEND_BUILD" -eq 0 && ! -f "$PROJECT_ROOT/static/index.html" ]]; then
    if [[ "$NPM_MISSING" -eq 1 ]]; then
      echo "[WARN] 未安装 npm，无法构建前端静态文件；后端启动后仅 API 可用。"
    elif [[ -d "$FRONTEND_DIR/node_modules" ]]; then
      echo "[INFO] 检测到 static 缺失，开始构建前端..."
      (cd "$FRONTEND_DIR" && npm run build)
    else
      echo "[WARN] 前端依赖未安装，跳过静态构建；访问 8000 时不会有完整前端页面。"
    fi
  fi
fi

if [[ "$START_FRONTEND_DEV" -eq 1 ]]; then
  trap 'kill 0' EXIT INT TERM
  echo "[INFO] 启动后端: http://127.0.0.1:${API_PORT}"
  "$PYTHON_BIN" "$PROJECT_ROOT/main.py" &
  BACKEND_PID=$!
  echo "[INFO] 启动前端开发服务器..."
  (cd "$FRONTEND_DIR" && npm run dev) &
  FRONTEND_PID=$!
  wait "$BACKEND_PID" "$FRONTEND_PID"
else
  echo "[INFO] 启动后端: http://127.0.0.1:${API_PORT}"
  exec "$PYTHON_BIN" "$PROJECT_ROOT/main.py"
fi
