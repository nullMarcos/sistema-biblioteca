#!/usr/bin/env bash

# Verificaciones HTTP, bindings reales y TCP; cualquier incumplimiento devuelve error.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi
exec "$PYTHON" "$ROOT/scripts/verificar_gateway.py" "$@"
