#!/bin/bash
# Gathm Enterprise - Output Caching Library
# Provides transparent caching with TTL for expensive API calls
# Cross-platform: Linux, macOS, Termux, Windows (WSL/Git Bash/MSYS2)

GATHM_CACHE_DIR="${GATHM_CACHE_DIR:-${HOME}/.gathm/cache}"
GATHM_CACHE_DEFAULT_TTL="${GATHM_CACHE_DEFAULT_TTL:-300}"  # 5 minutes default
GATHM_CACHE_ENABLED="${GATHM_CACHE_ENABLED:-true}"

# Initialize cache directory
init_cache() {
    mkdir -p "$GATHM_CACHE_DIR" 2>/dev/null || true
}

# Generate a cache key from tool name + arguments
# Usage: _cache_key TOOL_NAME [args...]
_cache_key() {
    local input="$*"
    if command -v sha256sum &>/dev/null; then
        echo "$input" | sha256sum | cut -d' ' -f1
    elif command -v shasum &>/dev/null; then
        echo "$input" | shasum -a 256 | cut -d' ' -f1
    elif command -v md5sum &>/dev/null; then
        echo "$input" | md5sum | cut -d' ' -f1
    elif command -v md5 &>/dev/null; then
        echo "$input" | md5 | tr -d ' '
    else
        # Fallback: simple hash
        printf '%s' "$input" | cksum | cut -d' ' -f1
    fi
}

# Get cached result if it exists and hasn't expired
# Usage: cache_get TOOL_NAME [args...]
# Returns: cached output on stdout, exit code 0 if hit, 1 if miss
cache_get() {
    [[ "$GATHM_CACHE_ENABLED" != "true" ]] && return 1

    local key
    key=$(_cache_key "$@")
    local cache_file="$GATHM_CACHE_DIR/${key}"
    local meta_file="$GATHM_CACHE_DIR/${key}.meta"

    # Check if cache file exists
    [[ ! -f "$cache_file" || ! -f "$meta_file" ]] && return 1

    # Read TTL and timestamp from meta
    local cached_at ttl
    cached_at=$(grep "^cached_at=" "$meta_file" 2>/dev/null | cut -d= -f2)
    ttl=$(grep "^ttl=" "$meta_file" 2>/dev/null | cut -d= -f2)
    ttl="${ttl:-$GATHM_CACHE_DEFAULT_TTL}"

    # Check expiry
    local now
    now=$(date +%s)
    if (( now - cached_at > ttl )); then
        # Expired - clean up
        rm -f "$cache_file" "$meta_file" 2>/dev/null
        return 1
    fi

    cat "$cache_file"
    return 0
}

# Store a result in cache
# Usage: cache_set TOOL_NAME ARGS... <<< "output"
# Or:    echo "output" | cache_set TOOL_NAME ARGS...
# Optional: GATHM_CACHE_TTL env var overrides default TTL
cache_set() {
    [[ "$GATHM_CACHE_ENABLED" != "true" ]] && return 0

    local key
    key=$(_cache_key "$@")
    local cache_file="$GATHM_CACHE_DIR/${key}"
    local meta_file="$GATHM_CACHE_DIR/${key}.meta"
    local ttl="${GATHM_CACHE_TTL:-$GATHM_CACHE_DEFAULT_TTL}"

    # Read from stdin
    cat > "$cache_file" 2>/dev/null || return 1

    # Write metadata
    cat > "$meta_file" 2>/dev/null << EOF
cached_at=$(date +%s)
ttl=$ttl
tool=$1
args=${*:2}
EOF
    return 0
}

# Invalidate cache for a specific tool+args combination
# Usage: cache_invalidate TOOL_NAME [args...]
cache_invalidate() {
    local key
    key=$(_cache_key "$@")
    rm -f "$GATHM_CACHE_DIR/${key}" "$GATHM_CACHE_DIR/${key}.meta" 2>/dev/null
}

# Invalidate all cache entries for a tool
# Usage: cache_invalidate_tool TOOL_NAME
cache_invalidate_tool() {
    local tool="$1"
    for meta_file in "$GATHM_CACHE_DIR"/*.meta; do
        [[ ! -f "$meta_file" ]] && continue
        local cached_tool
        cached_tool=$(grep "^tool=" "$meta_file" 2>/dev/null | cut -d= -f2)
        if [[ "$cached_tool" == "$tool" ]]; then
            local base="${meta_file%.meta}"
            rm -f "$base" "$meta_file" 2>/dev/null
        fi
    done
}

# Clear all expired cache entries
# Usage: cache_cleanup
cache_cleanup() {
    local now cleaned=0
    now=$(date +%s)
    for meta_file in "$GATHM_CACHE_DIR"/*.meta; do
        [[ ! -f "$meta_file" ]] && continue
        local cached_at ttl
        cached_at=$(grep "^cached_at=" "$meta_file" 2>/dev/null | cut -d= -f2)
        ttl=$(grep "^ttl=" "$meta_file" 2>/dev/null | cut -d= -f2)
        ttl="${ttl:-$GATHM_CACHE_DEFAULT_TTL}"

        if (( now - cached_at > ttl )); then
            local base="${meta_file%.meta}"
            rm -f "$base" "$meta_file" 2>/dev/null
            cleaned=$((cleaned + 1))
        fi
    done
    echo "$cleaned"
}

# Get cache statistics
# Usage: cache_stats
cache_stats() {
    local total=0 expired=0 active=0 size_bytes=0
    local now
    now=$(date +%s)

    for meta_file in "$GATHM_CACHE_DIR"/*.meta; do
        [[ ! -f "$meta_file" ]] && continue
        total=$((total + 1))

        local cached_at ttl
        cached_at=$(grep "^cached_at=" "$meta_file" 2>/dev/null | cut -d= -f2)
        ttl=$(grep "^ttl=" "$meta_file" 2>/dev/null | cut -d= -f2)
        ttl="${ttl:-$GATHM_CACHE_DEFAULT_TTL}"

        if (( now - cached_at > ttl )); then
            expired=$((expired + 1))
        else
            active=$((active + 1))
            local base="${meta_file%.meta}"
            if [[ -f "$base" ]]; then
                local fsize
                fsize=$(wc -c < "$base" 2>/dev/null || echo 0)
                size_bytes=$((size_bytes + fsize))
            fi
        fi
    done

    printf '{"total_entries":%d,"active":%d,"expired":%d,"size_bytes":%d}' \
        "$total" "$active" "$expired" "$size_bytes"
}

# Execute a tool with caching - main entry point
# Usage: cached_exec TOOL_NAME [args...]
# The tool is executed if not cached; output is cached and returned
cached_exec() {
    local tool_name="$1"
    shift
    local tool_args=("$@")

    # Try cache first
    local cached_output
    cached_output=$(cache_get "$tool_name" "${tool_args[@]}")
    if [[ $? -eq 0 ]]; then
        log_debug "cache" "Cache HIT for $tool_name ${tool_args[*]}" 2>/dev/null
        echo "$cached_output"
        return 0
    fi

    log_debug "cache" "Cache MISS for $tool_name ${tool_args[*]}" 2>/dev/null

    # Execute and cache
    local tool_path
    tool_path="${SCRIPT_DIR_CACHE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)}/tools/$tool_name/$tool_name"
    local output exit_code
    output=$("$tool_path" "${tool_args[@]}" 2>&1)
    exit_code=$?

    # Only cache successful results
    if [[ $exit_code -eq 0 && -n "$output" ]]; then
        echo "$output" | cache_set "$tool_name" "${tool_args[@]}"
    fi

    echo "$output"
    return $exit_code
}

# Initialize
init_cache
