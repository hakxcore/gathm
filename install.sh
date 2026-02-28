#!/usr/bin/env bash
# ============================================================
#  Gathm Enterprise - Universal Installer
#  Works on: Linux (all distros), macOS, WSL, Git Bash
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
        start_ollama_serve
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
            pkg install -y ollama 2>/dev/null || true
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
        start_ollama_serve
    else
        warn "Ollama installation failed — install manually from https://ollama.com"
    fi
}

# --- Ensure ollama serve is running in the background ---
start_ollama_serve() {
    if ! command -v ollama &>/dev/null; then
        return 0
    fi

    # Check if already serving (port 11434)
    if curl -s --max-time 2 http://127.0.0.1:11434/ &>/dev/null; then
        ok "Ollama server already running"
        return 0
    fi

    info "Starting Ollama server (ollama serve)..."
    local _log_dir="${PREFIX:+$PREFIX/tmp}"; _log_dir="${_log_dir:-${TMPDIR:-/tmp}}"
    nohup ollama serve &>"$_log_dir/ollama-serve.log" &
    disown $! 2>/dev/null || true

    # Wait up to 10 s for it to come up
    local i=0
    while (( i < 10 )); do
        sleep 1
        if curl -s --max-time 2 http://127.0.0.1:11434/ &>/dev/null; then
            ok "Ollama server started"
            return 0
        fi
        i=$((i + 1))
    done

    warn "Ollama server did not respond in time — it may still be starting up"
}

# --- Open URL in default browser (best-effort) ---
open_url() {
    local url="$1"
    local platform="${2:-linux}"
    case "$platform" in
        macos)   open "$url" 2>/dev/null || true ;;
        termux)  termux-open-url "$url" 2>/dev/null || true ;;
        wsl)     cmd.exe /c start "" "$url" 2>/dev/null || xdg-open "$url" 2>/dev/null || true ;;
        *)       xdg-open "$url" 2>/dev/null || sensible-browser "$url" 2>/dev/null || true ;;
    esac
}

# --- Google Gemini free-tier fallback (when Ollama is unavailable) ---
setup_gemini_fallback() {
    local platform="${1:-linux}"

    echo ""
    info "Setting up Google Gemini (free tier) as the Pilot AI backend."
    echo ""
    echo -e "  ${CYAN}Google Gemini${RESET} gives you free access to powerful LLMs."
    echo "  You only need a Google account — no credit card required."
    echo ""
    echo "  Steps:"
    echo "    1. We'll open Google AI Studio in your browser."
    echo "    2. Sign in with your Google account."
    echo "    3. Click 'Create API key' and copy it."
    echo "    4. Paste it here."
    echo ""
    read -r -p "  Press [Enter] to open AI Studio (or Ctrl+C to skip): " _dummy </dev/tty || true
    open_url "https://aistudio.google.com/apikey" "$platform"
    echo ""
    echo "  (If the browser didn't open, visit: https://aistudio.google.com/apikey)"
    echo ""

    local gemini_key=""
    while [[ -z "$gemini_key" ]]; do
        read -r -p "  Paste your Gemini API key: " gemini_key </dev/tty || true
        if [[ -z "$gemini_key" ]]; then
            local skip_ans=""
            read -r -p "  No key entered. Skip Gemini setup? [y/N]: " skip_ans </dev/tty || true
            if [[ "$skip_ans" =~ ^[Yy]$ ]]; then
                warn "Gemini setup skipped — Pilot AI will not be available."
                return 0
            fi
        fi
    done

    # Write API key + backend config to .env (preserving other vars)
    local env_file="$SCRIPT_DIR/.env"
    local preserved=""
    if [[ -f "$env_file" ]]; then
        preserved=$(grep -v '^GOOGLE_API_KEY=\|^GEMINI_API_KEY=\|^GATHM_LLM_BACKEND=\|^GATHM_GEMINI_MODEL=' "$env_file" 2>/dev/null || true)
    fi
    {
        [[ -n "$preserved" ]] && echo "$preserved"
        echo "GOOGLE_API_KEY=$gemini_key"
        echo "GATHM_LLM_BACKEND=gemini"
        echo "GATHM_GEMINI_MODEL=gemini-2.0-flash-lite"
    } > "$env_file"
    chmod 600 "$env_file" 2>/dev/null || true

    # Save backend + model to ~/.gathm/
    mkdir -p "$HOME/.gathm" 2>/dev/null || true
    echo "gemini" > "$HOME/.gathm/llm_backend"
    _save_model_config "gemini-2.0-flash-lite"

    # Install the Python SDK for Gemini
    local pip_cmd=""
    command -v pip3 &>/dev/null && pip_cmd="pip3"
    command -v pip  &>/dev/null && [[ -z "$pip_cmd" ]] && pip_cmd="pip"
    if [[ -n "$pip_cmd" ]]; then
        info "Installing langchain-google-genai..."
        "$pip_cmd" install -q langchain-google-genai 2>/dev/null && \
            ok "langchain-google-genai installed" || \
            warn "Run manually: $pip_cmd install langchain-google-genai"
    fi

    ok "Gemini configured! Model: gemini-2.0-flash-lite (free tier)"
}

# --- Install llmfit and select best model ---
install_llmfit_and_select_model() {
    if [[ "$GATHM_ONLINE" != "true" ]]; then
        warn "Skipping llmfit (offline) — using default model: gemma3:4b"
        _save_model_config "gemma3:4b"
        return 0
    fi

    if ! command -v ollama &>/dev/null; then
        warn "Ollama not available — attempting Gemini fallback"
        setup_gemini_fallback "${_GATHM_PLATFORM:-linux}"
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
# Usage: _save_model_config <model> [backend]   backend defaults to "ollama"
_save_model_config() {
    local model="$1"
    local backend="${2:-ollama}"
    local config_dir="$HOME/.gathm"
    mkdir -p "$config_dir" 2>/dev/null || true
    echo "$model"   > "$config_dir/model"
    echo "$backend" > "$config_dir/llm_backend"
    ok "LLM config saved: backend=$backend model=$model"

    # Also write to .env for Pilot to pick up
    local env_file="$SCRIPT_DIR/.env"
    if [[ -f "$env_file" ]]; then
        if grep -q '^GATHM_OLLAMA_MODEL=' "$env_file" 2>/dev/null; then
            sed -i "s|^GATHM_OLLAMA_MODEL=.*|GATHM_OLLAMA_MODEL=$model|" "$env_file" 2>/dev/null || \
                sed -i '' "s|^GATHM_OLLAMA_MODEL=.*|GATHM_OLLAMA_MODEL=$model|" "$env_file"
        else
            echo "GATHM_OLLAMA_MODEL=$model" >> "$env_file"
        fi
        if grep -q '^GATHM_LLM_BACKEND=' "$env_file" 2>/dev/null; then
            sed -i "s|^GATHM_LLM_BACKEND=.*|GATHM_LLM_BACKEND=$backend|" "$env_file" 2>/dev/null || \
                sed -i '' "s|^GATHM_LLM_BACKEND=.*|GATHM_LLM_BACKEND=$backend|" "$env_file"
        else
            echo "GATHM_LLM_BACKEND=$backend" >> "$env_file"
        fi
    else
        printf 'GATHM_OLLAMA_MODEL=%s\nGATHM_LLM_BACKEND=%s\n' "$model" "$backend" > "$env_file"
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

# --- Install engineer Python requirements (optional) ---
install_engineer_deps() {
    if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
        warn "Skipping engineer deps (not supported on Termux)"
        return 0
    fi

    local req_file="$SCRIPT_DIR/engineer/requirements.txt"
    if [[ ! -f "$req_file" ]]; then
        return 0
    fi

    local python_cmd=""
    command -v python3 &>/dev/null && python_cmd="python3"
    command -v python &>/dev/null && [[ -z "$python_cmd" ]] && python_cmd="python"

    if [[ -z "$python_cmd" ]]; then
        warn "Python not found — skipping engineer dependencies"
        return 0
    fi

    local pip_cmd=""
    command -v pip3 &>/dev/null && pip_cmd="pip3"
    command -v pip &>/dev/null && [[ -z "$pip_cmd" ]] && pip_cmd="pip"

    if [[ -z "$pip_cmd" ]]; then
        warn "pip not found — skipping engineer dependencies"
        warn "To enable the engineer agent, run: pip install -r engineer/requirements.txt"
        return 0
    fi

    info "Installing engineer AI dependencies (autogen-agentchat, autogen-ext)..."
    "$pip_cmd" install -q -r "$req_file" 2>/dev/null && \
        ok "Engineer dependencies installed" || \
        warn "Engineer dependency install failed — run manually: $pip_cmd install -r engineer/requirements.txt"
}

# --- Install Pilot Python requirements ---
install_pilot_deps() {
    local req_file="$SCRIPT_DIR/pilot/requirements.txt"
    if [[ ! -f "$req_file" ]]; then
        return 0
    fi

    local pip_cmd=""
    command -v pip3 &>/dev/null && pip_cmd="pip3"
    command -v pip &>/dev/null && [[ -z "$pip_cmd" ]] && pip_cmd="pip"

    if [[ -z "$pip_cmd" ]]; then
        warn "pip not found — skipping Pilot dependencies"
        return 0
    fi

    info "Installing Pilot AI dependencies..."
    if "$pip_cmd" install -q -r "$req_file"; then
        ok "Pilot dependencies installed"
    else
        warn "Pilot dependency install failed — run manually: $pip_cmd install -r pilot/requirements.txt"
    fi
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

# --- Start the GUI API server in the background ---
# Sets global GUI_PORT and GUI_URL
GUI_PORT=8080
GUI_URL="http://127.0.0.1:${GUI_PORT}"

start_gui_server() {
    local python_cmd=""
    command -v python3 &>/dev/null && python_cmd="python3"
    command -v python &>/dev/null && [[ -z "$python_cmd" ]] && python_cmd="python"

    if [[ -z "$python_cmd" ]]; then
        warn "Python not found — cannot start GUI server"
        return 1
    fi

    local server_script="$SCRIPT_DIR/api/server.py"
    if [[ ! -f "$server_script" ]]; then
        warn "API server not found at $server_script — skipping GUI"
        return 1
    fi

    # Check if something is already on the port
    if curl -s --max-time 2 "$GUI_URL/" &>/dev/null; then
        ok "GUI server already running at $GUI_URL"
        return 0
    fi

    info "Starting GUI server on port $GUI_PORT..."
    local _log_dir="${PREFIX:+$PREFIX/tmp}"; _log_dir="${_log_dir:-${TMPDIR:-/tmp}}"
    nohup "$python_cmd" "$server_script" --port "$GUI_PORT" &>"$_log_dir/gathm-gui.log" &
    disown $! 2>/dev/null || true

    # Wait up to 8 s for the server to respond
    local i=0
    while (( i < 8 )); do
        sleep 1
        if curl -s --max-time 2 "$GUI_URL/" &>/dev/null; then
            ok "GUI server started at ${CYAN}${GUI_URL}${RESET}"
            return 0
        fi
        i=$((i + 1))
    done

    warn "GUI server did not respond in time — check $_log_dir/gathm-gui.log"
    return 1
}

# --- Launch Pilot and open GUI browser after install ---
launch_post_install() {
    local platform="${1:-linux}"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${BOLD}${GREEN}Launching Gathm...${RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # 1. Start GUI server in background
    if start_gui_server; then
        # 2. Open GUI in default browser
        info "Opening GUI in browser..."
        open_url "$GUI_URL" "$platform"
        echo -e "  ${CYAN}GUI:${RESET} $GUI_URL"
    fi

    echo ""

    # 3. Determine how to launch Pilot
    local python_cmd=""
    command -v python3 &>/dev/null && python_cmd="python3"
    command -v python &>/dev/null && [[ -z "$python_cmd" ]] && python_cmd="python"

    local pilot_main="$SCRIPT_DIR/pilot/main.py"

    if [[ -z "$python_cmd" ]] || [[ ! -f "$pilot_main" ]]; then
        warn "Pilot not available — start it manually: python3 pilot/main.py"
        return 0
    fi

    echo -e "${BOLD}Starting Pilot AI...${RESET}"
    echo -e "  ${YELLOW}(Press Ctrl+C or type /exit to quit Pilot)${RESET}"
    echo ""

    # Hand off to Pilot in the current terminal
    cd "$SCRIPT_DIR"
    exec "$python_cmd" "$pilot_main"
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

    # LLM backend + model
    local llm_backend="ollama"
    [[ -f "$HOME/.gathm/llm_backend" ]] && llm_backend=$(cat "$HOME/.gathm/llm_backend")
    case "$llm_backend" in
        gemini)
            ok "LLM backend: Google Gemini (free tier)"
            if [[ -f "$HOME/.gathm/model" ]]; then
                ok "LLM model: $(cat "$HOME/.gathm/model")"
            fi
            ;;
        ollama)
            if command -v ollama &>/dev/null; then
                ok "LLM backend: Ollama ($(ollama --version 2>&1 | head -1))"
            else
                warn "LLM backend: Ollama not installed (optional — needed for Pilot AI)"
            fi
            if [[ -f "$HOME/.gathm/model" ]]; then
                ok "LLM model: $(cat "$HOME/.gathm/model")"
            fi
            ;;
        *)
            warn "LLM backend: not configured (Pilot AI unavailable)"
            ;;
    esac

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
    # Expose platform globally so sub-functions (e.g. Gemini fallback) can use it
    _GATHM_PLATFORM="$platform"

    # Check internet first — determines what we can install
    check_internet

    setup_termux_storage "$platform"
    install_deps "$platform"
    setup_files
    install_engineer_deps
    install_pilot_deps
    setup_shortcuts "$platform"

    # Install Ollama + best-fit model (requires internet)
    # If Ollama cannot be installed, falls back to Google Gemini free tier
    install_ollama "$platform"
    install_llmfit_and_select_model

    reload_shell_config
    verify

    echo ""
    echo -e "${BOLD}${GREEN}Setup Complete!${RESET}"
    echo ""

    # Start GUI + hand off to Pilot in the current terminal
    launch_post_install "$platform"
}

main "$@"
