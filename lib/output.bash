#!/usr/bin/env bash
# Gathm Enterprise — Structured Tool Output
# Source this in every tool script to emit consistent text or JSON output.
#
# Usage:
#   source "$(dirname "$0")/../../lib/output.bash"
#
#   # At the end of the tool, emit results:
#   tool_output 0 "plain text result"          # text mode → prints as-is
#                                               # json mode → wraps in JSON envelope
#
#   tool_output_json 0 '{"key":"value"}'        # json mode → merges into envelope
#                                               # text mode → prints the JSON string
#
#   tool_error 1 "something went wrong"         # always emits to stderr; sets exit 1
#
# GATHM_OUTPUT_MODE controls format: "json" or "text" (default: text)

# ---------------------------------------------------------------------------
# Internal: escape a string for use as a JSON value
# ---------------------------------------------------------------------------
_json_escape() {
    # Handles backslash, double-quote, and control characters
    printf '%s' "$1" \
        | sed 's/\\/\\\\/g; s/"/\\"/g; s/'"$(printf '\t')"'/\\t/g' \
        | tr -d '\000-\037'
}

# ---------------------------------------------------------------------------
# Internal: detect the tool name from the calling script's filename
# ---------------------------------------------------------------------------
_tool_name() {
    if [[ -n "${GATHM_TOOL_NAME:-}" ]]; then
        echo "$GATHM_TOOL_NAME"
    else
        basename "${BASH_SOURCE[2]:-${0}}"
    fi
}

# ---------------------------------------------------------------------------
# tool_output EXIT_CODE MESSAGE
#   Emit a plain-text message (or JSON envelope in json mode).
#   Returns EXIT_CODE.
# ---------------------------------------------------------------------------
tool_output() {
    local exit_code="${1:-0}"
    local message="${2:-}"

    if [[ "${GATHM_OUTPUT_MODE:-text}" == "json" ]]; then
        local tool
        tool=$(_tool_name)
        local escaped
        escaped=$(_json_escape "$message")
        local status="success"
        [[ "$exit_code" -ne 0 ]] && status="error"
        printf '{"tool":"%s","status":"%s","exit_code":%d,"output":"%s"}\n' \
            "$tool" "$status" "$exit_code" "$escaped"
    else
        printf '%s\n' "$message"
    fi

    return "$exit_code"
}

# ---------------------------------------------------------------------------
# tool_output_json EXIT_CODE JSON_STRING
#   Emit a pre-formed JSON object as tool output.
#   In json mode: merges tool/status/exit_code fields into the object.
#   In text mode: prints the raw JSON string (pretty if jq is available).
# ---------------------------------------------------------------------------
tool_output_json() {
    local exit_code="${1:-0}"
    local json="${2:-{\}}"

    if [[ "${GATHM_OUTPUT_MODE:-text}" == "json" ]]; then
        local tool
        tool=$(_tool_name)
        local status="success"
        [[ "$exit_code" -ne 0 ]] && status="error"

        # Inject envelope fields if jq is available; otherwise concatenate
        if command -v jq &>/dev/null; then
            printf '%s' "$json" \
                | jq --arg tool "$tool" --arg status "$status" --argjson code "$exit_code" \
                    '. + {tool: $tool, status: $status, exit_code: $code}'
        else
            # Strip trailing } and append fields manually
            local body="${json%\}}"
            printf '%s,"tool":"%s","status":"%s","exit_code":%d}\n' \
                "$body" "$tool" "$status" "$exit_code"
        fi
    else
        if command -v jq &>/dev/null; then
            printf '%s' "$json" | jq .
        else
            printf '%s\n' "$json"
        fi
    fi

    return "$exit_code"
}

# ---------------------------------------------------------------------------
# tool_error EXIT_CODE MESSAGE
#   Emit an error to stderr and, in json mode, also to stdout as a JSON error.
#   Always returns EXIT_CODE (defaults to 1).
# ---------------------------------------------------------------------------
tool_error() {
    local exit_code="${1:-1}"
    local message="${2:-unknown error}"

    printf '[ERROR] %s\n' "$message" >&2

    if [[ "${GATHM_OUTPUT_MODE:-text}" == "json" ]]; then
        local tool
        tool=$(_tool_name)
        local escaped
        escaped=$(_json_escape "$message")
        printf '{"tool":"%s","status":"error","exit_code":%d,"error":"%s"}\n' \
            "$tool" "$exit_code" "$escaped"
    fi

    return "$exit_code"
}
