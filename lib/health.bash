#!/bin/bash
# Gathm Enterprise - Health Check Framework
# Provides health checking capabilities for all tools

SCRIPT_DIR_HEALTH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"
source "$SCRIPT_DIR_HEALTH/lib/logging.bash" 2>/dev/null

GATHM_HEALTH_DIR="${HOME}/.gathm/health"
GATHM_HEALTH_CACHE_TTL=300  # 5 minutes cache

# Circuit breaker states
CB_STATE_CLOSED="closed"       # Normal operation
CB_STATE_OPEN="open"           # Failing, reject requests
CB_STATE_HALF_OPEN="half_open" # Testing if recovered

CB_FAILURE_THRESHOLD=3         # Failures before opening circuit
CB_RECOVERY_TIMEOUT=60         # Seconds before trying half-open

init_health() {
    mkdir -p "$GATHM_HEALTH_DIR"
}

# Check if a command/dependency exists
check_dependency() {
    local dep="$1"
    command -v "$dep" &>/dev/null
}

# Check if an API endpoint is reachable
# Usage: check_api_endpoint URL [timeout_seconds]
check_api_endpoint() {
    local url="$1"
    local timeout="${2:-5}"

    if command -v curl &>/dev/null; then
        curl -sf --max-time "$timeout" -o /dev/null "$url" 2>/dev/null
    elif command -v wget &>/dev/null; then
        wget -q --timeout="$timeout" -O /dev/null "$url" 2>/dev/null
    else
        return 1
    fi
}

# Run health check for a specific tool
# Usage: healthcheck_tool TOOL_NAME
# Returns JSON health status
healthcheck_tool() {
    local tool_name="$1"
    local tool_dir="$SCRIPT_DIR_HEALTH/tools/$tool_name"
    local manifest="$tool_dir/tool.yaml"
    local status="healthy"
    local issues=()

    # Check tool executable exists
    if [[ ! -x "$tool_dir/$tool_name" ]]; then
        status="unhealthy"
        issues+=("executable not found or not executable")
    fi

    # Check manifest exists
    if [[ ! -f "$manifest" ]]; then
        status="degraded"
        issues+=("no tool.yaml manifest")
    fi

    # Check dependencies from manifest
    if [[ -f "$manifest" ]]; then
        local deps
        deps=$(grep -A 20 "^dependencies:" "$manifest" | grep "system:" | sed 's/.*system: *\[//;s/\].*//;s/,/ /g;s/"//g' 2>/dev/null)
        for dep in $deps; do
            if ! check_dependency "$dep"; then
                status="unhealthy"
                issues+=("missing dependency: $dep")
            fi
        done

        # Check API endpoints
        local api_url
        api_url=$(grep "health_endpoint:" "$manifest" | sed 's/.*health_endpoint: *//' | tr -d '"' 2>/dev/null)
        if [[ -n "$api_url" && "$api_url" != "null" ]]; then
            if ! check_api_endpoint "$api_url"; then
                if [[ "$status" != "unhealthy" ]]; then
                    status="degraded"
                fi
                issues+=("API endpoint unreachable: $api_url")
            fi
        fi
    fi

    # Build JSON response
    local issues_json="[]"
    if [[ ${#issues[@]} -gt 0 ]]; then
        issues_json="["
        for i in "${!issues[@]}"; do
            if [[ $i -gt 0 ]]; then issues_json+=","; fi
            issues_json+="\"${issues[$i]}\""
        done
        issues_json+="]"
    fi

    printf '{"tool":"%s","status":"%s","issues":%s,"checked_at":"%s"}' \
        "$tool_name" "$status" "$issues_json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Run health check for all tools
healthcheck_all() {
    local results="["
    local first=true

    for dir in "$SCRIPT_DIR_HEALTH"/tools/*/; do
        local tool_name
        tool_name=$(basename "$dir")
        if [[ -f "$dir/$tool_name" ]]; then
            if ! $first; then results+=","; fi
            results+=$(healthcheck_tool "$tool_name")
            first=false
        fi
    done
    results+="]"
    echo "$results"
}

# Circuit Breaker - Get state for a tool
# Usage: cb_get_state TOOL_NAME
cb_get_state() {
    local tool="$1"
    local state_file="$GATHM_HEALTH_DIR/cb_${tool}.state"

    if [[ ! -f "$state_file" ]]; then
        echo "$CB_STATE_CLOSED"
        return
    fi

    local state failures last_failure
    state=$(grep "^state=" "$state_file" | cut -d= -f2)
    failures=$(grep "^failures=" "$state_file" | cut -d= -f2)
    last_failure=$(grep "^last_failure=" "$state_file" | cut -d= -f2)

    # If circuit is open, check if recovery timeout has passed
    if [[ "$state" == "$CB_STATE_OPEN" ]]; then
        local now
        now=$(date +%s)
        if (( now - last_failure >= CB_RECOVERY_TIMEOUT )); then
            echo "$CB_STATE_HALF_OPEN"
            return
        fi
    fi

    echo "${state:-$CB_STATE_CLOSED}"
}

# Circuit Breaker - Record a success
cb_record_success() {
    local tool="$1"
    local state_file="$GATHM_HEALTH_DIR/cb_${tool}.state"

    cat > "$state_file" << EOF
state=$CB_STATE_CLOSED
failures=0
last_failure=0
EOF
    log_debug "circuit_breaker" "Circuit closed for tool: $tool"
}

# Circuit Breaker - Record a failure
cb_record_failure() {
    local tool="$1"
    local state_file="$GATHM_HEALTH_DIR/cb_${tool}.state"
    local now
    now=$(date +%s)

    local failures=0
    if [[ -f "$state_file" ]]; then
        failures=$(grep "^failures=" "$state_file" | cut -d= -f2)
    fi
    failures=$((failures + 1))

    local state="$CB_STATE_CLOSED"
    if (( failures >= CB_FAILURE_THRESHOLD )); then
        state="$CB_STATE_OPEN"
        log_warn "circuit_breaker" "Circuit OPEN for tool: $tool (failures: $failures)"
    fi

    cat > "$state_file" << EOF
state=$state
failures=$failures
last_failure=$now
EOF
}

# Circuit Breaker - Check if tool is allowed to execute
# Returns 0 if allowed, 1 if circuit is open
cb_allow_request() {
    local tool="$1"
    local state
    state=$(cb_get_state "$tool")

    case "$state" in
        "$CB_STATE_CLOSED")   return 0 ;;
        "$CB_STATE_HALF_OPEN") return 0 ;;  # Allow one test request
        "$CB_STATE_OPEN")     return 1 ;;
        *)                    return 0 ;;
    esac
}

# Initialize
init_health
