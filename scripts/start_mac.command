#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PACKAGED="$ROOT/dist/risk-model-agent/risk-model-agent"

# A package embeds frontend/dist and Python code at build time.  Never start
# it when repository source has been modified afterwards; doing so makes the
# browser and API silently come from different versions.
packaged_is_current() {
  [ -x "$PACKAGED" ] || return 1
  if find "$ROOT/app" "$ROOT/frontend/dist" -type f \
    ! -path '*/__pycache__/*' ! -name '*.pyc' \
    -newer "$PACKAGED" -print -quit 2>/dev/null | grep -q .; then
    return 1
  fi
  return 0
}

if packaged_is_current; then
  exec "$PACKAGED"
fi
if [ -x "$PACKAGED" ]; then
  echo "打包后端早于当前源码，已改用源码环境启动；如需使用打包版请先重新构建。" >&2
fi
if [ -x "$ROOT/.venv/bin/python" ]; then
  exec "$ROOT/.venv/bin/python" -m app.main
fi
exec python3 -m app.main
