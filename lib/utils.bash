#!/bin/bash

# Colors
RED="$(printf '\033[31m')"
GREEN="$(printf '\033[32m')"
ORANGE="$(printf '\033[33m')"
BLUE="$(printf '\033[34m')"
MAGENTA="$(printf '\033[35m')"
CYAN="$(printf '\033[36m')"
WHITE="$(printf '\033[37m')"
BLACK="$(printf '\033[30m')"

# Indian Tricolor
SAFFRON="$(printf '\033[38;5;208m')" # Orange/Saffron
INDIAN_GREEN="$(printf '\033[38;5;28m')" # Darker Green
ASHOKA_BLUE="$(printf '\033[38;5;20m')" # Navy Blue

REDBG="$(printf '\033[41m')"
GREENBG="$(printf '\033[42m')"
ORANGEBG="$(printf '\033[43m')"
BLUEBG="$(printf '\033[44m')"
MAGENTABG="$(printf '\033[45m')"
CYANBG="$(printf '\033[46m')"
WHITEBG="$(printf '\033[47m')"
BLACKBG="$(printf '\033[40m')"
RESETBG="$(printf '\e[0m')"
BOLD="$(printf '\033[1m')"

# Platform Check
# Detects: darwin, termux, ubuntu, debian, arch, fedora, centos, alpine,
#          opensuse, linux (generic), windows (WSL/Git Bash/MSYS2/Cygwin)
detect_platform() {
    local kernel_name
    kernel_name=$(uname -s 2>/dev/null || echo "unknown")

    case "$kernel_name" in
        Darwin)
            echo "darwin"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo "windows"
            ;;
        Linux)
            # Differentiate between Termux, WSL, and other Linux environments
            if command -v termux-setup-storage &>/dev/null; then
                echo "termux"
            elif grep -qi microsoft /proc/version 2>/dev/null; then
                echo "wsl"
            elif [ -f "/etc/os-release" ]; then
                # Source os-release to get the ID, fallback to generic 'linux'
                local id
                id=$(grep '^ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')
                if [[ -n "$id" ]]; then
                    echo "$id"
                else
                    echo "linux"
                fi
            else
                echo "linux" # Generic Linux
            fi
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# Network Client Configuration
getConfiguredClient() {
    if command -v curl &>/dev/null; then
        configuredClient="curl"
    elif command -v wget &>/dev/null; then
        configuredClient="wget"
    elif command -v http &>/dev/null; then
        configuredClient="httpie"
    elif command -v fetch &>/dev/null; then
        configuredClient="fetch"
    else
        echo "Error: This tool requires either curl, wget, httpie or fetch." >&2
        return 1
    fi
}

httpGet() {
    case "$configuredClient" in
        curl)   curl -A curl -sL "$@" ;;
        wget)   wget -qO- "$@" ;;
        httpie) http -b GET "$@" ;;
        fetch)  fetch -q "$@" ;;
    esac
}

# Run a command under a wall-clock bound, wherever we are.
# macOS has no GNU `timeout` — tools that called it directly either failed for
# every invocation or ran unbounded, which is how siteciphers came to hang.
run_bounded() {
    local seconds="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$seconds" "$@"
    elif command -v perl >/dev/null 2>&1; then
        perl -e 'alarm shift; exec @ARGV' "$seconds" "$@"
    else
        # No bound available: still run, rather than failing outright.
        "$@"
    fi
}

# Explain why a JSON API call did not return JSON, instead of "empty response".
# The status, the content type and the first bytes are what actually identify
# the problem — a 403, an HTML error page, a captive portal, a moved endpoint.
httpJsonError() {
    local url="$1" body="$2" service="${3:-the service}"
    local status="" ctype="" probe=""
    if command -v curl >/dev/null 2>&1; then
        probe=$(curl -sS -o /dev/null -A curl -L \
                     -w '%{http_code} %{content_type}' "$url" 2>/dev/null) || probe=""
        status="${probe%% *}"
        ctype="${probe#* }"
    fi
    echo "Error: $service did not return usable JSON."
    if [[ -n "$status" ]]; then
        echo "       HTTP status: $status   content-type: ${ctype:-unknown}"
    fi
    if [[ -n "${body// }" ]]; then
        echo "       First bytes: $(printf '%s' "$body" | tr -d '\r' | tr '\n' ' ' | head -c 200)"
    else
        echo "       The response body was empty."
    fi
    echo "       Reproduce with: curl -sS -D - '$url' | head -20"
}

checkInternet() {
    httpGet github.com > /dev/null 2>&1 || return 1
}

# Returns "online" or "offline" on stdout (never fails)
get_connectivity_status() {
    if checkInternet 2>/dev/null; then
        echo "online"
    else
        echo "offline"
    fi
}

# UI Helpers
is_interactive() {
    # Returns 0 (true) if connected to a terminal and GATHM_NON_INTERACTIVE is not set
    if [[ -n "${GATHM_NON_INTERACTIVE}" ]]; then
        return 1
    fi
    if [ -t 0 ]; then
        return 0
    else
        return 1
    fi
}

print_header() {
    clear
    echo -e "${BOLD}${GREEN}"
    cat << "EOF"
   ___      _   _
  / _ \__ _| |_| |__  _ __ ___
 / /_\/ _` | __| '_ \| '_ ` _ \
/ /_\\ (_| | |_| | | | | | | | |
\____/\__,_|\__|_| |_|_| |_| |_|
EOF
    echo -e "${RESETBG}"
    echo -e "${BLUE}        Gathm Framework ${WHITE}v${currentVersion:-1.0.0}${RESETBG}"
    local net_status
    net_status=$(get_connectivity_status)
    if [[ "$net_status" == "online" ]]; then
        echo -e "        ${GREEN}● Online${RESETBG}"
    else
        echo -e "        ${RED}● Offline${RESETBG}"
    fi
    echo
}

pause() {
    if is_interactive; then
        read -n 1 -s -r -p "Press any key to continue..."
        echo
    fi
}

print_divider() {
    local cols
    cols=$(tput cols 2>/dev/null || echo 80)
    printf "${ORANGE}%*s\n${RESETBG}" "$cols" | tr ' ' '-'
}

# Install recipes, so a missing dependency comes with the command that fixes it.
# shellcheck source=lib/deps.bash
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/deps.bash" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/deps.bash"
fi

check_dependencies() {
    local missing_deps=0
    for dep in "$@"; do
        if ! command -v "$dep" &>/dev/null; then
            echo "${RED}Error: Required command '$dep' is not installed.${RESETBG}"
            # "Please install the missing dependencies and try again" told the
            # user nothing — and for a Go binary or a differently-named package,
            # guessing `pkg install <command>` does not work either.
            if declare -f gathm_dep_report >/dev/null 2>&1; then
                gathm_dep_report "$dep"
            fi
            missing_deps=1
        fi
    done

    if [ "$missing_deps" -eq 1 ]; then
        exit 1
    fi
}

open_file() {
    local file_path="$1"
    local platform
    platform=$(detect_platform)
    case "$platform" in
        darwin)  open "$file_path" &>/dev/null ;;
        termux)  termux-open "$file_path" &>/dev/null ;;
        windows) cmd.exe /c start "" "$file_path" &>/dev/null 2>&1 \
                     || explorer.exe "$file_path" &>/dev/null 2>&1 ;;
        wsl)     wslview "$file_path" &>/dev/null 2>&1 \
                     || explorer.exe "$file_path" &>/dev/null 2>&1 \
                     || xdg-open "$file_path" &>/dev/null ;;
        *)       xdg-open "$file_path" &>/dev/null ;;
    esac
}

# --- Enterprise Extensions ---
# Source enterprise libraries if available
GATHM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd)"
if [[ -f "$GATHM_ROOT/lib/logging.bash" ]]; then
    source "$GATHM_ROOT/lib/logging.bash"
fi
if [[ -f "$GATHM_ROOT/lib/schema.bash" ]]; then
    source "$GATHM_ROOT/lib/schema.bash"
fi
