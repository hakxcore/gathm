#!/usr/bin/env bash
# ============================================================
#  Gathm Enterprise - Universal Installer
#  Works on: Linux (all distros), macOS, Termux, WSL, Git Bash
#
#  Usage:
#    bash install.sh              # Auto-detect platform & install
#    bash install.sh --check      # Just verify environment
#    bash install.sh --uninstall  # Remove symlinks & data
# ============================================================

set -euo pipefail

VERSION="3.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN" 2>/dev/null || true
export PATH="$LOCAL_BIN:$PATH"

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

# --- Cleanup on interrupt ---
_setup_cleanup() {
    echo ""
    warn "Setup interrupted. Partial installation may exist."
    warn "Re-run 'bash install.sh' to resume, or 'bash install.sh --uninstall' to clean up."
    exit 130
}
trap _setup_cleanup INT TERM

# --- Internet connectivity check ---
GATHM_ONLINE="false"
check_internet() {
    info "Checking internet connectivity..."
    if curl -s --connect-timeout 5 --max-time 10 https://github.com > /dev/null 2>&1; then
        GATHM_ONLINE="true"
        ok "Network: ${GREEN}Online${RESET}"
    elif wget -q --spider --timeout=5 https://github.com 2>/dev/null; then
        GATHM_ONLINE="true"
        ok "Network: ${GREEN}Online${RESET}"
    else
        GATHM_ONLINE="false"
        warn "Network: ${RED}Offline${RESET} — skipping online-only steps (Ollama, llmfit)"
    fi
}

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

# --- Termux storage permissions (no-op on non-Termux) ---
setup_termux_storage() {
    local platform="$1"
    [[ "$platform" != "termux" ]] && return 0

    if ! command -v termux-setup-storage >/dev/null 2>&1; then
        warn "termux-setup-storage not found; skipping storage permission setup"
        return 0
    fi

    if [[ ! -d "$HOME/storage" ]]; then
        info "Requesting Termux storage permission..."
        termux-setup-storage 2>/dev/null || true
        sleep 2
    fi

    if [[ -d "$HOME/storage" ]]; then
        ok "Termux storage configured"
    else
        warn "Termux storage folder not detected. You can run: termux-setup-storage"
    fi
}

# --- Install Ollama (local LLM runtime) ---
install_ollama() {
    if [[ "$GATHM_ONLINE" != "true" ]]; then
        warn "Skipping Ollama install (offline)"
        return 0
    fi

    if command -v ollama &>/dev/null; then
        ok "Ollama already installed ($(ollama --version 2>&1 | head -1))"
        return 0
    fi

    info "Installing Ollama (local LLM runtime)..."
    local platform="$1"

    case "$platform" in
        macos)
            if command -v brew &>/dev/null; then
                brew install ollama 2>/dev/null || true
            else
                curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null || true
            fi
            ;;
        termux)
            warn "Ollama is not yet supported on Termux natively."
            warn "Consider using a remote Ollama instance (set OLLAMA_HOST)."
            return 0
            ;;
        windows)
            warn "On Windows, install Ollama from https://ollama.com/download"
            return 0
            ;;
        *)
            # Linux (all distros), WSL
            curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null || true
            ;;
    esac

    if command -v ollama &>/dev/null; then
        ok "Ollama installed"
    else
        warn "Ollama installation failed — install manually from https://ollama.com"
    fi
}

# --- Install llmfit and select best model ---
install_llmfit_and_select_model() {
    if [[ "$GATHM_ONLINE" != "true" ]]; then
        warn "Skipping llmfit (offline) — using default model: gemma3:4b"
        _save_model_config "gemma3:4b"
        return 0
    fi

    if ! command -v ollama &>/dev/null; then
        warn "Ollama not available — skipping model selection"
        _save_model_config "gemma3:4b"
        return 0
    fi

    info "Installing llmfit (hardware-aware LLM recommender)..."

    # Try to install llmfit
    local llmfit_installed=false
    if command -v llmfit &>/dev/null; then
        llmfit_installed=true
        ok "llmfit already installed"
    elif command -v cargo &>/dev/null; then
        cargo install llmfit 2>/dev/null && llmfit_installed=true
    else
        # Use the quick-install script
        curl -fsSL https://llmfit.axjns.dev/install.sh | sh 2>/dev/null && llmfit_installed=true
    fi

    if [[ "$llmfit_installed" == "true" ]] && command -v llmfit &>/dev/null; then
        ok "llmfit installed"
        info "Analyzing hardware to find the best local model..."

        local recommended=""
        # Get the top recommendation in JSON, extract the model name
        local llmfit_output
        llmfit_output=$(llmfit recommend --json --limit 5 2>/dev/null) || true

        if [[ -n "$llmfit_output" ]]; then
            # Try to find an ollama-compatible model from recommendations
            # llmfit outputs JSON array; parse with python or jq
            if command -v jq &>/dev/null; then
                recommended=$(echo "$llmfit_output" | jq -r '.[0].name // empty' 2>/dev/null)
            elif command -v python3 &>/dev/null; then
                recommended=$(python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    if data and len(data) > 0:
        print(data[0].get('name', ''))
except Exception:
    pass
" <<< "$llmfit_output" 2>/dev/null)
            fi
        fi

        if [[ -n "$recommended" ]]; then
            ok "Best model for your hardware: $recommended"
            info "Pulling model via Ollama (this may take a while)..."
            ollama pull "$recommended" 2>/dev/null || {
                warn "Failed to pull '$recommended', falling back to gemma3:4b"
                recommended="gemma3:4b"
                ollama pull "$recommended" 2>/dev/null || true
            }
            _save_model_config "$recommended"
        else
            warn "Could not determine best model — using default: gemma3:4b"
            info "Pulling default model..."
            ollama pull "gemma3:4b" 2>/dev/null || true
            _save_model_config "gemma3:4b"
        fi
    else
        warn "llmfit not available — using default model: gemma3:4b"
        info "Pulling default model..."
        ollama pull "gemma3:4b" 2>/dev/null || true
        _save_model_config "gemma3:4b"
    fi
}

# Save selected model to config
_save_model_config() {
    local model="$1"
    local config_dir="$HOME/.gathm"
    mkdir -p "$config_dir" 2>/dev/null || true
    echo "$model" > "$config_dir/model"
    ok "Model config saved: $model"

    # Also write to .env for Pilot to pick up
    local env_file="$SCRIPT_DIR/.env"
    if [[ -f "$env_file" ]]; then
        # Update existing GATHM_OLLAMA_MODEL line or append
        if grep -q '^GATHM_OLLAMA_MODEL=' "$env_file" 2>/dev/null; then
            sed -i "s|^GATHM_OLLAMA_MODEL=.*|GATHM_OLLAMA_MODEL=$model|" "$env_file" 2>/dev/null || \
                sed -i '' "s|^GATHM_OLLAMA_MODEL=.*|GATHM_OLLAMA_MODEL=$model|" "$env_file"
        else
            echo "GATHM_OLLAMA_MODEL=$model" >> "$env_file"
        fi
    else
        echo "GATHM_OLLAMA_MODEL=$model" > "$env_file"
    fi
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

    # compatibility alias: gathm-agent
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
    if [[ "$platform" == "termux" ]]; then
        rc_file="$HOME/.bashrc"
    elif [[ -f "$HOME/.zshrc" ]]; then rc_file="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then rc_file="$HOME/.bashrc"
    elif [[ -f "$HOME/.bash_profile" ]]; then rc_file="$HOME/.bash_profile"
    elif [[ -f "$HOME/.profile" ]]; then rc_file="$HOME/.profile"
    else rc_file="$HOME/.bashrc"
    fi

    [[ -f "$rc_file" ]] || touch "$rc_file"
    if [[ -n "$rc_file" ]] && ! grep -q '.local/bin' "$rc_file" 2>/dev/null; then
        echo '' >> "$rc_file"
        echo '# Gathm Framework' >> "$rc_file"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc_file"
        info "Added ~/.local/bin to PATH in $rc_file"
    fi

    ok "Commands: gathm, gathm-api"
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

    # Connectivity
    if [[ "$GATHM_ONLINE" == "true" ]]; then
        ok "Network: Online"
    else
        warn "Network: Offline"
    fi

    # Ollama
    if command -v ollama &>/dev/null; then
        ok "Ollama ($(ollama --version 2>&1 | head -1))"
    else
        warn "Ollama not installed (optional — needed for Pilot AI)"
    fi

    # Configured model
    if [[ -f "$HOME/.gathm/model" ]]; then
        ok "LLM model: $(cat "$HOME/.gathm/model")"
    fi

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

# --- Reload shell config (best effort) ---
reload_shell_config() {
    # This can refresh PATH for the installer process.
    # Parent shell still may require a manual `source ~/.bashrc`.
    source "$HOME/.bashrc" 2>/dev/null || true
    source "$HOME/.zshrc" 2>/dev/null || true
    source "$HOME/.bash_profile" 2>/dev/null || true
    source "$HOME/.profile" 2>/dev/null || true
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
            echo "Usage: bash install.sh [OPTION]"
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

    # Check internet first — determines what we can install
    check_internet

    setup_termux_storage "$platform"
    install_deps "$platform"
    setup_files
    setup_shortcuts "$platform"

    # Install Ollama + best-fit model (requires internet)
    install_ollama "$platform"
    install_llmfit_and_select_model

    reload_shell_config
    verify

    echo ""
    echo -e "${BOLD}${GREEN}Setup Complete!${RESET}"
    echo ""
    echo "  Restart your shell or run:"
    echo -e "    ${CYAN}source ~/.bashrc${RESET}  (or ~/.zshrc)"
    echo ""
    echo "  Quick start:"
    echo -e "    ${CYAN}gathm status${RESET}                    # Check agent"
    echo -e "    ${CYAN}gathm list${RESET}                      # List tools"
    echo -e "    ${CYAN}gathm ask \"weather NYC\"${RESET}          # NLP query"
    echo -e "    ${CYAN}gathm run weather Paris${RESET}         # Run tool"
    echo -e "    ${CYAN}gathm health all${RESET}                # Health check"
    echo -e "    ${CYAN}gathm plan \"daily briefing\"${RESET}       # Task plan"
    echo -e "    ${CYAN}gathm-api --port 8080${RESET}           # REST API"
    echo -e "    ${CYAN}gathm${RESET}                           # Interactive menu"
    echo ""
}

main "$@"
