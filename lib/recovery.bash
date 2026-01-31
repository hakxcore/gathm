#!/bin/bash
# Gathm Enterprise - Auto-Recovery Engine
# Handles automatic failure recovery, retries, and fallback chains

SCRIPT_DIR_RECOVERY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"
source "$SCRIPT_DIR_RECOVERY/lib/logging.bash" 2>/dev/null
source "$SCRIPT_DIR_RECOVERY/lib/health.bash" 2>/dev/null

# Default retry configuration
GATHM_MAX_RETRIES="${GATHM_MAX_RETRIES:-3}"
GATHM_RETRY_DELAY="${GATHM_RETRY_DELAY:-2}"        # Initial delay in seconds
GATHM_RETRY_BACKOFF="${GATHM_RETRY_BACKOFF:-2}"     # Multiplier for exponential backoff
GATHM_RETRY_MAX_DELAY="${GATHM_RETRY_MAX_DELAY:-30}" # Max delay between retries

# Retry a command with exponential backoff
# Usage: retry_with_backoff MAX_RETRIES COMMAND [args...]
retry_with_backoff() {
    local max_retries="$1"
    shift
    local delay="$GATHM_RETRY_DELAY"
    local attempt=0
    local exit_code=0

    while (( attempt < max_retries )); do
        attempt=$((attempt + 1))
        log_debug "recovery" "Attempt $attempt/$max_retries: $*"

        "$@"
        exit_code=$?

        if [[ $exit_code -eq 0 ]]; then
            if (( attempt > 1 )); then
                log_info "recovery" "Command succeeded on attempt $attempt: $*"
            fi
            return 0
        fi

        if (( attempt < max_retries )); then
            log_warn "recovery" "Attempt $attempt failed (exit: $exit_code), retrying in ${delay}s: $*"
            sleep "$delay"
            delay=$((delay * GATHM_RETRY_BACKOFF))
            if (( delay > GATHM_RETRY_MAX_DELAY )); then
                delay=$GATHM_RETRY_MAX_DELAY
            fi
        fi
    done

    log_error "recovery" "All $max_retries attempts failed for: $*"
    return $exit_code
}

# Execute a tool with full recovery pipeline
# Usage: execute_with_recovery TOOL_NAME [args...]
# This is the main entry point for resilient tool execution
execute_with_recovery() {
    local tool_name="$1"
    shift
    local tool_args=("$@")
    local tool_path="$SCRIPT_DIR_RECOVERY/tools/$tool_name/$tool_name"

    log_info "recovery" "Executing tool with recovery: $tool_name" "\"args\":\"${tool_args[*]}\""

    # Step 1: Check circuit breaker
    if ! cb_allow_request "$tool_name"; then
        log_warn "recovery" "Circuit breaker OPEN for $tool_name, trying fallback"
        local fallback
        fallback=$(get_fallback_tool "$tool_name")
        if [[ -n "$fallback" && "$fallback" != "null" ]]; then
            log_info "recovery" "Using fallback tool: $fallback for $tool_name"
            execute_with_recovery "$fallback" "${tool_args[@]}"
            return $?
        fi
        log_error "recovery" "No fallback available for $tool_name, circuit is open"
        echo '{"error":"service_unavailable","tool":"'"$tool_name"'","message":"Circuit breaker is open. Tool temporarily disabled due to repeated failures."}' >&2
        return 1
    fi

    # Step 2: Auto-install missing dependencies
    auto_install_deps "$tool_name"

    # Step 3: Execute with retries
    local start_ms
    start_ms=$(date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000)))

    local output exit_code
    output=$(retry_with_backoff "$GATHM_MAX_RETRIES" "$tool_path" "${tool_args[@]}" 2>&1)
    exit_code=$?

    local end_ms
    end_ms=$(date +%s%3N 2>/dev/null || echo $(($(date +%s) * 1000)))
    local duration=$((end_ms - start_ms))

    # Step 4: Update circuit breaker and metrics
    if [[ $exit_code -eq 0 ]]; then
        cb_record_success "$tool_name"
        log_metric "$tool_name" "$duration" "$exit_code"
        echo "$output"
    else
        cb_record_failure "$tool_name"
        log_metric "$tool_name" "$duration" "$exit_code"

        # Step 5: Try fallback on failure
        local fallback
        fallback=$(get_fallback_tool "$tool_name")
        if [[ -n "$fallback" && "$fallback" != "null" ]]; then
            log_warn "recovery" "Tool $tool_name failed, trying fallback: $fallback"
            execute_with_recovery "$fallback" "${tool_args[@]}"
            return $?
        fi

        echo "$output"
        return $exit_code
    fi
}

# Get fallback tool from manifest
# Usage: get_fallback_tool TOOL_NAME
get_fallback_tool() {
    local tool_name="$1"
    local manifest="$SCRIPT_DIR_RECOVERY/tools/$tool_name/tool.yaml"

    if [[ ! -f "$manifest" ]]; then
        echo ""
        return
    fi

    local fallback
    fallback=$(grep "fallback_tool:" "$manifest" | sed 's/.*fallback_tool: *//' | tr -d '"' | tr -d "'" 2>/dev/null)
    echo "$fallback"
}

# Auto-install missing dependencies for a tool
# Usage: auto_install_deps TOOL_NAME
auto_install_deps() {
    local tool_name="$1"
    local manifest="$SCRIPT_DIR_RECOVERY/tools/$tool_name/tool.yaml"

    if [[ ! -f "$manifest" ]]; then
        return 0
    fi

    local deps
    deps=$(grep -A 20 "^dependencies:" "$manifest" | grep "system:" | sed 's/.*system: *\[//;s/\].*//;s/,/ /g;s/"//g' 2>/dev/null)

    for dep in $deps; do
        if ! command -v "$dep" &>/dev/null; then
            log_warn "recovery" "Missing dependency '$dep' for tool '$tool_name', attempting install"
            _try_install_dep "$dep"
        fi
    done
}

# Try to install a dependency using available package manager
_try_install_dep() {
    local dep="$1"

    # Detect package manager
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y "$dep" 2>/dev/null && {
            log_info "recovery" "Installed dependency: $dep (via apt-get)"
            return 0
        }
    elif command -v pkg &>/dev/null; then
        pkg install -y "$dep" 2>/dev/null && {
            log_info "recovery" "Installed dependency: $dep (via pkg)"
            return 0
        }
    elif command -v brew &>/dev/null; then
        brew install "$dep" 2>/dev/null && {
            log_info "recovery" "Installed dependency: $dep (via brew)"
            return 0
        }
    elif command -v yum &>/dev/null; then
        sudo yum install -y "$dep" 2>/dev/null && {
            log_info "recovery" "Installed dependency: $dep (via yum)"
            return 0
        }
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm "$dep" 2>/dev/null && {
            log_info "recovery" "Installed dependency: $dep (via pacman)"
            return 0
        }
    fi

    log_error "recovery" "Failed to auto-install dependency: $dep"
    return 1
}

# Validate tool output (basic sanity check)
# Usage: validate_output TOOL_NAME OUTPUT
validate_output() {
    local tool_name="$1"
    local output="$2"

    # Check for empty output
    if [[ -z "$output" ]]; then
        log_warn "recovery" "Empty output from tool: $tool_name"
        return 1
    fi

    # Check for common error patterns
    if echo "$output" | grep -qi "error\|failed\|not found\|connection refused" &>/dev/null; then
        log_warn "recovery" "Potential error in output from tool: $tool_name"
        return 1
    fi

    return 0
}

# Self-heal: check and fix common issues
# Usage: self_heal TOOL_NAME
self_heal() {
    local tool_name="$1"
    local tool_dir="$SCRIPT_DIR_RECOVERY/tools/$tool_name"
    local tool_path="$tool_dir/$tool_name"
    local healed=false

    log_info "recovery" "Running self-heal for tool: $tool_name"

    # Fix: executable permissions
    if [[ -f "$tool_path" && ! -x "$tool_path" ]]; then
        chmod +x "$tool_path"
        log_info "recovery" "Fixed executable permissions for: $tool_name"
        healed=true
    fi

    # Fix: missing dependencies
    auto_install_deps "$tool_name"

    # Fix: reset circuit breaker if tool is now healthy
    local health
    health=$(healthcheck_tool "$tool_name")
    if echo "$health" | grep -q '"status":"healthy"'; then
        cb_record_success "$tool_name"
        log_info "recovery" "Tool $tool_name is healthy, circuit breaker reset"
        healed=true
    fi

    if $healed; then
        log_info "recovery" "Self-heal completed for: $tool_name"
    else
        log_info "recovery" "No issues found to heal for: $tool_name"
    fi
}
