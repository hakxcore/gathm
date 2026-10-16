#!/usr/bin/env bash
# No packages, processes, network, or files outside temporary fixtures touched.
set -eu
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX=$(mktemp -d)
trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/home"
env HOME="$FIX/home" GATHM_NON_INTERACTIVE=1 GATHM_INSTALL_SOURCE_ONLY=1 \
    "${BASH:-bash}" -s -- "$REPO" "$FIX" <<'TEST'
REPO="$1"; FIX="$2"
source "$REPO/install"
trap - ERR INT TERM
PASS=0; FAIL=0
assert() { if ! "$@"; then printf 'Assertion failed: %s\n' "$*" >&2; exit 1; fi; }
run() {
    if ( "$2" ); then PASS=$((PASS + 1)); printf 'ok %s\n' "$1"
    else FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; fi
}
fixture() {
    SCRIPT_DIR="$FIX/repo $1's \$tree"
    mkdir -p "$SCRIPT_DIR/lib" "$SCRIPT_DIR/pilot" "$SCRIPT_DIR/engineer" "$SCRIPT_DIR/api" "$SCRIPT_DIR/agent"
    cp "$REPO/uninstall" "$SCRIPT_DIR/uninstall"
    cp "$REPO/lib/install_common.bash" "$SCRIPT_DIR/lib/install_common.bash"
    printf '#!/usr/bin/env bash\n' > "$SCRIPT_DIR/gathm"
    cp "$SCRIPT_DIR/gathm" "$SCRIPT_DIR/agent/orchestrator.sh"
    touch "$SCRIPT_DIR/api/server.py"
    _init_venv
}
clear_fixture_home() {
    # HOME was replaced only for this test subprocess, above.
    assert test "$HOME" = "$FIX/home"
    rm -rf "$HOME/.gathm" "$HOME/.local" "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"
    mkdir -p "$HOME/.local/bin"
}
fake_python() {
    mkdir -p "$(dirname "$1")"
    cat > "$1" <<'PY'
#!/usr/bin/env bash
[[ "${1:-}" != -c ]] || exit 0
printf '%s\n' "$@"
PY
    chmod +x "$1"
}
readonly_modes() {
    clear_fixture_home
    rmdir "$HOME/.local/bin" "$HOME/.local"
    GATHM_INSTALL_SOURCE_ONLY=0 "$BASH" "$REPO/install" --help >/dev/null
    assert test ! -e "$HOME/.local"
    if GATHM_INSTALL_SOURCE_ONLY=0 "$BASH" "$REPO/install" --bad-option >/dev/null 2>&1; then exit 1; fi
    assert test ! -e "$HOME/.local"
    "$BASH" "$REPO/uninstall" --dry-run >/dev/null
    assert test ! -e "$HOME/.local"
    if "$BASH" "$REPO/uninstall" --bad-option >/dev/null 2>&1; then exit 1; fi
}
platforms() {
    unset PREFIX OSTYPE
    uname() { printf '%s\n' "$KERNEL"; }
    KERNEL=Darwin; assert test "$(detect_platform)" = macos
    for KERNEL in MINGW64_NT-10 MSYS_NT-10 CYGWIN_NT-10; do assert test "$(detect_platform)" = windows; done
    KERNEL=Android; assert test "$(detect_platform)" = termux
    KERNEL=Linux; PREFIX=/data/data/com.termux/files/usr; assert test "$(detect_platform)" = termux
    unset PREFIX
    KERNEL=FreeBSD; assert test "$(detect_platform)" = unknown
    # Mock only package-manager discovery; every other command remains real.
    KERNEL=Linux
    grep() { return 1; }
    command() { if [[ "$1" == -v ]]; then [[ "$2" == "$MANAGER" ]]; else builtin command "$@"; fi; }
    MANAGER=apt-get; assert test "$(detect_platform)" = debian
    MANAGER=dnf; assert test "$(detect_platform)" = fedora
    MANAGER=pacman; assert test "$(detect_platform)" = arch
    MANAGER=apk; assert test "$(detect_platform)" = alpine
    MANAGER=zypper; assert test "$(detect_platform)" = opensuse
    MANAGER=none; assert test "$(detect_platform)" = linux
    grep() { return 0; }; assert test "$(detect_platform)" = wsl
}
venv_repair() {
    fixture repair
    mkdir -p "$GATHM_VENV"
    _python_cmd() { echo fixture_python; }
    fixture_python() {
        [[ "$1" != -m || "$2" != venv ]] || fake_python "$3/bin/python3"
    }
    assert _ensure_venv
    assert test -x "$GATHM_VENV/bin/python3"
    mkdir -p "$SCRIPT_DIR/engineer/venv"
    assert _ensure_engineer_venv
    assert test -x "$ENGINEER_VENV/bin/python3"
    _ensure_venv() { return 1; }
    fixture_python() { touch "$FIX/unwanted-system-pip"; }
    if _venv_pip example; then exit 1; fi
    assert test ! -e "$FIX/unwanted-system-pip"
}
shortcut_roundtrip() {
    clear_fixture_home; fixture shortcuts
    fake_python "$GATHM_VENV/Scripts/python.exe"
    printf 'home = fixture\n' > "$GATHM_VENV/pyvenv.cfg"
    mkdir -p "$SCRIPT_DIR/engineer/venv"
    touch "$SCRIPT_DIR/engineer/venv/pyvenv.cfg"
    SHELL=/bin/zsh
    assert setup_shortcuts macos
    assert test -f "$HOME/.zshrc"
    assert test "$("$BASH" "$HOME/.local/bin/gathm-api" --port 'hello world')" = "$(printf '%s\n' "$SCRIPT_DIR/api/server.py" --port 'hello world')"
    # Existing user PATH lines and their permissions survive block cleanup.
    printf 'export PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
    chmod 600 "$HOME/.zshrc"
    before_mode=$(ls -l "$HOME/.zshrc" | awk '{print $1}')
    mkdir -p "$HOME/.gathm/models"; touch "$HOME/.gathm/models/model.gguf"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --dry-run
    assert test -f "$HOME/.gathm/models/model.gguf"
    if "$BASH" "$SCRIPT_DIR/uninstall" >/dev/null 2>&1; then exit 1; fi
    assert test -e "$HOME/.local/bin/gathm"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes --keep-data
    assert test -f "$HOME/.gathm/models/model.gguf"
    assert test ! -e "$HOME/.local/bin/gathm-api"
    assert test ! -e "$SCRIPT_DIR/pilot/venv"
    assert test ! -e "$SCRIPT_DIR/engineer/venv"
    assert test "$(grep -Fc 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zshrc")" = 1
    assert test "$(ls -l "$HOME/.zshrc" | awk '{print $1}')" = "$before_mode"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
    assert test ! -e "$HOME/.gathm"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
}
foreign_shortcuts() {
    clear_fixture_home; fixture foreign
    fake_python "$GATHM_VENV/bin/python3"
    printf 'my unrelated command\n' > "$HOME/.local/bin/gathm-api"
    if setup_shortcuts windows; then exit 1; fi
    assert test ! -e "$HOME/.local/bin/gathm"
    mkdir -p "$HOME/.gathm/models"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
    assert test "$(cat "$HOME/.local/bin/gathm-api")" = 'my unrelated command'
    assert test -d "$HOME/.gathm/models"
}
path_blocks() {
    clear_fixture_home; fixture rc
    printf '# Gathm Framework\nexport PATH="$HOME/.local/bin:$PATH"\n' > "$HOME/.bashrc"
    printf '# Gathm Framework\n# unrelated comment\nexport PATH="$HOME/.local/bin:$PATH"\n' > "$HOME/.profile"
    cp "$HOME/.profile" "$FIX/profile-before"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
    assert test ! -s "$HOME/.bashrc"
    assert cmp -s "$HOME/.profile" "$FIX/profile-before"
}
audio_ownership() {
    clear_fixture_home; fixture audio
    local src="$SCRIPT_DIR/audio build \$d's"
    mkdir -p "$src/build/bin"
    cat > "$src/build/bin/audiocpp_cli" <<'AUDIO'
#!/usr/bin/env bash
# Succeed only in the build directory; forces the quoted wrapper path.
[[ "$0" == */build/bin/audiocpp_cli ]]
AUDIO
    chmod +x "$src/build/bin/audiocpp_cli"
    assert _audiocpp_install_bin "$src"
    assert "$BASH" "$HOME/.local/bin/audiocpp_cli" --help
    assert gathm_audio_owned "$HOME/.local/bin/audiocpp_cli"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
    assert test ! -e "$HOME/.local/bin/audiocpp_cli"
    printf 'keep me\n' > "$HOME/.local/bin/audiocpp_cli"
    if _audiocpp_install_bin "$src"; then exit 1; fi
    assert test "$(cat "$HOME/.local/bin/audiocpp_cli")" = 'keep me'
}
required_failure() {
    clear_fixture_home; fixture main
    detect_platform() { echo macos; }
    check_internet() { GATHM_ONLINE=false; }
    check_and_install_python() { :; }; install_deps() { :; }; setup_files() { :; }
    install_llamacpp() { :; }; _install_ollama_fallback() { :; }; select_and_pull_llm_model() { :; }
    install_pilot_deps() { :; }; install_playwright_browser() { :; }; install_audio_cpp() { :; }
    setup_shortcuts() { :; }; reload_shell_config() { :; }; step_summary() { :; }
    launch_post_install() { touch "$FIX/unexpected-launch"; }
    verify() { return 1; }
    if main > "$FIX/main-output"; then exit 1; fi
    assert test ! -e "$FIX/unexpected-launch"
    if grep -q 'Setup Complete!' "$FIX/main-output"; then exit 1; fi
    verify() { return 0; }
    assert main > "$FIX/main-output"
    assert test ! -e "$FIX/unexpected-launch"
}
privileges() {
    id() { echo 0; }
    marker() { printf '%s\n' "$*"; }
    assert test "$(gathm_run_privileged marker root-package-install)" = root-package-install
    id() { echo 1000; }
    sudo() { printf '%s\n' "$*"; }
    assert test "$(gathm_run_privileged marker package-install)" = '-n marker package-install'
}
noninteractive_gemini() {
    open_url() { touch "$FIX/unwanted-browser"; }
    assert setup_gemini_fallback macos
    assert test ! -e "$FIX/unwanted-browser"
}
pipx_shortcut() {
    clear_fixture_home; fixture pipx
    fake_python "$GATHM_VENV/bin/python3"
    printf 'pipx launcher\n' > "$HOME/.local/bin/gathm"
    GATHM_INSTALL_KIND=pipx
    assert setup_shortcuts macos
    assert test "$(cat "$HOME/.local/bin/gathm")" = 'pipx launcher'
    assert test -f "$HOME/.local/bin/gathm-api"
}
wsl_packages() {
    fixture wsl
    gathm_linux_distribution() { echo fedora; }
    gathm_run_privileged() { printf '%s\n' "$*" >> "$FIX/package-ops"; }
    # Core tools are present in the fixture; do not query host package managers.
    command() { if [[ "$1" == -v ]]; then return 0; else builtin command "$@"; fi; }
    assert install_deps wsl
    assert grep -q '^dnf install' "$FIX/package-ops"
    if grep -q apt-get "$FIX/package-ops"; then exit 1; fi
}
uninstall_failures() {
    clear_fixture_home; fixture failure
    mkdir -p "$HOME/.gathm" "$FIX/fail-bin"
    printf '#!/usr/bin/env bash\nexit 1\n' > "$FIX/fail-bin/rm"
    chmod +x "$FIX/fail-bin/rm"
    if PATH="$FIX/fail-bin:$PATH" "$BASH" "$SCRIPT_DIR/uninstall" --yes > "$FIX/uninstall-failed" 2>&1; then exit 1; fi
    assert grep -q 'Uninstall incomplete' "$FIX/uninstall-failed"
    assert test -d "$HOME/.gathm"
}
verification_failure() {
    clear_fixture_home; fixture verify
    _python_cmd() { echo check_python; }
    check_python() {
        if [[ "$1" == - ]]; then
            printf 'pydantic\nimport pydantic_core: ImportError: fixture failure\n'
        fi
    }
    _audiocpp_bin() { return 1; }
    _audiocpp_src_dir() { echo "$FIX/no-audio"; }
    GATHM_ONLINE=false
    if verify > "$FIX/verify-output"; then exit 1; fi
    assert grep -q 'Pilot runtime missing/broken: pydantic' "$FIX/verify-output"
    assert grep -q 'required issue(s) found' "$FIX/verify-output"
}
windows_launchers() {
    clear_fixture_home; fixture windows
    fake_python "$GATHM_VENV/Scripts/python.exe"
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$0" "$@"\n' > "$SCRIPT_DIR/gathm"
    cp "$SCRIPT_DIR/gathm" "$SCRIPT_DIR/agent/orchestrator.sh"
    assert setup_shortcuts windows
    assert test "$("$BASH" "$HOME/.local/bin/gathm" 'hello world')" = "$(printf '%s\n' "$SCRIPT_DIR/gathm" 'hello world')"
    assert test "$("$BASH" "$HOME/.local/bin/gathm-agent" 'hello world')" = "$(printf '%s\n' "$SCRIPT_DIR/agent/orchestrator.sh" 'hello world')"
    assert "$BASH" "$SCRIPT_DIR/uninstall" --yes
    assert test ! -e "$HOME/.local/bin/gathm"
    assert test ! -e "$HOME/.local/bin/gathm-agent"
}
private_temporary_failures() {
    clear_fixture_home; fixture tempfiles
    _llamacpp_release_asset() { printf 'https://invalid.test/archive.zip\ttest\n'; }
    curl() { touch "$FIX/unwanted-download"; return 1; }
    mktemp() { return 1; }
    if _llamacpp_install_prebuilt linux; then exit 1; fi
    assert test ! -e "$FIX/unwanted-download"
    mkdir -p "$SCRIPT_DIR/external/sentencepiece/src"
    local spm="$SCRIPT_DIR/external/sentencepiece/src/CMakeLists.txt"
    printf 'add_library(sentencepiece-static STATIC source.cc)\n' > "$spm"
    cp "$spm" "$FIX/cmake-before"
    if _audiocpp_patch_termux_sentencepiece "$SCRIPT_DIR"; then exit 1; fi
    assert cmp -s "$spm" "$FIX/cmake-before"
    assert test ! -e "$spm.gathm.tmp"
}
run 'temporary allocation failures never use predictable fallback paths' private_temporary_failures
run 'Windows launchers resolve the checkout without symlink privileges' windows_launchers
run 'pipx setup preserves the package-managed launcher' pipx_shortcut
run 'WSL uses its guest distro package manager' wsl_packages
run 'uninstall reports removal errors with a nonzero exit' uninstall_failures
run 'verification detects broken imports as required failures' verification_failure
run 'help, dry-run, and invalid flags do not install anything' readonly_modes
run 'platform matrix: macOS, Termux, Windows Bash, WSL, distro families' platforms
run 'repair incomplete venvs without a system-pip fallback' venv_repair
run 'Windows interpreter paths; install/uninstall round trip with model retention' shortcut_roundtrip
run 'preserve commands and shared data belonging to another installation' foreign_shortcuts
run 'remove exact PATH blocks, including the only lines in a file' path_blocks
run 'audio wrapper quoting and ownership' audio_ownership
run 'failed verification returns failure; unattended success does not launch' required_failure
run 'root needs no sudo; unattended sudo cannot prompt' privileges
run 'unattended Gemini setup cannot loop waiting for input' noninteractive_gemini
printf 'passed=%s failed=%s\n' "$PASS" "$FAIL"
exit "$FAIL"
TEST
