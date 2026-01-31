#!/bin/bash
# Gathm Enterprise - Structured Logging Library
# Provides JSON-structured logging for all tools and agent operations

GATHM_LOG_DIR="${GATHM_LOG_DIR:-${HOME}/.gathm/logs}"
GATHM_LOG_LEVEL="${GATHM_LOG_LEVEL:-INFO}"
GATHM_LOG_FILE="${GATHM_LOG_DIR}/gathm.log"
GATHM_AUDIT_FILE="${GATHM_LOG_DIR}/audit.log"
GATHM_METRICS_FILE="${GATHM_LOG_DIR}/metrics.log"

# Log levels (numeric for comparison)
declare -A LOG_LEVELS=( [DEBUG]=0 [INFO]=1 [WARN]=2 [ERROR]=3 [FATAL]=4 )

# Initialize logging
init_logging() {
    mkdir -p "$GATHM_LOG_DIR"
    touch "$GATHM_LOG_FILE" "$GATHM_AUDIT_FILE" "$GATHM_METRICS_FILE"
}

# Get current timestamp in ISO 8601
_timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# Generate a unique request/trace ID
_trace_id() {
    echo "$(date +%s%N | sha256sum | head -c 16 2>/dev/null || echo $$-$(date +%s))"
}

# Core structured log function - outputs JSON
# Usage: _log LEVEL COMPONENT MESSAGE [extra_json_fields]
_log() {
    local level="$1"
    local component="$2"
    local message="$3"
    local extra="${4:-}"

    # Check log level threshold
    local level_num="${LOG_LEVELS[$level]:-1}"
    local threshold="${LOG_LEVELS[$GATHM_LOG_LEVEL]:-1}"
    if (( level_num < threshold )); then
        return 0
    fi

    local timestamp
    timestamp=$(_timestamp)
    local hostname
    hostname=$(hostname 2>/dev/null || echo "unknown")
    local pid=$$

    local log_entry
    if [[ -n "$extra" ]]; then
        log_entry=$(printf '{"timestamp":"%s","level":"%s","component":"%s","message":"%s","hostname":"%s","pid":%d,%s}' \
            "$timestamp" "$level" "$component" "$message" "$hostname" "$pid" "$extra")
    else
        log_entry=$(printf '{"timestamp":"%s","level":"%s","component":"%s","message":"%s","hostname":"%s","pid":%d}' \
            "$timestamp" "$level" "$component" "$message" "$hostname" "$pid")
    fi

    echo "$log_entry" >> "$GATHM_LOG_FILE"

    # Also print errors/fatals to stderr
    if (( level_num >= 3 )); then
        echo "[$level] $component: $message" >&2
    fi
}

# Convenience log functions
log_debug()  { _log "DEBUG" "$1" "$2" "${3:-}"; }
log_info()   { _log "INFO"  "$1" "$2" "${3:-}"; }
log_warn()   { _log "WARN"  "$1" "$2" "${3:-}"; }
log_error()  { _log "ERROR" "$1" "$2" "${3:-}"; }
log_fatal()  { _log "FATAL" "$1" "$2" "${3:-}"; }

# Audit log - tracks who/what triggered actions (for compliance)
# Usage: audit_log ACTION ACTOR TOOL [details]
audit_log() {
    local action="$1"
    local actor="$2"    # "human", "agent", or agent ID
    local tool="$3"
    local details="${4:-}"
    local timestamp
    timestamp=$(_timestamp)

    local entry
    entry=$(printf '{"timestamp":"%s","action":"%s","actor":"%s","tool":"%s","details":"%s"}' \
        "$timestamp" "$action" "$actor" "$tool" "$details")
    echo "$entry" >> "$GATHM_AUDIT_FILE"
}

# Metrics log - track tool invocations, latency, success rates
# Usage: log_metric TOOL DURATION_MS EXIT_CODE [extra]
log_metric() {
    local tool="$1"
    local duration_ms="$2"
    local exit_code="$3"
    local extra="${4:-}"
    local timestamp
    timestamp=$(_timestamp)
    local status="success"
    if [[ "$exit_code" -ne 0 ]]; then
        status="failure"
    fi

    local entry
    if [[ -n "$extra" ]]; then
        entry=$(printf '{"timestamp":"%s","tool":"%s","duration_ms":%s,"exit_code":%d,"status":"%s",%s}' \
            "$timestamp" "$tool" "$duration_ms" "$exit_code" "$status" "$extra")
    else
        entry=$(printf '{"timestamp":"%s","tool":"%s","duration_ms":%s,"exit_code":%d,"status":"%s"}' \
            "$timestamp" "$tool" "$duration_ms" "$exit_code" "$status")
    fi
    echo "$entry" >> "$GATHM_METRICS_FILE"
}

# Timed execution wrapper - runs a command and logs metrics
# Usage: timed_exec TOOL_NAME command [args...]
timed_exec() {
    local tool_name="$1"
    shift
    local start_ms
    start_ms=$(date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000)))

    "$@"
    local exit_code=$?

    local end_ms
    end_ms=$(date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000)))
    local duration=$((end_ms - start_ms))

    log_metric "$tool_name" "$duration" "$exit_code"
    return $exit_code
}

# Get recent metrics summary for a tool
# Usage: get_tool_metrics TOOL_NAME [last_n]
get_tool_metrics() {
    local tool="$1"
    local last_n="${2:-10}"

    if [[ ! -f "$GATHM_METRICS_FILE" ]]; then
        echo '{"total":0,"successes":0,"failures":0,"avg_duration_ms":0}'
        return
    fi

    grep "\"tool\":\"$tool\"" "$GATHM_METRICS_FILE" | tail -n "$last_n" | \
    awk -F'"' '
    BEGIN { total=0; success=0; fail=0; dur=0 }
    {
        total++
        if (index($0, "\"status\":\"success\"")) success++
        else fail++
        # Extract duration
        match($0, /"duration_ms":([0-9]+)/, arr)
        if (arr[1]) dur += arr[1]
    }
    END {
        avg = (total > 0) ? dur/total : 0
        printf "{\"total\":%d,\"successes\":%d,\"failures\":%d,\"avg_duration_ms\":%.0f}\n", total, success, fail, avg
    }'
}

# Initialize on source
init_logging
