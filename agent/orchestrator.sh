#!/usr/bin/env bash
# Gathm Enterprise - AI Agent Orchestrator
# The central brain that manages tool discovery, execution, recovery, and chaining
# Cross-platform: Linux (all distros), macOS, Termux, Windows (WSL/Git Bash/MSYS2)
#
# Usage:
#   ./agent/orchestrator.sh run <tool> [args...]     # Execute a tool with full recovery
#   ./agent/orchestrator.sh plan <task_description>   # Plan a task using AI reasoning
#   ./agent/orchestrator.sh ask <natural_language>    # Natural language tool selection
#   ./agent/orchestrator.sh health [tool]             # Health check (single or all)
#   ./agent/orchestrator.sh list                      # List all available tools
#   ./agent/orchestrator.sh heal [tool]               # Self-heal a tool
#   ./agent/orchestrator.sh chain <pipeline>          # Execute a tool chain/pipeline
#   ./agent/orchestrator.sh status                    # Agent status and metrics
#   ./agent/orchestrator.sh monitor                   # Continuous health monitoring

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
GATHM_ROOT="$(cd "$AGENT_DIR/.." &>/dev/null && pwd)"

# Source all libraries
source "$GATHM_ROOT/lib/utils.bash"
source "$GATHM_ROOT/lib/logging.bash"
source "$GATHM_ROOT/lib/health.bash"
source "$GATHM_ROOT/lib/recovery.bash"
source "$GATHM_ROOT/lib/schema.bash"
source "$GATHM_ROOT/lib/cache.bash"
source "$GATHM_ROOT/lib/ratelimit.bash"

AGENT_VERSION="2.0.0"
AGENT_NAME="gathm-agent"
AGENT_STATE_DIR="${HOME}/.gathm/agent"
AGENT_MEMORY_FILE="${AGENT_STATE_DIR}/memory.json"

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

# --- Agent Initialization ---
init_agent() {
    mkdir -p "$AGENT_STATE_DIR" 2>/dev/null || true

    # Initialize memory file
    if [[ ! -f "$AGENT_MEMORY_FILE" ]]; then
        echo '{"sessions":[],"last_active":"","tool_usage":{}}' > "$AGENT_MEMORY_FILE"
    fi

    log_info "$AGENT_NAME" "Agent initialized (v$AGENT_VERSION)"
    audit_log "agent_init" "agent" "orchestrator" "version=$AGENT_VERSION"
}

# --- Tool Discovery ---
# List all available tools with their metadata (JSON)
cmd_list() {
    local output_mode="${1:-text}"

    if [[ "$output_mode" == "--json" || "$GATHM_OUTPUT_MODE" == "json" ]]; then
        list_tools_json
    else
        echo -e "${BOLD}${GREEN}Available Tools${RESETBG}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
        printf "%-15s %-12s %s\n" "TOOL" "CATEGORY" "DESCRIPTION"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"

        for dir in "$GATHM_ROOT"/tools/*/; do
            local tool_name
            tool_name=$(basename "$dir")
            if [[ ! -f "$dir/$tool_name" ]]; then continue; fi

            local desc category
            desc=$(get_tool_description "$tool_name" 2>/dev/null || echo "No description")
            category=$(get_tool_category "$tool_name" 2>/dev/null || echo "unknown")

            printf "  %-15s ${CYAN}%-12s${RESETBG} %s\n" "$tool_name" "$category" "$desc"
        done

        echo ""
        local total
        total=$(find "$GATHM_ROOT"/tools/ -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
        echo -e "${GREEN}Total: $total tools available${RESETBG}"
    fi
}

# --- Input Sanitization ---
# Validates input against blocked patterns from policies.yaml
_sanitize_input() {
    local input="$*"
    # Block command injection patterns
    if [[ "$input" == *'$('* || "$input" == *'`'* || "$input" == *'&&'* || "$input" == *'||'* || "$input" == *';'* ]]; then
        log_warn "$AGENT_NAME" "Blocked potentially dangerous input: $input"
        echo "Error: Input contains blocked characters." >&2
        return 1
    fi
    # Block path traversal
    if [[ "$input" == *'../'* ]]; then
        log_warn "$AGENT_NAME" "Blocked path traversal attempt: $input"
        echo "Error: Path traversal not allowed." >&2
        return 1
    fi
    # Check argument length
    if [[ ${#input} -gt 1024 ]]; then
        log_warn "$AGENT_NAME" "Input exceeds max argument length: ${#input}"
        echo "Error: Input too long (max 1024 characters)." >&2
        return 1
    fi
    return 0
}

# --- Tool Execution with Recovery ---
cmd_run() {
    local tool_name="$1"
    shift
    local tool_args=("$@")

    if [[ -z "$tool_name" ]]; then
        echo "Usage: $0 run <tool_name> [args...]" >&2
        return 1
    fi

    # Input sanitization
    if ! _sanitize_input "${tool_args[@]+"${tool_args[@]}"}"; then
        return 1
    fi

    local tool_path="$GATHM_ROOT/tools/$tool_name/$tool_name"
    if [[ ! -f "$tool_path" ]]; then
        log_error "$AGENT_NAME" "Tool not found: $tool_name"
        echo "Error: Tool '$tool_name' not found." >&2

        # Suggest similar tools (portable find without -exec basename)
        local suggestions=""
        for d in "$GATHM_ROOT"/tools/*"${tool_name}"*/; do
            if [[ -d "$d" ]]; then
                suggestions="$suggestions $(basename "$d")"
            fi
        done
        if [[ -n "$suggestions" ]]; then
            echo "Did you mean:$suggestions" >&2
        fi
        return 1
    fi

    # Rate limiting
    if ! ratelimit_check "$tool_name"; then
        local rl_status
        rl_status=$(ratelimit_status "$tool_name")
        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            json_response "$tool_name" "error" "" "Rate limit exceeded"
        else
            echo -e "${RED}Rate limit exceeded for '$tool_name'.${RESETBG}" >&2
            echo "Status: $rl_status" >&2
        fi
        return 1
    fi

    audit_log "tool_execute" "agent" "$tool_name" "args=${tool_args[*]}"

    # Check cache first (skip for tools that modify state)
    local skip_cache=false
    case "$tool_name" in
        todo|transfer|crypt) skip_cache=true ;;
    esac

    if [[ "$skip_cache" == "false" ]]; then
        local cached_output
        if cached_output=$(cache_get "$tool_name" "${tool_args[@]+"${tool_args[@]}"}"); then
            log_info "$AGENT_NAME" "Cache hit for $tool_name"
            echo "$cached_output"
            _update_memory "$tool_name" 0
            return 0
        fi
    fi

    # Execute with full recovery pipeline
    local output
    output=$(execute_with_recovery "$tool_name" "${tool_args[@]}")
    local exit_code=$?

    # Cache successful results
    if [[ $exit_code -eq 0 && "$skip_cache" == "false" && -n "$output" ]]; then
        echo "$output" | cache_set "$tool_name" "${tool_args[@]+"${tool_args[@]}"}"
    fi

    echo "$output"

    # Update agent memory
    _update_memory "$tool_name" "$exit_code"

    return $exit_code
}

# --- Natural Language Interface ---
# Intent matching using Bash 3 compatible approach (no associative arrays)
# Returns: tool name on stdout, or empty
_match_intent() {
    local query_lower="$1"
    local best_match=""
    local best_score=0
    local normalized_query
    normalized_query=$(echo "$query_lower" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/ /g' | tr -s ' ' | sed 's/^ //; s/ $//')

    # Each line: "patterns|tool_name"
    # Patterns within a group separated by spaces
    local intents="
weather forecast temperature rain snow climate|weather
ip address wan lan geolocation where_am_i my_ip|geo
dns mx txt cname aaaa ns doh resolve|dns
rdap registrar registrant domain_owner nameserver registration_data|rdap
rdns reverse_dns ptr reverse_lookup ip_to_domain|rdns
whois whois_lookup registrant_contact registrar_lookup ownership_lookup|whois
certificate cert tls ssl expiry issuer san|certinfo
cve cvss advisory vuln vulnerability cwe|cve
http https headers status response endpoint uptime|httpprobe
dnssec dnskey ds rrsig signed_zone chain_of_trust|dnssec
asn bgp prefix prefixes autonomous_system route routing|asn
urlscan phishing suspicious_url malware_url reputation domain_intel|urlscan
waf firewall cloudflare akamai incapsula sucuri bot_protection wafdetect|wafdetect
subdomain subdomains crt certificate_transparency enumerate_hosts|subdomains
robots robots_txt crawl_directives sitemap crawler bot_rules|robotsaudit
security_headers hsts csp x_frame_options referrer_policy headers_audit|headersaudit
subfinder passive_subdomain projectdiscovery_subfinder|subfinder
dnsx dns_probe mass_dns projectdiscovery_dnsx|dnsx
httpx web_probe banner_grab projectdiscovery_httpx|httpx
naabu port_scan fast_port_scan projectdiscovery_naabu|naabu
katana web_crawler endpoint_crawler projectdiscovery_katana|katana
nuclei template_scan vuln_templates projectdiscovery_nuclei|nuclei
uncover search_engine_discovery internet_asset_discovery projectdiscovery_uncover|uncover
pdchain recon_chain projectdiscovery_chain recon_workflow|pdchain
strix strix_tool strix_runner|strix
portscan tcp_scan udp_scan port_range service_discovery nmap|portscan
maltego graph_investigation entity_graph transforms|maltego
tipcheck threat_intel virustotal abuseipdb ioc_reputation|tipcheck
imganalyze image_analysis ocr object_detection image_forensics|imganalyze
search google find_online look_up_online|googler
movie film cinema imdb rating|movie
define meaning definition dictionary|define
encrypt decrypt cipher encode decode base64|cipher
file_encrypt file_decrypt aes openssl|crypt
breach pwned compromised leaked data_breach password_leak|pwned
scan shodan port device vulnerability iot|shodan
qr qrcode qr_code|qrify
shorten expand short_url tiny_url bitly|shorturl
upload download transfer share_file send_file|transfer
bitcoin ethereum crypto btc eth coin exchange_rate|cryptocurrency
news headline current_event latest|news
lyrics song_text song_words|lyrics
cheat cheatsheet reference how_to syntax|cheat
todo task remind checklist to_do|todo
meme funny joke_image meme_gen|meme
gif animated giphy|gif
covid corona pandemic virus|covidinfo
music play jukebox listen|jukebox
ip_info ip_detail ip_lookup|ipinfo
"

    echo "$intents" | while IFS='|' read -r patterns tool; do
        # Skip empty lines
        [[ -z "$tool" ]] && continue
        tool=$(echo "$tool" | tr -d ' ')

        local score=0
        for p in $patterns; do
            # Convert underscores to spaces for matching
            local match_pattern
            match_pattern=$(echo "$p" | tr '_' ' ')
            if [[ " $normalized_query " == *" $match_pattern "* ]]; then
                score=$((score + 1))
            fi
        done

        if [ "$score" -gt 0 ]; then
            echo "$score $tool"
        fi
    done | sort -rn | head -1
}

cmd_ask() {
    local query="$*"

    if [[ -z "$query" ]]; then
        echo "Usage: $0 ask <natural language query>" >&2
        return 1
    fi

    log_info "$AGENT_NAME" "Processing query: $query"
    audit_log "agent_query" "agent" "orchestrator" "query=$query"

    local query_lower
    query_lower=$(echo "$query" | tr '[:upper:]' '[:lower:]')

    # Match query against intent patterns (Bash 3 compatible)
    local match_result
    match_result=$(_match_intent "$query_lower")

    local best_score best_match
    best_score=$(echo "$match_result" | awk '{print $1}')
    best_match=$(echo "$match_result" | awk '{print $2}')

    if [[ -n "$best_match" && "${best_score:-0}" -gt 0 ]]; then
        local tool_desc
        tool_desc=$(get_tool_description "$best_match" 2>/dev/null || echo "")

        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            json_object \
                "query" "$query" \
                "matched_tool" "$best_match" \
                "confidence" "$best_score" \
                "description" "$tool_desc" \
                "action" "Use: gathm-agent run $best_match"
        else
            echo -e "${GREEN}Matched Tool:${RESETBG} $best_match"
            echo -e "${BLUE}Description:${RESETBG} $tool_desc"
            echo -e "${BLUE}Confidence:${RESETBG} $best_score match(es)"
            echo ""
            echo -e "Run: ${BOLD}gathm-agent run $best_match${RESETBG}"
            echo ""

            # Extract potential arguments from query
            _suggest_args "$best_match" "$query"
        fi

        log_info "$AGENT_NAME" "Query matched to tool: $best_match (confidence: $best_score)"
    else
        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            json_object "query" "$query" "matched_tool" "null" "confidence" "0" "error" "No matching tool found"
        else
            echo -e "${ORANGE}No matching tool found for: $query${RESETBG}"
            echo ""
            echo "Available capabilities:"
            cmd_list
        fi
        log_warn "$AGENT_NAME" "No tool match for query: $query"
    fi
}

# Suggest arguments based on query context
_suggest_args() {
    local tool="$1"
    local query="$2"

    case "$tool" in
        weather)
            local location
            location=$(echo "$query" | sed -E 's/.*(weather|forecast|temperature) (in |for |at )?//i' | sed 's/[?.!]$//')
            if [[ -n "$location" && "$location" != "$query" ]]; then
                echo -e "${CYAN}Suggested command:${RESETBG} gathm-agent run weather $location"
            fi
            ;;
        define)
            local word
            word=$(echo "$query" | sed -E 's/.*(define|meaning of|definition of) //i' | sed 's/[?.!]$//' | awk '{print $1}')
            if [[ -n "$word" ]]; then
                echo -e "${CYAN}Suggested command:${RESETBG} gathm-agent run define $word"
            fi
            ;;
        movie)
            local title
            title=$(echo "$query" | sed -E 's/.*(movie|film|about) //i' | sed 's/[?.!]$//')
            if [[ -n "$title" && "$title" != "$query" ]]; then
                echo -e "${CYAN}Suggested command:${RESETBG} gathm-agent run movie $title"
            fi
            ;;
        pwned)
            local email
            email=$(echo "$query" | grep -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
            if [[ -n "$email" ]]; then
                echo -e "${CYAN}Suggested command:${RESETBG} gathm-agent run pwned $email"
            fi
            ;;
    esac
}

# --- Health Commands ---
cmd_health() {
    local tool="${1:-all}"

    if [[ "$tool" == "all" ]]; then
        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            healthcheck_all
        else
            echo -e "${BOLD}${GREEN}System Health Report${RESETBG}"
            echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"

            local healthy=0 degraded=0 unhealthy=0

            for dir in "$GATHM_ROOT"/tools/*/; do
                local name
                name=$(basename "$dir")
                if [[ ! -f "$dir/$name" ]]; then continue; fi

                local result
                result=$(healthcheck_tool "$name")
                local status
                status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

                local icon
                case "$status" in
                    healthy)   icon="${GREEN}[OK]${RESETBG}"; healthy=$((healthy + 1)) ;;
                    degraded)  icon="${ORANGE}[!!]${RESETBG}"; degraded=$((degraded + 1)) ;;
                    unhealthy) icon="${RED}[XX]${RESETBG}"; unhealthy=$((unhealthy + 1)) ;;
                    *)         icon="${RED}[??]${RESETBG}"; unhealthy=$((unhealthy + 1)) ;;
                esac

                # Show circuit breaker state
                local cb_state
                cb_state=$(cb_get_state "$name")
                local cb_icon=""
                if [[ "$cb_state" != "closed" ]]; then
                    cb_icon=" ${RED}[CB:$cb_state]${RESETBG}"
                fi

                printf "  %s %-15s %s%s\n" "$icon" "$name" "$status" "$cb_icon"
            done

            echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
            echo -e "  ${GREEN}Healthy: $healthy${RESETBG} | ${ORANGE}Degraded: $degraded${RESETBG} | ${RED}Unhealthy: $unhealthy${RESETBG}"
        fi
    else
        local result
        result=$(healthcheck_tool "$tool")
        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            echo "$result"
        else
            echo -e "${BOLD}Health: $tool${RESETBG}"
            # Cross-platform JSON pretty-print
            local py
            py=$(_find_python)
            if [[ -n "$py" ]]; then
                echo "$result" | "$py" -m json.tool 2>/dev/null || echo "$result"
            elif command -v jq &>/dev/null; then
                echo "$result" | jq . 2>/dev/null || echo "$result"
            else
                echo "$result"
            fi
        fi
    fi
}

# --- Self-Heal Command ---
cmd_heal() {
    local tool="${1:-all}"

    if [[ "$tool" == "all" ]]; then
        echo -e "${BOLD}${GREEN}Running self-heal on all tools...${RESETBG}"
        for dir in "$GATHM_ROOT"/tools/*/; do
            local name
            name=$(basename "$dir")
            if [[ ! -f "$dir/$name" ]]; then continue; fi
            self_heal "$name"
        done
        echo -e "${GREEN}Self-heal complete.${RESETBG}"
    else
        self_heal "$tool"
    fi
}

# --- Tool Chaining / Pipeline ---
cmd_chain() {
    local pipeline="$*"

    if [[ -z "$pipeline" ]]; then
        echo "Usage: $0 chain '<tool1> [args] | <tool2> [args] | ...'" >&2
        echo "Example: $0 chain 'geo -w | ipinfo'" >&2
        return 1
    fi

    log_info "$AGENT_NAME" "Executing pipeline: $pipeline"
    audit_log "pipeline_execute" "agent" "orchestrator" "pipeline=$pipeline"

    local prev_output=""
    local step=0
    local total_steps
    total_steps=$(echo "$pipeline" | tr '|' '\n' | wc -l)

    echo -e "${BOLD}${GREEN}Executing Pipeline ($total_steps steps)${RESETBG}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"

    # Split pipeline by | (portable, no read -ra needed)
    local IFS_OLD="$IFS"
    IFS='|'
    set -f  # Disable globbing
    # shellcheck disable=SC2086
    set -- $pipeline
    set +f
    IFS="$IFS_OLD"

    for step_cmd in "$@"; do
        step=$((step + 1))

        # Trim whitespace (portable)
        step_cmd=$(echo "$step_cmd" | sed 's/^ *//;s/ *$//')

        # Extract tool name and args
        local tool_name
        tool_name=$(echo "$step_cmd" | awk '{print $1}')
        local tool_args
        tool_args=$(echo "$step_cmd" | sed "s/^$tool_name//;s/^ *//")

        # If there's previous output, append it as argument
        if [[ -n "$prev_output" && -n "$tool_args" ]]; then
            tool_args="$tool_args $prev_output"
        elif [[ -n "$prev_output" ]]; then
            tool_args="$prev_output"
        fi

        echo -e "\n${CYAN}Step $step/$total_steps:${RESETBG} $tool_name $tool_args"
        echo "────────────────────────────────────"

        # Execute tool
        local tool_path="$GATHM_ROOT/tools/$tool_name/$tool_name"
        if [[ ! -f "$tool_path" ]]; then
            echo -e "${RED}Error: Tool '$tool_name' not found in pipeline step $step${RESETBG}" >&2
            return 1
        fi

        # shellcheck disable=SC2086
        prev_output=$(execute_with_recovery "$tool_name" $tool_args 2>&1)
        local exit_code=$?

        if [[ $exit_code -ne 0 ]]; then
            echo -e "${RED}Pipeline failed at step $step ($tool_name)${RESETBG}" >&2
            echo "$prev_output"
            log_error "$AGENT_NAME" "Pipeline failed at step $step: $tool_name"
            return 1
        fi

        echo "$prev_output"
    done

    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
    echo -e "${GREEN}Pipeline completed successfully ($total_steps steps)${RESETBG}"

    log_info "$AGENT_NAME" "Pipeline completed: $total_steps steps"
}

# --- Agent Status ---
cmd_status() {
    if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
        local tools_count
        tools_count=$(find "$GATHM_ROOT"/tools/ -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
        json_object \
            "agent" "$AGENT_NAME" \
            "version" "$AGENT_VERSION" \
            "tools_available" "$tools_count" \
            "platform" "$(uname -s 2>/dev/null || echo unknown)" \
            "state_dir" "$AGENT_STATE_DIR" \
            "log_dir" "$GATHM_LOG_DIR"
    else
        echo -e "${BOLD}${GREEN}Gathm Agent Status${RESETBG}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
        echo -e "  Agent:     ${CYAN}$AGENT_NAME v$AGENT_VERSION${RESETBG}"
        echo -e "  Platform:  $(detect_platform) ($(uname -s 2>/dev/null || echo unknown))"
        echo -e "  Shell:     ${BASH_VERSION:-unknown}"
        echo -e "  Tools:     $(find "$GATHM_ROOT"/tools/ -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) available"
        echo -e "  State Dir: $AGENT_STATE_DIR"
        echo -e "  Log Dir:   $GATHM_LOG_DIR"
        echo ""

        # Show recent metrics summary
        if [[ -f "$GATHM_METRICS_FILE" ]]; then
            local total_invocations
            total_invocations=$(wc -l < "$GATHM_METRICS_FILE" 2>/dev/null || echo 0)
            local successes
            successes=$(grep -c '"status":"success"' "$GATHM_METRICS_FILE" 2>/dev/null || echo 0)
            local failures
            failures=$(grep -c '"status":"failure"' "$GATHM_METRICS_FILE" 2>/dev/null || echo 0)

            echo -e "  ${BOLD}Metrics:${RESETBG}"
            echo -e "    Total Invocations: $total_invocations"
            echo -e "    Successes: ${GREEN}$successes${RESETBG}"
            echo -e "    Failures:  ${RED}$failures${RESETBG}"
            if [ "$total_invocations" -gt 0 ] 2>/dev/null; then
                local success_rate=$((successes * 100 / total_invocations))
                echo -e "    Success Rate: ${success_rate}%"
            fi
        fi

        echo ""

        # Show open circuit breakers
        local open_circuits=0
        for state_file in "$GATHM_HEALTH_DIR"/cb_*.state; do
            if [[ -f "$state_file" ]]; then
                local state
                state=$(grep "^state=" "$state_file" | cut -d= -f2)
                if [[ "$state" == "open" ]]; then
                    local tool_from_file
                    tool_from_file=$(basename "$state_file" | sed 's/^cb_//;s/\.state$//')
                    echo -e "  ${RED}Circuit OPEN:${RESETBG} $tool_from_file"
                    open_circuits=$((open_circuits + 1))
                fi
            fi
        done
        if [ "$open_circuits" -eq 0 ]; then
            echo -e "  ${GREEN}All circuits closed (healthy)${RESETBG}"
        fi

        echo ""

        # Show cache stats
        local cache_data
        cache_data=$(cache_stats 2>/dev/null)
        if [[ -n "$cache_data" ]]; then
            echo -e "  ${BOLD}Cache:${RESETBG}"
            local active expired
            active=$(echo "$cache_data" | grep -o '"active":[0-9]*' | cut -d: -f2)
            expired=$(echo "$cache_data" | grep -o '"expired":[0-9]*' | cut -d: -f2)
            echo -e "    Active entries: ${GREEN}${active:-0}${RESETBG}"
            echo -e "    Expired entries: ${ORANGE}${expired:-0}${RESETBG}"
        fi
    fi
}

# --- Engineering Agent ---
cmd_engineer() {
    source "$GATHM_ROOT/engineer/engineer.sh"
    cmd_engineer "$@"
}

# --- Continuous Monitoring ---
cmd_monitor() {
    local interval="${1:-30}"
    echo -e "${BOLD}${GREEN}Starting continuous health monitoring (interval: ${interval}s)${RESETBG}"
    echo "Press Ctrl+C to stop."
    echo ""

    while true; do
        echo -e "\n${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] Health Check${RESETBG}"
        cmd_health all

        # Auto-heal unhealthy tools
        for dir in "$GATHM_ROOT"/tools/*/; do
            local name
            name=$(basename "$dir")
            if [[ ! -f "$dir/$name" ]]; then continue; fi

            local result
            result=$(healthcheck_tool "$name")
            local status
            status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

            if [[ "$status" == "unhealthy" ]]; then
                echo -e "${ORANGE}Auto-healing: $name${RESETBG}"
                self_heal "$name"
            fi
        done

        sleep "$interval"
    done
}

# --- Tool Scaffolding ---
cmd_new_tool() {
    local tool_name="$1"

    if [[ -z "$tool_name" ]]; then
        echo "Usage: $0 new-tool <tool_name> [--category <cat>] [--description <desc>]" >&2
        return 1
    fi

    # Parse optional flags
    shift
    local category="utility" description="A new gathm tool"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --category) category="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    local tool_dir="$GATHM_ROOT/tools/$tool_name"
    if [[ -d "$tool_dir" ]]; then
        echo "Error: Tool '$tool_name' already exists at $tool_dir" >&2
        return 1
    fi

    mkdir -p "$tool_dir"

    # Create the tool executable
    cat > "$tool_dir/$tool_name" << 'TOOL_TEMPLATE'
#!/usr/bin/env bash
# Gathm Tool: TOOL_NAME_PLACEHOLDER
# Description: DESCRIPTION_PLACEHOLDER

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
source "$TOOL_DIR/../../lib/utils.bash"
getConfiguredClient

TOOL_VERSION="1.0.0"

show_help() {
    echo "Usage: TOOL_NAME_PLACEHOLDER [options] <arguments>"
    echo ""
    echo "DESCRIPTION_PLACEHOLDER"
    echo ""
    echo "Options:"
    echo "  -h, --help     Show this help"
    echo "  -v, --version  Show version"
    echo "  --json         Output in JSON format"
}

main() {
    case "${1:-}" in
        -h|--help) show_help; return 0 ;;
        -v|--version) echo "TOOL_NAME_PLACEHOLDER $TOOL_VERSION"; return 0 ;;
    esac

    # Parse --json flag
    local output_mode="text"
    local args=()
    for arg in "$@"; do
        case "$arg" in
            --json) output_mode="json" ;;
            *) args+=("$arg") ;;
        esac
    done

    # TODO: Implement your tool logic here
    local result="Hello from TOOL_NAME_PLACEHOLDER!"

    if [[ "$output_mode" == "json" ]]; then
        gathm_output "TOOL_NAME_PLACEHOLDER" "" "$(json_object "result" "$result")"
    else
        echo "$result"
    fi
}

main "$@"
TOOL_TEMPLATE

    # Replace placeholders
    sed -i "s/TOOL_NAME_PLACEHOLDER/$tool_name/g" "$tool_dir/$tool_name" 2>/dev/null || \
        sed -i '' "s/TOOL_NAME_PLACEHOLDER/$tool_name/g" "$tool_dir/$tool_name"
    sed -i "s/DESCRIPTION_PLACEHOLDER/$description/g" "$tool_dir/$tool_name" 2>/dev/null || \
        sed -i '' "s/DESCRIPTION_PLACEHOLDER/$description/g" "$tool_dir/$tool_name"

    chmod +x "$tool_dir/$tool_name"

    # Create tool.yaml manifest
    cat > "$tool_dir/tool.yaml" << YAML_TEMPLATE
name: $tool_name
version: "1.0.0"
description: "$description"
category: $category

dependencies:
  system: [curl]

input_schema:
  arguments:
    - name: input
      type: string
      required: true
      description: "Primary input for $tool_name"
  flags:
    - flag: json
      description: "Output in JSON format"

output_schema:
  text: "Human-readable output"
  json_fields:
    - name: result
      type: string

fallback_tool: null
tags: [$category, $tool_name]
YAML_TEMPLATE

    echo -e "${GREEN}Tool '$tool_name' created successfully!${RESETBG}"
    echo -e "  Directory:  $tool_dir"
    echo -e "  Executable: $tool_dir/$tool_name"
    echo -e "  Manifest:   $tool_dir/tool.yaml"
    echo ""
    echo -e "Next steps:"
    echo -e "  1. Edit ${CYAN}$tool_dir/$tool_name${RESETBG} to implement your logic"
    echo -e "  2. Update ${CYAN}$tool_dir/tool.yaml${RESETBG} with correct dependencies"
    echo -e "  3. Test: ${BOLD}gathm-agent run $tool_name --help${RESETBG}"

    log_info "$AGENT_NAME" "Created new tool: $tool_name (category: $category)"
    audit_log "tool_create" "agent" "$tool_name" "category=$category"
}

# --- Parallel Tool Execution ---
cmd_parallel() {
    local tools_spec="$*"

    if [[ -z "$tools_spec" ]]; then
        echo "Usage: $0 parallel '<tool1> [args], <tool2> [args], ...'" >&2
        echo "Example: $0 parallel 'weather Paris, news, cryptocurrency'" >&2
        return 1
    fi

    log_info "$AGENT_NAME" "Parallel execution: $tools_spec"
    audit_log "parallel_execute" "agent" "orchestrator" "tools=$tools_spec"

    # Split by comma
    local IFS_OLD="$IFS"
    IFS=','
    local tool_cmds=()
    # shellcheck disable=SC2086
    for cmd in $tools_spec; do
        cmd=$(echo "$cmd" | sed 's/^ *//;s/ *$//')
        [[ -n "$cmd" ]] && tool_cmds+=("$cmd")
    done
    IFS="$IFS_OLD"

    local total=${#tool_cmds[@]}
    echo -e "${BOLD}${GREEN}Parallel Execution ($total tools)${RESETBG}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"

    # Launch all tools in background
    local tmpdir
    tmpdir=$(mktemp -d 2>/dev/null || mktemp -d -t gathm_parallel)
    local pids=()

    for i in "${!tool_cmds[@]}"; do
        local cmd="${tool_cmds[$i]}"
        local tool_name
        tool_name=$(echo "$cmd" | awk '{print $1}')
        local tool_args
        tool_args=$(echo "$cmd" | sed "s/^$tool_name//;s/^ *//")

        local tool_path="$GATHM_ROOT/tools/$tool_name/$tool_name"
        if [[ ! -f "$tool_path" ]]; then
            echo "Error: Tool '$tool_name' not found" > "$tmpdir/result_$i"
            continue
        fi

        (
            # shellcheck disable=SC2086
            execute_with_recovery "$tool_name" $tool_args > "$tmpdir/result_$i" 2>&1
            echo $? > "$tmpdir/exit_$i"
        ) &
        pids+=($!)
        echo -e "  ${CYAN}Started:${RESETBG} $tool_name $tool_args (PID: $!)"
    done

    # Wait for all to complete
    local failed=0
    for i in "${!pids[@]}"; do
        wait "${pids[$i]}" 2>/dev/null || true
    done

    echo ""

    # Display results
    for i in "${!tool_cmds[@]}"; do
        local cmd="${tool_cmds[$i]}"
        local tool_name
        tool_name=$(echo "$cmd" | awk '{print $1}')
        local exit_code=0
        [[ -f "$tmpdir/exit_$i" ]] && exit_code=$(cat "$tmpdir/exit_$i")

        local status_icon="${GREEN}[OK]${RESETBG}"
        if [[ "$exit_code" -ne 0 ]]; then
            status_icon="${RED}[FAIL]${RESETBG}"
            failed=$((failed + 1))
        fi

        echo -e "\n$status_icon ${BOLD}$tool_name${RESETBG}"
        echo "────────────────────────────────────"
        [[ -f "$tmpdir/result_$i" ]] && cat "$tmpdir/result_$i"
    done

    # Cleanup
    rm -rf "$tmpdir" 2>/dev/null

    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
    echo -e "${GREEN}Parallel execution completed: $total tools, $failed failed${RESETBG}"

    log_info "$AGENT_NAME" "Parallel execution: $total tools, $failed failed"
    [[ $failed -gt 0 ]] && return 1
    return 0
}

# --- Cache Management ---
cmd_cache() {
    local subcmd="${1:-stats}"

    case "$subcmd" in
        stats)
            local stats
            stats=$(cache_stats)
            if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
                echo "$stats"
            else
                echo -e "${BOLD}${GREEN}Cache Statistics${RESETBG}"
                echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
                local py
                py=$(_find_python)
                if [[ -n "$py" ]]; then
                    echo "$stats" | "$py" -m json.tool 2>/dev/null || echo "$stats"
                else
                    echo "$stats"
                fi
            fi
            ;;
        clear)
            local cleaned
            cleaned=$(cache_cleanup)
            echo -e "${GREEN}Cleaned $cleaned expired cache entries.${RESETBG}"
            ;;
        purge)
            rm -rf "$GATHM_CACHE_DIR"/* 2>/dev/null
            echo -e "${GREEN}Cache purged.${RESETBG}"
            ;;
        invalidate)
            local tool="${2:-}"
            if [[ -z "$tool" ]]; then
                echo "Usage: $0 cache invalidate <tool_name>" >&2
                return 1
            fi
            cache_invalidate_tool "$tool"
            echo -e "${GREEN}Cache invalidated for '$tool'.${RESETBG}"
            ;;
        *)
            echo "Usage: $0 cache [stats|clear|purge|invalidate <tool>]" >&2
            return 1
            ;;
    esac
}

# --- Agent Memory ---
_update_memory() {
    local tool="$1"
    local exit_code="$2"
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    local memory_entry
    memory_entry=$(printf '{"tool":"%s","exit_code":%d,"timestamp":"%s"}' "$tool" "$exit_code" "$timestamp")

    echo "$memory_entry" >> "${AGENT_STATE_DIR}/history.jsonl" 2>/dev/null || true
}

# --- Help ---
show_help() {
    cat << EOF
${BOLD}Gathm AI Agent Orchestrator v$AGENT_VERSION${RESETBG}

${GREEN}Usage:${RESETBG} gathm-agent <command> [options]

${BOLD}Commands:${RESETBG}
  run <tool> [args]        Execute a tool with recovery, caching, and rate limiting
  ask <query>              Natural language tool selection and execution
  plan <description>       Decompose a task into a tool execution plan
  engineer <task>          AI engineering agent for code and tool development
  chain '<t1 | t2 | t3>'  Execute a tool pipeline (pipe output between tools)
  parallel '<t1, t2, t3>'  Execute multiple tools in parallel
  new-tool <name> [opts]   Scaffold a new tool with boilerplate and manifest
  list [--json]            List all available tools with metadata
  health [tool|all]        Run health checks on tools
  heal [tool|all]          Self-heal broken tools (fix deps, permissions, etc.)
  cache [stats|clear|purge|invalidate <tool>]  Manage output cache
  status                   Show agent status, metrics, and circuit breaker states
  monitor [interval]       Continuous health monitoring with auto-heal

${BOLD}Options:${RESETBG}
  --json                   Output in JSON format (for programmatic access)
  -h, --help               Show this help message
  -v, --version            Show agent version

${BOLD}Platforms:${RESETBG}
  Linux (all distros), macOS, Termux (Android), Windows (WSL/Git Bash/MSYS2)

${BOLD}Examples:${RESETBG}
  gathm-agent run weather Paris
  gathm-agent ask "what's the weather in Tokyo?"
  gathm-agent chain 'geo -w | ipinfo'
  gathm-agent parallel 'weather Paris, news, cryptocurrency'
  gathm-agent new-tool mytool --category security --description "My custom tool"
  gathm-agent health all
  gathm-agent heal weather
  gathm-agent cache stats
  gathm-agent list --json
  gathm-agent monitor 60

${BOLD}Environment:${RESETBG}
  GATHM_LOG_LEVEL       Log level (DEBUG|INFO|WARN|ERROR) [default: INFO]
  GATHM_OUTPUT_MODE     Output mode (text|json) [default: text]
  GATHM_MAX_RETRIES     Max retry attempts [default: 3]
  GATHM_CACHE_ENABLED   Enable output caching [default: true]
  GATHM_CACHE_DEFAULT_TTL  Cache TTL in seconds [default: 300]
EOF
}

# --- Main ---
init_agent

# Parse global flags
for arg in "$@"; do
    case "$arg" in
        --json) GATHM_OUTPUT_MODE="json" ;;
    esac
done

# Remove --json from args for subcommands
args=()
for arg in "$@"; do
    if [[ "$arg" != "--json" ]]; then
        args+=("$arg")
    fi
done
set -- "${args[@]+"${args[@]}"}"

command="${1:-help}"
shift 2>/dev/null || true

case "$command" in
    run)       cmd_run "$@" ;;
    ask)       cmd_ask "$@" ;;
    plan)      source "$AGENT_DIR/planner.sh"; cmd_plan "$@" ;;
    engineer)  cmd_engineer "$@" ;;
    chain)     cmd_chain "$@" ;;
    parallel)  cmd_parallel "$@" ;;
    new-tool)  cmd_new_tool "$@" ;;
    list)      cmd_list "$@" ;;
    health)    cmd_health "$@" ;;
    heal)      cmd_heal "$@" ;;
    cache)     cmd_cache "$@" ;;
    status)    cmd_status ;;
    monitor)   cmd_monitor "$@" ;;
    -h|--help|help) show_help ;;
    -v|--version)   echo "gathm-agent v$AGENT_VERSION" ;;
    *)
        echo "Unknown command: $command" >&2
        echo "Run 'gathm-agent --help' for usage." >&2
        exit 1
        ;;
esac
