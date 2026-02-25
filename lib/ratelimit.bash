#!/bin/bash
# Gathm Enterprise - Rate Limiting Library
# Enforces per-tool and global rate limits based on policies.yaml
# Cross-platform: Linux, macOS, Termux, Windows (WSL/Git Bash/MSYS2)

GATHM_RATELIMIT_DIR="${HOME}/.gathm/ratelimit"
GATHM_RATELIMIT_WINDOW="${GATHM_RATELIMIT_WINDOW:-60}"     # Window in seconds
GATHM_RATELIMIT_DEFAULT="${GATHM_RATELIMIT_DEFAULT:-60}"    # Default requests per window
GATHM_RATELIMIT_BURST="${GATHM_RATELIMIT_BURST:-10}"        # Burst allowance

# Initialize rate limit state directory
init_ratelimit() {
    mkdir -p "$GATHM_RATELIMIT_DIR" 2>/dev/null || true
}

# Per-tool rate limits (loaded from policies.yaml patterns)
_get_tool_limit() {
    local tool="$1"
    local policies_file
    policies_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)/config/policies.yaml"

    if [[ -f "$policies_file" ]]; then
        local limit
        limit=$(grep "^    ${tool}:" "$policies_file" 2>/dev/null | sed 's/.*: *//' | tr -d ' ')
        if [[ -n "$limit" && "$limit" =~ ^[0-9]+$ ]]; then
            echo "$limit"
            return
        fi
    fi
    echo "$GATHM_RATELIMIT_DEFAULT"
}

# Check and enforce rate limit for a tool
# Usage: ratelimit_check TOOL_NAME
# Returns: 0 if allowed, 1 if rate limited
ratelimit_check() {
    local tool="$1"
    local now
    now=$(date +%s)
    local state_file="$GATHM_RATELIMIT_DIR/rl_${tool}"
    local limit
    limit=$(_get_tool_limit "$tool")
    local effective_limit=$((limit + GATHM_RATELIMIT_BURST))

    # Create state file if missing
    if [[ ! -f "$state_file" ]]; then
        echo "window_start=$now" > "$state_file"
        echo "count=0" >> "$state_file"
    fi

    # Read current state
    local window_start count
    window_start=$(grep "^window_start=" "$state_file" 2>/dev/null | cut -d= -f2)
    count=$(grep "^count=" "$state_file" 2>/dev/null | cut -d= -f2)
    window_start="${window_start:-$now}"
    count="${count:-0}"

    # Check if window has expired - reset
    if (( now - window_start >= GATHM_RATELIMIT_WINDOW )); then
        window_start=$now
        count=0
    fi

    # Check limit
    if (( count >= effective_limit )); then
        local remaining=$((GATHM_RATELIMIT_WINDOW - (now - window_start)))
        log_warn "ratelimit" "Rate limit exceeded for $tool ($count/$limit in window, resets in ${remaining}s)" 2>/dev/null
        return 1
    fi

    # Increment and save
    count=$((count + 1))
    cat > "$state_file" << EOF
window_start=$window_start
count=$count
EOF
    return 0
}

# Get rate limit status for a tool (JSON)
# Usage: ratelimit_status TOOL_NAME
ratelimit_status() {
    local tool="$1"
    local now
    now=$(date +%s)
    local state_file="$GATHM_RATELIMIT_DIR/rl_${tool}"
    local limit
    limit=$(_get_tool_limit "$tool")

    if [[ ! -f "$state_file" ]]; then
        printf '{"tool":"%s","limit":%d,"used":0,"remaining":%d,"window_seconds":%d}' \
            "$tool" "$limit" "$limit" "$GATHM_RATELIMIT_WINDOW"
        return
    fi

    local window_start count
    window_start=$(grep "^window_start=" "$state_file" 2>/dev/null | cut -d= -f2)
    count=$(grep "^count=" "$state_file" 2>/dev/null | cut -d= -f2)
    window_start="${window_start:-$now}"
    count="${count:-0}"

    # Reset if window expired
    if (( now - window_start >= GATHM_RATELIMIT_WINDOW )); then
        count=0
    fi

    local remaining=$((limit - count))
    [[ $remaining -lt 0 ]] && remaining=0

    printf '{"tool":"%s","limit":%d,"used":%d,"remaining":%d,"window_seconds":%d}' \
        "$tool" "$limit" "$count" "$remaining" "$GATHM_RATELIMIT_WINDOW"
}

# Reset rate limit state for a tool
# Usage: ratelimit_reset TOOL_NAME
ratelimit_reset() {
    local tool="$1"
    rm -f "$GATHM_RATELIMIT_DIR/rl_${tool}" 2>/dev/null
}

# Get rate limit status for all tools (JSON array)
ratelimit_status_all() {
    local result="["
    local first=true
    for state_file in "$GATHM_RATELIMIT_DIR"/rl_*; do
        [[ ! -f "$state_file" ]] && continue
        local tool
        tool=$(basename "$state_file" | sed 's/^rl_//')
        if ! $first; then result+=","; fi
        first=false
        result+=$(ratelimit_status "$tool")
    done
    result+="]"
    echo "$result"
}

# Initialize
init_ratelimit
