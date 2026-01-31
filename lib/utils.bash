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
detect_platform() {
    local kernel_name
    kernel_name=$(uname -s)

    if [[ "$kernel_name" == "Darwin" ]]; then
        echo "darwin"
    elif [[ "$kernel_name" == "Linux" ]]; then
        # Differentiate between Termux and other Linux environments
        if command -v termux-setup-storage &>/dev/null; then
            echo "termux"
        elif [ -f "/etc/os-release" ]; then
            # Source os-release to get the ID, fallback to generic 'linux'
            local id
            id=$(source /etc/os-release && echo "$ID")
            if [[ -n "$id" ]]; then
                echo "$id"
            else
                echo "linux"
            fi
        else
            echo "linux" # Generic Linux
        fi
    else
        echo "unknown"
    fi
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

checkInternet() {
    httpGet github.com > /dev/null 2>&1 || return 1
}

# UI Helpers
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
    echo -e "${BLUE}        Gathm Framework ${WHITE}v${currentVersion}${RESETBG}"
    echo
}

pause() {
    read -n 1 -s -r -p "Press any key to continue..."
    echo
}

print_divider() {
    printf "${ORANGE}%*s\n${RESETBG}" "$(tput cols)" | tr ' ' '⬢'
}

check_dependencies() {
    local missing_deps=0
    for dep in "$@"; do
        if ! command -v "$dep" &>/dev/null; then
            echo "${RED}Error: Required command '$dep' is not installed.${RESETBG}"
            missing_deps=1
        fi
    done

    if [ "$missing_deps" -eq 1 ]; then
        echo "Please install the missing dependencies and try again."
        exit 1
    fi
}

open_file() {
    local file_path="$1"
    case "$(detect_platform)" in
        darwin) open "$file_path" &>/dev/null ;;
        termux) termux-open "$file_path" &>/dev/null ;;
        *)      xdg-open "$file_path" &>/dev/null ;; # Default to xdg-open for Linux
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
