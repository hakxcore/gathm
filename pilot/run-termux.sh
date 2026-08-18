#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

if [[ -x "$ROOT/venv/bin/python" ]]; then
    exec "$ROOT/venv/bin/python" "$ROOT/pilot/main.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$ROOT/pilot/main.py" "$@"
else
    exec python "$ROOT/pilot/main.py" "$@"
fi
