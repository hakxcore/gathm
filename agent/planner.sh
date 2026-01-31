#!/usr/bin/env bash
# Gathm Enterprise - AI Agent Task Planner
# Decomposes complex tasks into step-by-step tool execution plans
# Cross-platform: Linux (all distros), macOS, Termux, Windows (WSL/Git Bash/MSYS2)

PLANNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
GATHM_ROOT="${GATHM_ROOT:-$(cd "$PLANNER_DIR/.." &>/dev/null && pwd)}"

# Task plan storage
PLANS_DIR="${HOME}/.gathm/agent/plans"
mkdir -p "$PLANS_DIR" 2>/dev/null || true

# --- Plan Templates (Bash 3 compatible - no associative arrays) ---
_get_plan_template() {
    local key="$1"
    case "$key" in
        security_audit)
            echo '[
  {"step":1,"tool":"geo","args":"-w","description":"Get current WAN IP"},
  {"step":2,"tool":"geo","args":"-g","description":"Get geolocation data"},
  {"step":3,"tool":"pwned","args":"__EMAIL__","description":"Check for data breaches"},
  {"step":4,"tool":"shodan","args":"__IP__","description":"Scan for exposed services"}
]'
            ;;
        network_recon)
            echo '[
  {"step":1,"tool":"geo","args":"-wlrd","description":"Get all network info (WAN, LAN, Router, DNS)"},
  {"step":2,"tool":"geo","args":"-g","description":"Geolocate current IP"},
  {"step":3,"tool":"ipinfo","args":"","description":"Get detailed IP information"}
]'
            ;;
        daily_briefing)
            echo '[
  {"step":1,"tool":"weather","args":"","description":"Get local weather forecast"},
  {"step":2,"tool":"news","args":"","description":"Get latest headlines"},
  {"step":3,"tool":"cryptocurrency","args":"","description":"Check crypto prices"},
  {"step":4,"tool":"todo","args":"list","description":"Show pending tasks"}
]'
            ;;
        encrypt_and_transfer)
            echo '[
  {"step":1,"tool":"crypt","args":"-e __FILE__","description":"Encrypt the file"},
  {"step":2,"tool":"transfer","args":"__FILE__.enc","description":"Upload encrypted file"},
  {"step":3,"tool":"qrify","args":"__URL__","description":"Generate QR code for download link"}
]'
            ;;
        ip_investigation)
            echo '[
  {"step":1,"tool":"geo","args":"-a __IP__ -o city,country,isp,org","description":"Geolocate the target IP"},
  {"step":2,"tool":"ipinfo","args":"__IP__","description":"Get detailed IP info"},
  {"step":3,"tool":"shodan","args":"__IP__","description":"Scan for services and vulnerabilities"}
]'
            ;;
        *)
            echo ""
            ;;
    esac
}

# --- Task Decomposition ---
cmd_plan() {
    local task_description="$*"

    if [[ -z "$task_description" ]]; then
        echo "Usage: gathm-agent plan <task description>" >&2
        echo "" >&2
        echo "Examples:" >&2
        echo "  gathm-agent plan 'run a security audit on my network'" >&2
        echo "  gathm-agent plan 'get my daily briefing'" >&2
        echo "  gathm-agent plan 'investigate IP 8.8.8.8'" >&2
        echo "  gathm-agent plan 'encrypt and share file secret.txt'" >&2
        return 1
    fi

    log_info "planner" "Planning task: $task_description"
    audit_log "task_plan" "agent" "planner" "task=$task_description"

    local task_lower
    task_lower=$(echo "$task_description" | tr '[:upper:]' '[:lower:]')

    # Match against plan templates
    local matched_template=""
    local plan_name=""
    local plan_vars=""

    if echo "$task_lower" | grep -qE "security audit|security scan|audit"; then
        matched_template="security_audit"
        plan_name="Security Audit"
        local email
        email=$(echo "$task_description" | grep -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' || echo "")
        plan_vars="__EMAIL__=${email:-user@example.com}"
    elif echo "$task_lower" | grep -qE "network recon|network info|network scan"; then
        matched_template="network_recon"
        plan_name="Network Reconnaissance"
    elif echo "$task_lower" | grep -qE "daily briefing|morning brief|daily report|daily summary"; then
        matched_template="daily_briefing"
        plan_name="Daily Briefing"
    elif echo "$task_lower" | grep -qE "encrypt.*transfer|encrypt.*share|encrypt.*send|encrypt.*upload"; then
        matched_template="encrypt_and_transfer"
        plan_name="Encrypt & Transfer"
        local file
        file=$(echo "$task_description" | grep -oE '\S+\.\S+' | head -1 || echo "")
        plan_vars="__FILE__=${file:-file.txt}"
    elif echo "$task_lower" | grep -qE "investigate.*ip|lookup.*ip|trace.*ip|scan.*ip"; then
        matched_template="ip_investigation"
        plan_name="IP Investigation"
        local ip
        ip=$(echo "$task_description" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || echo "")
        plan_vars="__IP__=${ip:-8.8.8.8}"
    fi

    if [[ -n "$matched_template" ]]; then
        _display_plan "$plan_name" "$matched_template" "$plan_vars"
    else
        _generate_dynamic_plan "$task_description"
    fi
}

# Display a pre-defined plan
_display_plan() {
    local plan_name="$1"
    local template_key="$2"
    local vars="$3"

    local plan
    plan=$(_get_plan_template "$template_key")

    # Substitute variables
    if [[ -n "$vars" ]]; then
        local key="${vars%%=*}"
        local value="${vars#*=}"
        plan="${plan//$key/$value}"
    fi

    # Save plan
    local plan_id
    plan_id=$(date +%s)
    local plan_file="$PLANS_DIR/plan_${plan_id}.json"
    echo "$plan" > "$plan_file"

    if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
        printf '{"plan_id":"%s","plan_name":"%s","steps":%s}' "$plan_id" "$plan_name" "$plan"
    else
        echo -e "${BOLD}${GREEN}Execution Plan: $plan_name${RESETBG}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
        echo -e "  Plan ID: ${CYAN}$plan_id${RESETBG}"
        echo ""

        # Parse and display steps
        local step=0
        while IFS= read -r line; do
            local s_tool s_args s_desc
            s_tool=$(echo "$line" | grep -o '"tool":"[^"]*"' | cut -d'"' -f4)
            s_args=$(echo "$line" | grep -o '"args":"[^"]*"' | cut -d'"' -f4)
            s_desc=$(echo "$line" | grep -o '"description":"[^"]*"' | cut -d'"' -f4)

            if [[ -n "$s_tool" ]]; then
                step=$((step + 1))
                local health
                health=$(healthcheck_tool "$s_tool" 2>/dev/null)
                local status
                status=$(echo "$health" | grep -o '"status":"[^"]*"' | cut -d'"' -f4 2>/dev/null || echo "unknown")
                local status_icon
                case "$status" in
                    healthy) status_icon="${GREEN}[OK]${RESETBG}" ;;
                    degraded) status_icon="${ORANGE}[!!]${RESETBG}" ;;
                    *) status_icon="${RED}[??]${RESETBG}" ;;
                esac

                echo -e "  ${BOLD}Step $step:${RESETBG} $s_desc"
                echo -e "    Tool: ${CYAN}$s_tool${RESETBG} $s_args  $status_icon"
                echo ""
            fi
        done <<< "$(echo "$plan" | tr ',' '\n')"

        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
        echo ""
        echo -e "Execute this plan: ${BOLD}gathm-agent execute-plan $plan_id${RESETBG}"
        echo -e "Or run step by step with: ${BOLD}gathm-agent run <tool> <args>${RESETBG}"
    fi
}

# Generate a dynamic plan from keyword analysis (Bash 3 compatible)
_generate_dynamic_plan() {
    local task="$1"
    local task_lower
    task_lower=$(echo "$task" | tr '[:upper:]' '[:lower:]')
    local steps=()
    local step=0

    # Keyword-to-tool mapping using simple string matching (no associative arrays)
    # Format: "keyword:tool" pairs
    local keyword_tools="
weather:weather
forecast:weather
ip:geo
location:geo
geolocation:geo
network:geo
search:googler
google:googler
movie:movie
film:movie
define:define
meaning:define
dictionary:define
encrypt:crypt
decrypt:crypt
encode:cipher
decode:cipher
breach:pwned
pwned:pwned
scan:shodan
vulnerability:shodan
qr:qrify
shorten:shorturl
transfer:transfer
upload:transfer
crypto:cryptocurrency
bitcoin:cryptocurrency
news:news
headlines:news
lyrics:lyrics
todo:todo
task:todo
cheat:cheat
reference:cheat
meme:meme
covid:covidinfo
"

    local seen_tools=""

    echo "$keyword_tools" | while IFS=':' read -r keyword tool; do
        # Skip empty lines
        [[ -z "$keyword" || -z "$tool" ]] && continue
        keyword=$(echo "$keyword" | tr -d ' ')
        tool=$(echo "$tool" | tr -d ' ')

        if echo "$task_lower" | grep -q "$keyword"; then
            # Check if we already added this tool
            if ! echo "$seen_tools" | grep -q ":${tool}:"; then
                step=$((step + 1))
                local desc
                desc=$(get_tool_description "$tool" 2>/dev/null || echo "Execute $tool")
                printf '{"step":%d,"tool":"%s","args":"","description":"%s"}\n' "$step" "$tool" "$desc"
                seen_tools="${seen_tools}:${tool}:"
            fi
        fi
    done | {
        # Collect results from subshell
        local collected=()
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            collected+=("$line")
        done

        if [[ ${#collected[@]} -eq 0 ]]; then
            echo -e "${ORANGE}Could not decompose task into a plan.${RESETBG}"
            echo "Try being more specific, or use one of these templates:"
            echo "  - 'security audit on my network'"
            echo "  - 'daily briefing'"
            echo "  - 'investigate IP 1.2.3.4'"
            echo "  - 'encrypt and transfer file.txt'"
            return 1
        fi

        # Build plan JSON
        local plan_json="["
        local first=true
        for s in "${collected[@]}"; do
            if ! $first; then plan_json+=","; fi
            plan_json+="$s"
            first=false
        done
        plan_json+="]"

        # Save and display
        local plan_id
        plan_id=$(date +%s)
        local plan_file="$PLANS_DIR/plan_${plan_id}.json"
        echo "$plan_json" > "$plan_file"

        if [[ "$GATHM_OUTPUT_MODE" == "json" ]]; then
            printf '{"plan_id":"%s","plan_name":"Dynamic Plan","steps":%s}' "$plan_id" "$plan_json"
        else
            echo -e "${BOLD}${GREEN}Dynamic Execution Plan${RESETBG}"
            echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
            echo -e "  Plan ID: ${CYAN}$plan_id${RESETBG}"
            echo -e "  Task: $task"
            echo ""

            local i=0
            for s in "${collected[@]}"; do
                i=$((i + 1))
                local s_tool
                s_tool=$(echo "$s" | grep -o '"tool":"[^"]*"' | cut -d'"' -f4)
                local s_desc
                s_desc=$(echo "$s" | grep -o '"description":"[^"]*"' | cut -d'"' -f4)

                echo -e "  ${BOLD}Step $i:${RESETBG} $s_desc"
                echo -e "    Tool: ${CYAN}$s_tool${RESETBG}"
            done

            echo ""
            echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESETBG}"
            echo -e "Execute: ${BOLD}gathm-agent execute-plan $plan_id${RESETBG}"
        fi
    }
}

# Execute a saved plan
cmd_execute_plan() {
    local plan_id="$1"
    local plan_file="$PLANS_DIR/plan_${plan_id}.json"

    if [[ ! -f "$plan_file" ]]; then
        echo "Error: Plan $plan_id not found." >&2
        return 1
    fi

    log_info "planner" "Executing plan: $plan_id"
    audit_log "plan_execute" "agent" "planner" "plan_id=$plan_id"

    echo -e "${BOLD}${GREEN}Executing Plan: $plan_id${RESETBG}"
    echo ""

    local plan
    plan=$(cat "$plan_file")

    # Parse each step and execute (portable - no grep -P)
    local step=0

    # Extract JSON objects portably using awk instead of grep -oP
    echo "$plan" | awk '
    BEGIN { RS="[{]"; FS="[}]" }
    NR > 1 { print "{" $1 "}" }
    ' | while IFS= read -r step_json; do
        [[ -z "$step_json" || "$step_json" == "{}" ]] && continue
        step=$((step + 1))
        local tool args desc
        tool=$(echo "$step_json" | grep -o '"tool":"[^"]*"' | cut -d'"' -f4)
        args=$(echo "$step_json" | grep -o '"args":"[^"]*"' | cut -d'"' -f4)
        desc=$(echo "$step_json" | grep -o '"description":"[^"]*"' | cut -d'"' -f4)

        [[ -z "$tool" ]] && continue

        echo -e "${CYAN}[Step $step]${RESETBG} $desc"
        echo -e "  Running: ${BOLD}$tool $args${RESETBG}"

        execute_with_recovery "$tool" $args
        local exit_code=$?

        if [[ $exit_code -ne 0 ]]; then
            echo -e "${RED}Step $step failed!${RESETBG}"
            echo -e "${ORANGE}Attempting self-heal and retry...${RESETBG}"
            self_heal "$tool"
            execute_with_recovery "$tool" $args
            exit_code=$?
            if [[ $exit_code -ne 0 ]]; then
                echo -e "${RED}Step $step failed after self-heal. Continuing...${RESETBG}"
            fi
        fi

        echo ""
    done

    echo -e "${GREEN}Plan execution complete.${RESETBG}"
}
