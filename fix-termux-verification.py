from pathlib import Path

p = Path("install")
s = p.read_text()

start_marker = "    # ── Pilot runtime: venv + the Python packages Pilot imports at startup ──"
end_marker = "    # ── Browser engine (powers Pilot's 'browser' tool) ──"

start = s.find(start_marker)
end = s.find(end_marker, start)

if start < 0:
    raise SystemExit("ERROR: Pilot verification block not found")

if end < 0:
    raise SystemExit("ERROR: Browser verification marker not found")

replacement = """    # ── Pilot runtime ───────────────────────────────────────────────
    # Termux intentionally uses the lightweight Pilot runtime.
    # It does NOT require LangChain, LangGraph, Pydantic or pydantic-core.
    # Desktop/server platforms continue to verify the full Pilot stack.

    _init_venv
    local pilot_py="$GATHM_VENV/bin/python3"

    if [[ -x "$pilot_py" ]]; then
        if [[ "$plat" == "termux" ]]; then
            if "$pilot_py" -c "import requests, rich, prompt_toolkit, dotenv, bs4" 2>/dev/null; then
                ok "Pilot runtime (Termux lightweight)"
            else
                warn "Termux Pilot runtime incomplete"
                note_missing "Pilot Python deps" "re-run ./install"
            fi
        else
            local missing_mods
            missing_mods=$("$pilot_py" - <<'PYINNER' 2>/dev/null
mods = {
    "pydantic_core": "pydantic",
    "langchain_core": "langchain-core",
    "langgraph": "langgraph",
    "rich": "rich",
    "dotenv": "python-dotenv",
    "prompt_toolkit": "prompt_toolkit",
    "bs4": "beautifulsoup4",
    "requests": "requests",
}
missing = []
for m, pkg in mods.items():
    try:
        __import__(m)
    except Exception:
        missing.append(pkg)
print(" ".join(missing))
PYINNER
)

            if [[ -z "$missing_mods" ]]; then
                ok "Pilot runtime (venv + dependencies)"
            else
                warn "Pilot runtime missing/broken: $missing_mods"
                note_missing "Pilot Python deps" "pip install -r pilot/requirements.txt"
            fi
        fi

        if [[ "$plat" == "termux" ]]; then
            ok "GUI server: skipped on Termux"
        elif "$pilot_py" -c "import fastapi, uvicorn" 2>/dev/null; then
            ok "GUI server dependencies (fastapi, uvicorn)"
        else
            warn "GUI server dependencies missing"
            note_missing "GUI deps (fastapi/uvicorn)" "pip install fastapi uvicorn"
        fi
    else
        warn "Pilot venv not found"
        note_missing "Pilot venv" "re-run ./install"
    fi

"""

s = s[:start] + replacement + s[end:]

old = """    if [[ "$plat" == "termux" ]]; then
        if command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
            ok "Browser engine (system Chromium)"
        else
            warn "Chromium not installed (browser control limited to open/fetch)"
            note_missing "Chromium (browser tool)" "pkg install tur-repo x11-repo && pkg install chromium"
        fi
    else
"""

new = """    if [[ "$plat" == "termux" ]]; then
        ok "Browser engine: skipped on Termux"
    else
"""

if old not in s:
    raise SystemExit("ERROR: Termux browser verification block not found")

s = s.replace(old, new, 1)

p.write_text(s)
print("Termux verification cleaned up.")
