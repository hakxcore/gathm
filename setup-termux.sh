#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  Gathm Enterprise - Termux Quick Setup
#  One-command setup for Android/Termux environments
#
#  Usage:
#    bash setup-termux.sh          # Full install
#    bash setup-termux.sh --agent  # Agent only (skip UI tools)
#    bash setup-termux.sh --check  # Just verify environment
# ============================================================

set -euo pipefail

VERSION="3.0.0"

# --- Colors ---
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
CYAN="\033[36m"
BOLD="\033[1m"
RESET="\033[0m"

info()  { echo -e "${BLUE}[*]${RESET} $1"; }
ok()    { echo -e "${GREEN}[+]${RESET} $1"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $1"; }
fail()  { echo -e "${RED}[-]${RESET} $1"; }

# --- Verify we're on Termux ---
check_termux() {
    if [[ ! -d "/data/data/com.termux" ]]; then
        fail "This script is designed for Termux (Android)."
        echo "  For other platforms, use: bash setup.sh"
        exit 1
    fi
    ok "Running on Termux"
}

# --- Storage permissions ---
setup_storage() {
    if [[ ! -d "$HOME/storage" ]]; then
        info "Requesting storage access..."
        termux-setup-storage 2>/dev/null || true
        sleep 2
    fi
    ok "Storage access configured"
}

# --- Package name mapping ---
# Some packages have different names on Termux vs standard Linux
termux_pkg_name() {
    case "$1" in
        python3)        echo "python" ;;
        python3-pip)    echo "python" ;;  # pip included with python on Termux
        libxml2-utils)  echo "libxml2" ;;
        dnsutils)       echo "dnsutils" ;;
        iproute2)       echo "iproute2" ;;
        net-tools)      echo "net-tools" ;;
        dialog)         echo "dialog" ;;
        jp2a)           echo "jp2a" ;;
        telnet)         echo "inetutils" ;;  # telnet is part of inetutils on Termux
        *)              echo "$1" ;;
    esac
}

# --- Install packages ---
install_packages() {
    local mode="${1:-full}"

    info "Updating package index..."
    pkg update -y 2>/dev/null || {
        warn "pkg update failed - trying with apt..."
        apt update -y 2>/dev/null || true
    }

    # Core packages (always needed)
    local core_pkgs=(
        bash
        curl
        wget
        jq
        python
        openssl
        git
        coreutils
        pv
    )

    # Extended packages (for full install)
    local extended_pkgs=(
        dialog
        dnsutils
        inetutils    # provides telnet
        iproute2
        net-tools
        libxml2
        jp2a
    )

    info "Installing core packages..."
    for pkg_name in "${core_pkgs[@]}"; do
        if ! command -v "$pkg_name" &>/dev/null 2>&1; then
            pkg install -y "$pkg_name" 2>/dev/null || {
                warn "Failed to install $pkg_name (non-critical)"
            }
        fi
    done

    if [[ "$mode" == "full" ]]; then
        info "Installing extended packages..."
        for pkg_name in "${extended_pkgs[@]}"; do
            pkg install -y "$pkg_name" 2>/dev/null || {
                warn "Could not install $pkg_name (optional)"
            }
        done
    fi

    # Python packages
    info "Installing Python packages..."
    pip install pyyaml 2>/dev/null || {
        warn "PyYAML install failed (API server will use fallback parser)"
    }

    ok "Package installation complete"
}

# --- Setup Gathm directories ---
setup_directories() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

    info "Setting up directories..."

    # Data directories
    mkdir -p "$HOME/.gathm"/{logs,health,agent/plans}

    # Make all scripts executable
    chmod +x "$script_dir/gathm" 2>/dev/null || true
    chmod +x "$script_dir/agent/"*.sh 2>/dev/null || true
    for dir in "$script_dir"/tools/*/; do
        local tool_name
        tool_name=$(basename "$dir")
        if [[ -f "$dir/$tool_name" ]]; then
            chmod +x "$dir/$tool_name"
        fi
    done

    ok "Directories ready"
}

# --- Create Termux shortcuts ---
setup_shortcuts() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

    info "Creating command shortcuts..."

    # Create bin directory in Termux PATH
    mkdir -p "$HOME/.local/bin"

    # Create wrapper scripts
    cat > "$HOME/.local/bin/gathm" << SCRIPT
#!/data/data/com.termux/files/usr/bin/bash
exec "$script_dir/gathm" "\$@"
SCRIPT
    chmod +x "$HOME/.local/bin/gathm"

    cat > "$HOME/.local/bin/gathm-agent" << SCRIPT
#!/data/data/com.termux/files/usr/bin/bash
exec bash "$script_dir/agent/orchestrator.sh" "\$@"
SCRIPT
    chmod +x "$HOME/.local/bin/gathm-agent"

    cat > "$HOME/.local/bin/gathm-api" << SCRIPT
#!/data/data/com.termux/files/usr/bin/bash
exec python "$script_dir/api/server.py" "\$@"
SCRIPT
    chmod +x "$HOME/.local/bin/gathm-api"

    # Ensure ~/.local/bin is in PATH
    local shell_rc="$HOME/.bashrc"
    if [[ -f "$HOME/.zshrc" ]] && command -v zsh &>/dev/null; then
        shell_rc="$HOME/.zshrc"
    fi

    if ! grep -q '.local/bin' "$shell_rc" 2>/dev/null; then
        echo '' >> "$shell_rc"
        echo '# Gathm Framework' >> "$shell_rc"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
        info "Added ~/.local/bin to PATH in $shell_rc"
    fi

    ok "Shortcuts created: gathm, gathm-agent, gathm-api"
}

# --- Verify installation ---
verify_install() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
    local errors=0

    echo ""
    echo -e "${BOLD}Environment Check${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Check bash version
    local bash_ver="${BASH_VERSION:-unknown}"
    if [[ "${bash_ver%%.*}" -ge 4 ]]; then
        ok "Bash $bash_ver (full feature support)"
    else
        warn "Bash $bash_ver (Bash 3 compatible mode)"
    fi

    # Check core commands
    local cmds=(curl jq python openssl git)
    for cmd in "${cmds[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            ok "$cmd found: $(command -v "$cmd")"
        else
            fail "$cmd NOT FOUND"
            errors=$((errors + 1))
        fi
    done

    # Check optional commands
    local optional=(dialog pv wget telnet jp2a)
    for cmd in "${optional[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            ok "$cmd found (optional)"
        else
            warn "$cmd not found (optional, some features limited)"
        fi
    done

    # Check tool count
    local tool_count=0
    for dir in "$script_dir"/tools/*/; do
        local name
        name=$(basename "$dir")
        if [[ -f "$dir/$name" ]]; then
            tool_count=$((tool_count + 1))
        fi
    done
    ok "$tool_count tools available"

    # Check manifest count
    local manifest_count=0
    for f in "$script_dir"/tools/*/tool.yaml; do
        if [[ -f "$f" ]]; then
            manifest_count=$((manifest_count + 1))
        fi
    done
    ok "$manifest_count tool manifests found"

    # Test agent
    echo ""
    echo -e "${BOLD}Agent Test${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if bash "$script_dir/agent/orchestrator.sh" status --json &>/dev/null; then
        ok "Agent orchestrator works"
    else
        fail "Agent orchestrator failed"
        errors=$((errors + 1))
    fi

    if bash "$script_dir/agent/orchestrator.sh" list --json &>/dev/null; then
        ok "Tool discovery works"
    else
        fail "Tool discovery failed"
        errors=$((errors + 1))
    fi

    if bash "$script_dir/agent/orchestrator.sh" ask "weather" --json &>/dev/null; then
        ok "NLP intent matching works"
    else
        fail "NLP intent matching failed"
        errors=$((errors + 1))
    fi

    # Summary
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [[ $errors -eq 0 ]]; then
        ok "All checks passed!"
    else
        fail "$errors check(s) failed"
    fi

    return $errors
}

# --- Main ---
main() {
    echo ""
    echo -e "${BOLD}${GREEN}Gathm Enterprise v$VERSION - Termux Setup${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    local mode="full"
    for arg in "$@"; do
        case "$arg" in
            --agent)  mode="agent" ;;
            --check)  mode="check" ;;
            --help|-h)
                echo "Usage: bash setup-termux.sh [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --agent   Agent-only install (skip UI packages)"
                echo "  --check   Just verify environment"
                echo "  --help    Show this help"
                exit 0
                ;;
        esac
    done

    if [[ "$mode" == "check" ]]; then
        verify_install
        exit $?
    fi

    check_termux
    setup_storage
    install_packages "$mode"
    setup_directories
    setup_shortcuts
    verify_install

    echo ""
    echo -e "${BOLD}${GREEN}Setup Complete!${RESET}"
    echo ""
    echo "  Restart your shell or run:"
    echo -e "    ${CYAN}source ~/.bashrc${RESET}"
    echo ""
    echo "  Then use:"
    echo -e "    ${CYAN}gathm${RESET}                         # Interactive menu"
    echo -e "    ${CYAN}gathm-agent status${RESET}             # Agent status"
    echo -e "    ${CYAN}gathm-agent ask \"weather in NYC\"${RESET} # Natural language"
    echo -e "    ${CYAN}gathm-agent health all${RESET}         # System health"
    echo -e "    ${CYAN}gathm-agent run weather Paris${RESET}  # Run a tool"
    echo -e "    ${CYAN}gathm-api --port 8080${RESET}          # Start REST API"
    echo ""
}

main "$@"
