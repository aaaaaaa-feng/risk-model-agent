#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ -x "$ROOT/dist/risk-model-agent/risk-model-agent" ]; then
  exec "$ROOT/dist/risk-model-agent/risk-model-agent"
fi
if [ -x "$ROOT/dist/risk-model-agent" ]; then
  exec "$ROOT/dist/risk-model-agent"
fi
if [ -x "$ROOT/.venv/bin/python" ]; then
  exec "$ROOT/.venv/bin/python" -m app.main
fi
exec python3 -m app.main
