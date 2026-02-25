#!/usr/bin/env bash
# Gathm Enterprise - AI Engineering Agent
# Handles automated tool generation, code modification, and codebase maintenance.
# Cross-platform: Linux, macOS, Termux, Windows (WSL/Git Bash)

ENGINEER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
GATHM_ROOT="$(cd "$ENGINEER_DIR/.." &>/dev/null && pwd)"

# Source libraries
source "$GATHM_ROOT/lib/utils.bash"
source "$GATHM_ROOT/lib/logging.bash"
source "$GATHM_ROOT/lib/schema.bash"

# --- Cross-Platform Python Detection ---
_find_python() {
    local cmd
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            echo "$cmd"
            return 0
        fi
    done
    echo ""
    return 1
}

cmd_engineer() {
    local task="$*"
    if [[ -z "$task" ]]; then
        echo "Usage: gathm-agent engineer <task_description>" >&2
        echo "Example: gathm-agent engineer 'create a new tool called portscan that uses nmap'" >&2
        return 1
    fi

    log_info "engineer" "Processing engineering task: $task"
    
    local python_cmd
    python_cmd=$(_find_python)
    if [[ -z "$python_cmd" ]]; then
        log_error "engineer" "Python not found. Engineering agent requires Python 3."
        return 1
    fi

    # Execute the Python engineering agent
    "$python_cmd" "$ENGINEER_DIR/main.py" "$task"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    cmd_engineer "$@"
fi
