#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
(cd "$ROOT/frontend" && npm ci && npm run build)
"$PYTHON" "$ROOT/scripts/verify_packaging.py"
if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller 未安装，请先执行: $PYTHON -m pip install \".[package]\"" >&2
  exit 2
fi
exec "$PYTHON" -m PyInstaller "$ROOT/packaging/risk_model_agent.spec" --noconfirm --clean
