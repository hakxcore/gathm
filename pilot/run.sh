#!/usr/bin/env bash
# Gathm Pilot launcher.
# Prefers the project venv when present, otherwise falls back to system
# Python. If the required dependencies (e.g. rich) are missing, it prints
# an actionable message instead of crashing with a raw traceback.
cd "$(dirname "$0")" || exit 1

PYTHON=""
if [[ -n "${GATHM_PYTHON:-}" && -x "$GATHM_PYTHON" ]]; then
    PYTHON="$GATHM_PYTHON"
else
    for candidate in venv/bin/python3 venv/bin/python venv/Scripts/python.exe; do
        [[ -x "$candidate" ]] && { PYTHON="$candidate"; break; }
    done
fi
if [[ -n "$PYTHON" ]]; then
    # Preserve the existing POSIX environment for tool subprocesses. Native
    # Windows venvs can run directly without sourcing an incompatible script.
    if [[ "$PYTHON" == venv/bin/* ]]; then
        # shellcheck disable=SC1091
        source venv/bin/activate 2>/dev/null || true
    fi
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "Error: Python 3 not found. Install Python 3.9+ and try again." >&2
    exit 1
fi

# Preflight: Pilot needs `rich` for the TUI and `langchain_core` for the agent
# loop. If either is missing the venv was never built (or the install failed),
# so guide the user rather than letting main.py crash with a traceback.
if ! "$PYTHON" -c "import rich, langchain_core" 2>/dev/null; then
    echo "Pilot dependencies are not installed for: $PYTHON" >&2
    echo "" >&2
    echo "Install them with:" >&2
    echo "    pip install -r pilot/requirements.txt" >&2
    echo "or re-run the installer:" >&2
    echo "    ./install" >&2
    exit 1
fi

exec "$PYTHON" main.py "$@"
