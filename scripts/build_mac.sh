#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" -m PyInstaller "$ROOT/packaging/risk_model_agent.spec" --noconfirm --clean
