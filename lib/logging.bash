#!/bin/bash
# Gathm Enterprise - Structured Logging Library
# Provides JSON-structured logging for all tools and agent operations
# Cross-platform: Linux, macOS, Termux, Windows (WSL/Git Bash/MSYS2)

GATHM_LOG_DIR="${GATHM_LOG_DIR:-${HOME}/.gathm/logs}"
GATHM_LOG_LEVEL="${GATHM_LOG_LEVEL:-INFO}"
GATHM_LOG_FILE="${GATHM_LOG_DIR}/gathm.log"
GATHM_AUDIT_FILE="${GATHM_LOG_DIR}/audit.log"
GATHM_METRICS_FILE="${GATHM_LOG_DIR}/metrics.log"

# Log levels (using indexed arrays for Bash 3 compatibility)
_log_level_value() {
    case "$1" in
        DEBUG) echo 0 ;;
        INFO)  echo 1 ;;
        WARN)  echo 2 ;;
        ERROR) echo 3 ;;
        FATAL) echo 4 ;;
        *)     echo 1 ;;
    esac
}

# Initialize logging
init_logging() {
    mkdir -p "$GATHM_LOG_DIR" 2>/dev/null || true
    touch "$GATHM_LOG_FILE" "$GATHM_AUDIT_FILE" "$GATHM_METRICS_FILE" 2>/dev/null || true
}

# Cross-platform millisecond timestamp
# Works on: GNU date, BSD date (macOS), BusyBox date (Termux), Windows (WSL/MSYS2)
_timestamp() {
    # Try GNU date with nanoseconds first
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null)
    # If %3N was not expanded (BSD date returns literal "%3NZ"), fall back
    if [[ "$ts" == *"%3N"* ]] || [[ -z "$ts" ]]; then
        ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
    fi
    echo "$ts"
}

# Generate a unique request/trace ID (cross-platform)
_trace_id() {
    # Try multiple methods in order of preference
    if command -v sha256sum &>/dev/null; then
        echo "$(date +%s)$$" | sha256sum | head -c 16
    elif command -v shasum &>/dev/null; then
        # macOS uses shasum
        echo "$(date +%s)$$" | shasum -a 256 | head -c 16
    elif command -v md5sum &>/dev/null; then
        echo "$(date +%s)$$" | md5sum | head -c 16
    elif command -v md5 &>/dev/null; then
        # macOS fallback
        echo "$(date +%s)$$" | md5 | head -c 16
    else
        # Ultimate fallback: PID + epoch
        printf '%08x%08x' $$ "$(date +%s)"
    fi
}

# Cross-platform epoch milliseconds
_epoch_ms() {
    # Try GNU date with nanosecond support
    local ms
    ms=$(date +%s%3N 2>/dev/null)
    # If %3N was not expanded (literal "s%3N" or letters in output), fall back
    if [[ "$ms" =~ [^0-9] ]] || [[ ${#ms} -lt 4 ]]; then
        # Fall back to seconds * 1000
        echo $(( $(date +%s) * 1000 ))
    else
        echo "$ms"
    fi
}

# Append a line to a log file under an exclusive lock.
# Uses flock(1) on Linux/WSL/Termux; falls back to >> (atomic on most POSIX
# filesystems for small writes) on macOS/BSD where flock may not be present.
_log_append() {
    local file="$1"
    local line="$2"
    if command -v flock &>/dev/null; then
        local lock_file="${file}.lock"
        (flock -x 9; printf '%s\n' "$line" >> "$file") 9>>"$lock_file" 2>/dev/null
    else
        printf '%s\n' "$line" >> "$file" 2>/dev/null
    fi
}

# Core structured log function - outputs JSON
# Usage: _log LEVEL COMPONENT MESSAGE [extra_json_fields]
_log() {
    local level="$1"
    local component="$2"
    local message="$3"
    local extra="${4:-}"

    # Check log level threshold
    local level_num
    level_num=$(_log_level_value "$level")
    local threshold
    threshold=$(_log_level_value "$GATHM_LOG_LEVEL")
    if [ "$level_num" -lt "$threshold" ]; then
        return 0
    fi

    local timestamp
    timestamp=$(_timestamp)
    local hostname_val
    hostname_val=$(hostname 2>/dev/null || echo "unknown")
    local pid=$$

    # Escape message for JSON safety
    message=$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')

    local log_entry
    if [[ -n "$extra" ]]; then
        log_entry=$(printf '{"timestamp":"%s","level":"%s","component":"%s","message":"%s","hostname":"%s","pid":%d,%s}' \
            "$timestamp" "$level" "$component" "$message" "$hostname_val" "$pid" "$extra")
    else
        log_entry=$(printf '{"timestamp":"%s","level":"%s","component":"%s","message":"%s","hostname":"%s","pid":%d}' \
            "$timestamp" "$level" "$component" "$message" "$hostname_val" "$pid")
    fi

    _log_append "$GATHM_LOG_FILE" "$log_entry"

    # Also print errors/fatals to stderr
    if [ "$level_num" -ge 3 ]; then
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
    _log_append "$GATHM_AUDIT_FILE" "$entry"
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
    _log_append "$GATHM_METRICS_FILE" "$entry"
}

# Timed execution wrapper - runs a command and logs metrics
# Usage: timed_exec TOOL_NAME command [args...]
timed_exec() {
    local tool_name="$1"
    shift
    local start_ms
    start_ms=$(_epoch_ms)

    "$@"
    local exit_code=$?

    local end_ms
    end_ms=$(_epoch_ms)
    local duration=$((end_ms - start_ms))

    log_metric "$tool_name" "$duration" "$exit_code"
    return $exit_code
}

# Get recent metrics summary for a tool
# Uses only POSIX awk features (no gawk-specific match with capture groups)
# Usage: get_tool_metrics TOOL_NAME [last_n]
get_tool_metrics() {
    local tool="$1"
    local last_n="${2:-10}"

    if [[ ! -f "$GATHM_METRICS_FILE" ]]; then
        echo '{"total":0,"successes":0,"failures":0,"avg_duration_ms":0}'
        return
    fi

    grep "\"tool\":\"$tool\"" "$GATHM_METRICS_FILE" | tail -n "$last_n" | \
    awk '
    BEGIN { total=0; success=0; fail=0; dur=0 }
    {
        total++
        if (index($0, "\"status\":\"success\"")) success++
        else fail++
        # Extract duration_ms using portable split
        n = split($0, parts, "\"duration_ms\":")
        if (n >= 2) {
            sub(/[^0-9].*/, "", parts[2])
            dur += parts[2] + 0
        }
    }
    END {
        avg = (total > 0) ? dur/total : 0
        printf "{\"total\":%d,\"successes\":%d,\"failures\":%d,\"avg_duration_ms\":%.0f}\n", total, success, fail, avg
    }'
}

# Initialize on source
init_logging
