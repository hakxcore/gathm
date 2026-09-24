#!/bin/bash
# An API worker receives a positive allowlist; CLI sessions leave it unset.
# Check every execution, including cache hits and recursive fallbacks.
gathm_tool_allowed() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
        echo '{"error":"invalid_tool_name"}' >&2
        return 1
    fi
    if [[ "${GATHM_ALLOWED_TOOLS+x}" == x ]]; then
        case ",${GATHM_ALLOWED_TOOLS}," in
            *",${name},"*) ;;
            *) echo '{"error":"tool_access_denied"}' >&2; return 1 ;;
        esac
    fi
}
