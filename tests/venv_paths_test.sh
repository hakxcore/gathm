#!/usr/bin/env bash
# Exercise POSIX and native Windows venv layouts without installing packages.
# Fake interpreters record argv; all generated shortcuts stay in the fixture.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FIX="$(mktemp -d)" || exit 1
trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/home"

env HOME="$FIX/home" GATHM_INSTALL_SOURCE_ONLY=1 bash -s -- "$REPO" "$FIX" <<'TEST'
REPO="$1"
FIX="$2"
source "$REPO/install"
set +e
trap - ERR INT TERM
PASS=0; FAIL=0
unset GATHM_PYTHON
check() {
    if [[ "$2" == "$3" ]]; then
        PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"
    else
        FAIL=$((FAIL + 1)); printf '  FAIL %s\n  got: <%s>\n  expected: <%s>\n' "$1" "$2" "$3"
    fi
}
fake_python() {
    mkdir -p "$(dirname "$1")"
    cat > "$1" <<'PYTHON'
#!/usr/bin/env bash
[[ "${1:-}" == "-c" ]] && exit 0
printf '%s\n' "$0" "$@"
PYTHON
    chmod +x "$1"
}

SCRIPT_DIR="$FIX/checkout with spaces"
mkdir -p "$SCRIPT_DIR/pilot" "$SCRIPT_DIR/agent" "$SCRIPT_DIR/api"
touch "$SCRIPT_DIR/gathm" "$SCRIPT_DIR/agent/orchestrator.sh" "$SCRIPT_DIR/api/server.py"
cp "$REPO/pilot/run.sh" "$SCRIPT_DIR/pilot/run.sh"
_init_venv

fake_python "$GATHM_VENV/bin/python3"
check "POSIX/Termux python3 layout" "$(_venv_python "$GATHM_VENV")" "$GATHM_VENV/bin/python3"
check "POSIX packages use venv Python's pip" "$(_venv_pip -r 'requirements file.txt')" \
    "$(printf '%s\n' "$GATHM_VENV/bin/python3" -m pip install -r 'requirements file.txt')"
check "direct TUI keeps the POSIX interpreter" \
    "$(bash "$SCRIPT_DIR/pilot/run.sh" 'hello world')" \
    "$(printf '%s\n' "$GATHM_VENV/bin/python3" main.py 'hello world')"
mv "$GATHM_VENV/bin/python3" "$GATHM_VENV/bin/python"
check "POSIX python-only layout" "$(_venv_python "$GATHM_VENV")" "$GATHM_VENV/bin/python"
fake_python "$GATHM_VENV/Scripts/python.exe"
check "existing POSIX runtime still wins" "$(_venv_python "$GATHM_VENV")" "$GATHM_VENV/bin/python"
rm "$GATHM_VENV/bin/python"
check "Windows Scripts layout" "$(_venv_python "$GATHM_VENV")" "$GATHM_VENV/Scripts/python.exe"
check "Windows packages stay in the venv" "$(_venv_pip -r 'requirements file.txt')" \
    "$(printf '%s\n' "$GATHM_VENV/Scripts/python.exe" -m pip install -r 'requirements file.txt')"
_init_engineer_venv
fake_python "$ENGINEER_VENV/Scripts/python.exe"
check "Engineer packages use its Windows venv" "$(_engineer_venv_pip example-package)" \
    "$(printf '%s\n' "$ENGINEER_VENV/Scripts/python.exe" -m pip install -q example-package)"
_venv_python "$FIX/missing" >/dev/null
check "missing venv is reported" "$?" "1"

# Load the launcher's selector only; sourcing the complete launcher starts it.
eval "$(sed -n '/^pick_python() {$/,/^}$/p' "$REPO/gathm")"
INSTALL_DIR="$SCRIPT_DIR"
unset GATHM_PYTHON
check "GUI launcher selects Windows venv" "$(pick_python)" "$GATHM_VENV/Scripts/python.exe"
check "direct TUI uses Windows venv and preserves arguments" \
    "$(bash "$SCRIPT_DIR/pilot/run.sh" 'hello world')" \
    "$(printf '%s\n' "$GATHM_VENV/Scripts/python.exe" main.py 'hello world')"
fake_python "$FIX/explicit python"
export GATHM_PYTHON="$FIX/explicit python"
check "GUI respects explicit interpreter" "$(pick_python)" "$GATHM_PYTHON"
check "TUI respects explicit interpreter" \
    "$(bash "$SCRIPT_DIR/pilot/run.sh" 'hello world')" \
    "$(printf '%s\n' "$GATHM_PYTHON" main.py 'hello world')"
unset GATHM_PYTHON

# The generated wrapper must quote both interpreter and checkout paths.
setup_shortcuts windows >/dev/null
check "API shortcut handles Windows layout and spaces" \
    "$(bash "$HOME/.local/bin/gathm-api" --port 8912)" \
    "$(printf '%s\n' "$GATHM_VENV/Scripts/python.exe" "$SCRIPT_DIR/api/server.py" --port 8912)"
printf 'passed=%s failed=%s\n' "$PASS" "$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
TEST
