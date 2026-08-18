from pathlib import Path

p = Path("install")
s = p.read_text()

def replace_function(source, name, replacement):
    marker = name + "() {"
    start = source.find(marker)
    if start == -1:
        raise SystemExit(f"ERROR: {name}() not found")

    # Find the closing brace of the function using brace counting.
    depth = 0
    i = start

    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                return source[:start] + replacement.rstrip() + source[end:]

        i += 1

    raise SystemExit(f"ERROR: could not find end of {name}()")


# ------------------------------------------------------------
# GUI dependencies
# ------------------------------------------------------------

new_gui = r'''
_install_gui_deps() {
    # Android/Termux does not need the Gathm GUI server.
    #
    # FastAPI -> Pydantic -> pydantic-core requires native components
    # that we intentionally do not install in the lightweight Termux
    # runtime.
    if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
        info "Termux: skipping GUI dependencies (FastAPI/uvicorn)"
        return 0
    fi

    local gui_req="$SCRIPT_DIR/webui/requirements.txt"

    if [[ -f "$gui_req" ]]; then
        info "Installing GUI dependencies..."
        if _venv_pip -r "$gui_req"; then
            ok "GUI dependencies installed"
        else
            warn "GUI dependency installation failed"
        fi
    else
        if _venv_pip install fastapi uvicorn; then
            ok "GUI dependencies installed"
        else
            warn "GUI dependency installation failed"
        fi
    fi
}
'''

s = replace_function(s, "_install_gui_deps", new_gui)


# ------------------------------------------------------------
# Playwright / Chromium
# ------------------------------------------------------------

new_browser = r'''
install_playwright_browser() {
    # Chromium is optional on Android/Termux.
    # Do not download ~100+ MB during the normal Gathm installation.
    if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
        info "Termux: skipping Playwright/Chromium browser engine"
        return 0
    fi

    local pw_cmd=""

    if [[ -x "$GATHM_VENV/bin/playwright" ]]; then
        pw_cmd="$GATHM_VENV/bin/playwright"
    elif command -v playwright >/dev/null 2>&1; then
        pw_cmd="playwright"
    else
        warn "playwright CLI not found — skipping Chromium download"
        return 0
    fi

    info "Installing Playwright Chromium (browser engine for Pilot)..."

    if "$pw_cmd" install chromium --with-deps 2>/dev/null; then
        ok "Playwright Chromium installed"
    elif "$pw_cmd" install chromium 2>/dev/null; then
        ok "Playwright Chromium installed"
    else
        warn "Playwright Chromium installation failed"
    fi
}
'''

s = replace_function(s, "install_playwright_browser", new_browser)


# ------------------------------------------------------------
# Post-install launcher
# ------------------------------------------------------------

new_launch = r'''
launch_post_install() {
    local platform="${1:-unknown}"

    # Termux uses the CLI-only runtime.
    # Do not attempt to launch FastAPI/uvicorn or desktop browser tools.
    if [[ "$platform" == "termux" ]]; then
        echo
        echo -e "${BOLD}Starting Gathm on Termux...${RESET}"
        echo -e "${CYAN}GUI server: skipped (Android/Termux)${RESET}"
        echo -e "${CYAN}Browser engine: skipped (Android/Termux)${RESET}"
        echo

        local pilot_run="$SCRIPT_DIR/pilot/run.sh"
        local pilot_main="$SCRIPT_DIR/pilot/main.py"

        if [[ -f "$pilot_run" ]]; then
            bash "$pilot_run"
        elif [[ -f "$pilot_main" ]]; then
            "$GATHM_VENV/bin/python3" "$pilot_main"
        else
            warn "Pilot not available — start it manually"
        fi

        return 0
    fi

    # Existing desktop/server behavior.
    if declare -F start_gui_server >/dev/null 2>&1; then
        start_gui_server
    fi

    local pilot_run="$SCRIPT_DIR/pilot/run.sh"
    local pilot_main="$SCRIPT_DIR/pilot/main.py"

    echo -e "${BOLD}Starting Pilot AI...${RESET}"

    if [[ -f "$pilot_run" ]]; then
        bash "$pilot_run"
    elif [[ -f "$pilot_main" ]]; then
        "$GATHM_VENV/bin/python3" "$pilot_main"
    else
        warn "Pilot not available — start it manually"
    fi
}
'''

s = replace_function(s, "launch_post_install", new_launch)


# ------------------------------------------------------------
# Verification messages
# ------------------------------------------------------------

s = s.replace(
'''        if [[ -x "$GATHM_VENV/bin/python3" ]] && "$GATHM_VENV/bin/python3" -c "import fastapi, uvicorn" 2>/dev/null; then
            ok "GUI server dependencies (fastapi, uvicorn)"
        else
            warn "GUI server dependencies missing (GUI won't start; Pilot CLI still works)"
            note_missing "GUI deps (fastapi/uvicorn)" "pip install fastapi uvicorn"
        fi
''',
'''        if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
            ok "GUI server: skipped on Termux"
        elif [[ -x "$GATHM_VENV/bin/python3" ]] && "$GATHM_VENV/bin/python3" -c "import fastapi, uvicorn" 2>/dev/null; then
            ok "GUI server dependencies (fastapi, uvicorn)"
        else
            warn "GUI server dependencies missing"
            note_missing "GUI deps (fastapi/uvicorn)" "pip install fastapi uvicorn"
        fi
''',
1
)

s = s.replace(
'''        if [[ -x "$GATHM_VENV/bin/playwright" ]] && "$pilot_py" -c "import playwright" 2>/dev/null; then
            ok "Playwright Chromium"
        else
            note_missing "Playwright Chromium" "playwright install chromium"
        fi
''',
'''        if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
            ok "Browser engine: skipped on Termux"
        elif [[ -x "$GATHM_VENV/bin/playwright" ]] && "$pilot_py" -c "import playwright" 2>/dev/null; then
            ok "Playwright Chromium"
        else
            note_missing "Playwright Chromium" "playwright install chromium"
        fi
''',
1
)

p.write_text(s)
print("Termux installer cleanup applied successfully.")
