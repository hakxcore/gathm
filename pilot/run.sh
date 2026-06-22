#!/usr/bin/env bash
# Gathm Pilot launcher.
# Prefers the project venv when present, otherwise falls back to system
# Python. If the required dependencies (e.g. rich) are missing, it prints
# an actionable message instead of crashing with a raw traceback.
cd "$(dirname "$0")" || exit 1

PYTHON=""
if [[ -x "venv/bin/python" ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate 2>/dev/null || true
    PYTHON="venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "Error: Python 3 not found. Install Python 3.8+ and try again." >&2
    exit 1
fi

# Preflight: Pilot's hard dependency is `rich`. If it can't be imported the
# venv was never built (or the install failed), so guide the user rather
# than letting main.py crash with a ModuleNotFoundError traceback.
if ! "$PYTHON" -c "import rich" 2>/dev/null; then
    echo "Pilot dependencies are not installed for: $PYTHON" >&2
    echo "" >&2
    echo "Install them with:" >&2
    echo "    pip install -r pilot/requirements.txt" >&2
    echo "or re-run the installer:" >&2
    echo "    bash install.sh" >&2
    exit 1
fi

exec "$PYTHON" main.py "$@"
