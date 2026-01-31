#!/usr/bin/env bash
# ============================================================
#  Gathm Enterprise - Universal Quick Setup
#  Works on: Linux (all distros), macOS, Termux, WSL, Git Bash
#
#  Usage:
#    bash setup.sh              # Auto-detect platform & install
#    bash setup.sh --check      # Just verify environment
#    bash setup.sh --uninstall  # Remove symlinks & data
# ============================================================

set -euo pipefail

VERSION="3.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# --- Colors (skip on dumb terminals) ---
if [[ "${TERM:-dumb}" != "dumb" ]]; then
    RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"
    BLUE="\033[34m"; CYAN="\033[36m"; BOLD="\033[1m"; RESET="\033[0m"
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; BOLD=""; RESET=""
fi

info()  { echo -e "${BLUE}[*]${RESET} $1"; }
ok()    { echo -e "${GREEN}[+]${RESET} $1"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $1"; }
fail()  { echo -e "${RED}[-]${RESET} $1"; }

# --- Platform detection ---
detect_platform() {
    local kernel
    kernel=$(uname -s 2>/dev/null || echo "unknown")
    case "$kernel" in
        Darwin)       echo "macos" ;;
        MINGW*|MSYS*) echo "windows" ;;
        CYGWIN*)      echo "windows" ;;
        Linux)
            if command -v termux-setup-storage &>/dev/null; then
                echo "termux"
            elif grep -qi microsoft /proc/version 2>/dev/null; then
                echo "wsl"
            elif command -v apt-get &>/dev/null; then
                echo "debian"
            elif command -v dnf &>/dev/null; then
                echo "fedora"
            elif command -v pacman &>/dev/null; then
                echo "arch"
            elif command -v apk &>/dev/null; then
                echo "alpine"
            elif command -v zypper &>/dev/null; then
                echo "opensuse"
            else
                echo "linux"
            fi
            ;;
        *) echo "unknown" ;;
    esac
}

# --- Install dependencies by platform ---
install_deps() {
    local platform="$1"

    # Core packages every platform needs
    local core="bash curl jq git openssl"

    info "Platform detected: $platform"
    info "Installing dependencies..."

    case "$platform" in
        termux)
            pkg update -y 2>/dev/null || true
            pkg install -y $core python pv dialog wget dnsutils iproute2 net-tools libxml2 2>/dev/null || true
            pip install pyyaml 2>/dev/null || true
            ;;
        debian|wsl)
            sudo apt-get update -y 2>/dev/null || true
            sudo apt-get install -y $core python3 python3-pip pv dialog wget dnsutils iproute2 net-tools libxml2-utils 2>/dev/null || true
            pip3 install pyyaml 2>/dev/null || pip install pyyaml 2>/dev/null || true
            ;;
        fedora)
            sudo dnf install -y $core python3 python3-pip pv dialog wget bind-utils iproute net-tools libxml2 2>/dev/null || true
            pip3 install pyyaml 2>/dev/null || true
            ;;
        arch)
            sudo pacman -Sy --noconfirm $core python python-pip pv dialog wget bind-tools iproute2 net-tools libxml2 2>/dev/null || true
            pip install pyyaml 2>/dev/null || true
            ;;
        alpine)
            apk update 2>/dev/null || true
            apk add $core python3 py3-pip pv dialog wget bind-tools iproute2 net-tools libxml2-utils 2>/dev/null || true
            pip3 install pyyaml 2>/dev/null || true
            ;;
        opensuse)
            sudo zypper install -y $core python3 python3-pip pv dialog wget bind-utils iproute2 net-tools libxml2-tools 2>/dev/null || true
            pip3 install pyyaml 2>/dev/null || true
            ;;
        macos)
            if command -v brew &>/dev/null; then
                brew install bash curl jq git openssl python3 pv dialog wget 2>/dev/null || true
                pip3 install pyyaml 2>/dev/null || true
            else
                warn "Homebrew not found. Install it from https://brew.sh"
                warn "Then run: brew install bash curl jq git python3 pv dialog"
            fi
            ;;
        windows)
            if command -v scoop &>/dev/null; then
                scoop install git curl jq python 2>/dev/null || true
            elif command -v choco &>/dev/null; then
                choco install git curl jq python3 -y 2>/dev/null || true
            elif command -v pacman &>/dev/null; then
                # MSYS2
                pacman -Sy --noconfirm $core python python-pip 2>/dev/null || true
            else
                warn "No package manager found (try installing scoop, choco, or MSYS2)"
            fi
            pip install pyyaml 2>/dev/null || pip3 install pyyaml 2>/dev/null || true
            ;;
        *)
            warn "Unknown platform. Please install manually: $core python3 pv jq"
            ;;
    esac

    ok "Dependencies installed"
}

# --- Setup files and permissions ---
setup_files() {
    info "Setting up files..."

    # Data directories
    mkdir -p "$HOME/.gathm"/{logs,health,agent/plans} 2>/dev/null || true

    # Make scripts executable
    chmod +x "$SCRIPT_DIR/gathm" 2>/dev/null || true
    chmod +x "$SCRIPT_DIR/agent/"*.sh 2>/dev/null || true
    for dir in "$SCRIPT_DIR"/tools/*/; do
        local name
        name=$(basename "$dir")
        [[ -f "$dir/$name" ]] && chmod +x "$dir/$name"
    done

    ok "Files ready"
}

# --- Create command shortcuts ---
setup_shortcuts() {
    local platform="$1"
    info "Creating command shortcuts..."

    local bin_dir="$HOME/.local/bin"
    mkdir -p "$bin_dir"

    # Determine shebang
    local shebang="#!/usr/bin/env bash"
    if [[ "$platform" == "termux" ]]; then
        shebang="#!/data/data/com.termux/files/usr/bin/bash"
    fi

    # gathm
    cat > "$bin_dir/gathm" << SCRIPT
$shebang
exec "$SCRIPT_DIR/gathm" "\$@"
SCRIPT
    chmod +x "$bin_dir/gathm"

    # gathm-agent
    cat > "$bin_dir/gathm-agent" << SCRIPT
$shebang
exec bash "$SCRIPT_DIR/agent/orchestrator.sh" "\$@"
SCRIPT
    chmod +x "$bin_dir/gathm-agent"

    # gathm-api
    local python_cmd="python3"
    command -v python3 &>/dev/null || python_cmd="python"
    cat > "$bin_dir/gathm-api" << SCRIPT
$shebang
exec $python_cmd "$SCRIPT_DIR/api/server.py" "\$@"
SCRIPT
    chmod +x "$bin_dir/gathm-api"

    # Add to PATH if needed
    local rc_file=""
    if [[ -f "$HOME/.zshrc" ]]; then rc_file="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then rc_file="$HOME/.bashrc"
    elif [[ -f "$HOME/.bash_profile" ]]; then rc_file="$HOME/.bash_profile"
    fi

    if [[ -n "$rc_file" ]] && ! grep -q '.local/bin' "$rc_file" 2>/dev/null; then
        echo '' >> "$rc_file"
        echo '# Gathm Framework' >> "$rc_file"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc_file"
        info "Added ~/.local/bin to PATH in $rc_file"
    fi

    ok "Commands: gathm, gathm-agent, gathm-api"
}

# --- Verify everything ---
verify() {
    local errors=0

    echo ""
    echo -e "${BOLD}Verification${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Platform
    ok "Platform: $(detect_platform) | $(uname -s) | Bash ${BASH_VERSION:-?}"

    # Core commands
    for cmd in bash curl jq git; do
        if command -v "$cmd" &>/dev/null; then
            ok "$cmd"
        else
            fail "$cmd NOT FOUND"; errors=$((errors + 1))
        fi
    done

    # Python
    if command -v python3 &>/dev/null; then
        ok "python3 ($(python3 --version 2>&1))"
    elif command -v python &>/dev/null; then
        ok "python ($(python --version 2>&1))"
    else
        fail "python NOT FOUND"; errors=$((errors + 1))
    fi

    # Tools
    local tc=0
    for dir in "$SCRIPT_DIR"/tools/*/; do
        local n; n=$(basename "$dir")
        [[ -f "$dir/$n" ]] && tc=$((tc + 1))
    done
    ok "$tc tools available"

    # Agent smoke test
    if bash "$SCRIPT_DIR/agent/orchestrator.sh" list --json &>/dev/null; then
        ok "Agent orchestrator"
    else
        fail "Agent orchestrator broken"; errors=$((errors + 1))
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [[ $errors -eq 0 ]]; then
        ok "All checks passed!"
    else
        fail "$errors issue(s) found"
    fi
    return $errors
}

# --- Uninstall ---
uninstall() {
    info "Removing Gathm shortcuts..."
    rm -f "$HOME/.local/bin/gathm" "$HOME/.local/bin/gathm-agent" "$HOME/.local/bin/gathm-api"
    info "Removing data directory..."
    rm -rf "$HOME/.gathm"
    ok "Uninstalled. Source directory not removed: $SCRIPT_DIR"
}

# --- Main ---
main() {
    echo ""
    echo -e "${BOLD}${GREEN}Gathm Enterprise v$VERSION - Setup${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    case "${1:-install}" in
        --check|-c)
            verify; exit $? ;;
        --uninstall|-u)
            uninstall; exit 0 ;;
        --help|-h)
            echo "Usage: bash setup.sh [OPTION]"
            echo ""
            echo "Options:"
            echo "  (default)     Auto-detect platform and install"
            echo "  --check       Verify environment only"
            echo "  --uninstall   Remove shortcuts and data"
            echo "  --help        Show this help"
            exit 0 ;;
    esac

    local platform
    platform=$(detect_platform)

    # Redirect to Termux-specific installer if on Termux
    if [[ "$platform" == "termux" && -f "$SCRIPT_DIR/setup-termux.sh" ]]; then
        info "Termux detected, using Termux-optimized setup..."
        exec bash "$SCRIPT_DIR/setup-termux.sh" "$@"
    fi

    install_deps "$platform"
    setup_files
    setup_shortcuts "$platform"
    verify

    echo ""
    echo -e "${BOLD}${GREEN}Setup Complete!${RESET}"
    echo ""
    echo "  Restart your shell or run:"
    echo -e "    ${CYAN}source ~/.bashrc${RESET}  (or ~/.zshrc)"
    echo ""
    echo "  Quick start:"
    echo -e "    ${CYAN}gathm-agent status${RESET}              # Check agent"
    echo -e "    ${CYAN}gathm-agent list${RESET}                # List tools"
    echo -e "    ${CYAN}gathm-agent ask \"weather NYC\"${RESET}    # NLP query"
    echo -e "    ${CYAN}gathm-agent run weather Paris${RESET}   # Run tool"
    echo -e "    ${CYAN}gathm-agent health all${RESET}          # Health check"
    echo -e "    ${CYAN}gathm-agent plan \"daily briefing\"${RESET} # Task plan"
    echo -e "    ${CYAN}gathm-api --port 8080${RESET}           # REST API"
    echo -e "    ${CYAN}gathm${RESET}                           # Interactive menu"
    echo ""
}

main "$@"
