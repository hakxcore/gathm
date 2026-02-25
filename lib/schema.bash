#!/bin/bash
# Gathm Enterprise - Schema & Validation Library
# Provides input/output validation and JSON output formatting

SCRIPT_DIR_SCHEMA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"

# Global output mode flag
GATHM_OUTPUT_MODE="${GATHM_OUTPUT_MODE:-text}"  # "text" or "json"

# Parse the --json flag from arguments
# Usage: parse_output_mode "$@"
# Sets GATHM_OUTPUT_MODE and returns remaining args
parse_output_mode() {
    local args=()
    for arg in "$@"; do
        if [[ "$arg" == "--json" ]]; then
            GATHM_OUTPUT_MODE="json"
        else
            args+=("$arg")
        fi
    done
    echo "${args[@]}"
}

# JSON output helpers
# Build a JSON object from key-value pairs
# Usage: json_object "key1" "value1" "key2" "value2" ...
json_object() {
    local result="{"
    local first=true

    while [[ $# -ge 2 ]]; do
        local key="$1"
        local value="$2"
        shift 2

        if ! $first; then result+=","; fi
        first=false

        # Auto-detect type
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            result+="\"$key\":$value"
        elif [[ "$value" =~ ^[0-9]+\.[0-9]+$ ]]; then
            result+="\"$key\":$value"
        elif [[ "$value" == "true" || "$value" == "false" ]]; then
            result+="\"$key\":$value"
        elif [[ "$value" == "null" ]]; then
            result+="\"$key\":null"
        elif [[ "$value" == "["* || "$value" == "{"* ]]; then
            result+="\"$key\":$value"
        else
            # Escape special characters in strings
            value=$(echo "$value" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr '\n' ' ')
            result+="\"$key\":\"$value\""
        fi
    done

    result+="}"
    echo "$result"
}

# Build a JSON array from values
# Usage: json_array "val1" "val2" ...
json_array() {
    local result="["
    local first=true

    for value in "$@"; do
        if ! $first; then result+=","; fi
        first=false

        if [[ "$value" == "{"* || "$value" == "["* ]]; then
            result+="$value"
        elif [[ "$value" =~ ^[0-9]+$ ]]; then
            result+="$value"
        elif [[ "$value" == "true" || "$value" == "false" || "$value" == "null" ]]; then
            result+="$value"
        else
            result+="\"$value\""
        fi
    done

    result+="]"
    echo "$result"
}

# Wrap tool output in standard JSON envelope
# Usage: json_response TOOL_NAME STATUS OUTPUT [error_message]
json_response() {
    local tool="$1"
    local status="$2"     # "success" or "error"
    local data="$3"
    local error_msg="${4:-}"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    if [[ "$status" == "success" ]]; then
        if [[ "$data" == "{"* || "$data" == "["* ]]; then
            printf '{"tool":"%s","status":"success","data":%s,"timestamp":"%s"}\n' \
                "$tool" "$data" "$timestamp"
        else
            printf '{"tool":"%s","status":"success","data":"%s","timestamp":"%s"}\n' \
                "$tool" "$(echo "$data" | sed 's/"/\\"/g' | tr '\n' ' ')" "$timestamp"
        fi
    else
        printf '{"tool":"%s","status":"error","error":"%s","timestamp":"%s"}\n' \
            "$tool" "${error_msg//\"/\\\"}" "$timestamp"
    fi
}

# Output function that respects GATHM_OUTPUT_MODE
# Usage: gathm_output TOOL_NAME TEXT_OUTPUT JSON_OUTPUT
gathm_output() {
    local tool="$1"
    local text_output="$2"
    local json_output="$3"

    if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
        if [[ "$json_output" == "{"* || "$json_output" == "["* ]]; then
            json_response "$tool" "success" "$json_output"
        else
            json_response "$tool" "success" "$json_output"
        fi
    else
        echo "$text_output"
    fi
}

# Parse tool.yaml manifest
# Usage: parse_manifest TOOL_NAME FIELD
parse_manifest() {
    local tool_name="$1"
    local field="$2"
    local manifest="$SCRIPT_DIR_SCHEMA/tools/$tool_name/tool.yaml"

    if [[ ! -f "$manifest" ]]; then
        echo ""
        return 1
    fi

    grep "^${field}:" "$manifest" | sed "s/^${field}: *//" | tr -d '"' | tr -d "'"
}

# Get tool description from manifest
get_tool_description() {
    parse_manifest "$1" "description"
}

# Get tool version from manifest
get_tool_version() {
    parse_manifest "$1" "version"
}

# Get tool category from manifest
get_tool_category() {
    parse_manifest "$1" "category"
}

# List all tools with their metadata (for agent discovery)
list_tools_json() {
    local result="["
    local first=true

    for dir in "$SCRIPT_DIR_SCHEMA"/tools/*/; do
        local tool_name
        tool_name=$(basename "$dir")
        if [[ ! -f "$dir/$tool_name" ]]; then continue; fi

        local manifest="$dir/tool.yaml"
        if [[ ! -f "$manifest" ]]; then continue; fi

        if ! $first; then result+=","; fi
        first=false

        local desc version category
        desc=$(get_tool_description "$tool_name")
        version=$(get_tool_version "$tool_name")
        category=$(get_tool_category "$tool_name")

        result+=$(printf '{"name":"%s","description":"%s","version":"%s","category":"%s"}' \
            "$tool_name" "$desc" "$version" "$category")
    done

    result+="]"
    echo "$result"
}

# Validate required arguments
# Usage: validate_required TOOL_NAME "arg_name" "arg_value" ...
validate_required() {
    local tool="$1"
    shift
    local errors=()

    while [[ $# -ge 2 ]]; do
        local name="$1"
        local value="$2"
        shift 2
        if [[ -z "$value" ]]; then
            errors+=("Missing required argument: $name")
        fi
    done

    if [[ ${#errors[@]} -gt 0 ]]; then
        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            local err_json
            err_json=$(json_array "${errors[@]}")
            json_response "$tool" "error" "" "Validation failed: ${errors[*]}"
        else
            for err in "${errors[@]}"; do
                echo "Error: $err" >&2
            done
        fi
        return 1
    fi
    return 0
}
