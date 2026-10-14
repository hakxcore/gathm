#!/usr/bin/env bash
# Installation hardening, with disposable homes and stubbed downloads/processes.
set -euo pipefail
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
assert() { "$@" || { printf 'Assertion failed: %s\n' "$*" >&2; exit 1; }; }
run() {
    if ( "$2" ); then PASS=$((PASS+1)); printf 'ok %s\n' "$1"
    else FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$1"; fi
}
fixture() {
    SCRIPT_DIR="$FIX/$1 checkout"
    LOCAL_BIN="$HOME/.local/bin"
    mkdir -p "$SCRIPT_DIR/api" "$LOCAL_BIN" "$HOME/.gathm"
    touch "$SCRIPT_DIR/api/server.py"
}
temp_failures() {
    fixture temporary
    _llamacpp_release_asset() { printf 'https://example.invalid/runtime.zip\tb123\n'; }
    mktemp() { return 1; }
    curl() { touch "$FIX/unexpected-download"; }
    AUDIOCPP_BIN_URL=https://example.invalid/audio
    if _llamacpp_install_prebuilt macos; then exit 1; fi
    if _audiocpp_fetch_prebuilt; then exit 1; fi
    assert test ! -e "$FIX/unexpected-download"
}
checksum_failure() {
    fixture checksum
    AUDIOCPP_BIN_URL=https://example.invalid/audio
    AUDIOCPP_BIN_SHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    run_with_spinner() { shift 2; "$@"; }
    curl() {
        local dest=""
        while (( $# )); do
            if [[ "$1" == -o ]]; then dest="$2"; shift; fi
            shift
        done
        printf 'fixture binary\n' > "$dest"
    }
    command() {
        if [[ "$1" == -v && ( "$2" == sha256sum || "$2" == shasum ) ]]; then return 1; fi
        builtin command "$@"
    }
    _install_python() { return 1; }
    _audiocpp_binary_usable() { touch "$FIX/unverified-executed"; }
    if _audiocpp_fetch_prebuilt; then exit 1; fi
    assert test ! -e "$FIX/unverified-executed"
}
dotenv_privacy() {
    fixture dotenv
    printf 'UNCHANGED=fixture\n' > "$SCRIPT_DIR/.env"
    chmod 644 "$SCRIPT_DIR/.env"
    _audiocpp_write_env GATHM_LLAMACPP_MODEL 'a path with spaces'
    assert grep -Fxq 'UNCHANGED=fixture' "$SCRIPT_DIR/.env"
    assert _install_python -c 'import os,sys; assert os.name == "nt" or os.stat(sys.argv[1]).st_mode & 0o777 == 0o600' "$SCRIPT_DIR/.env"
    printf 'outside fixture\n' > "$FIX/external-env"
    rm "$SCRIPT_DIR/.env"
    ln -s "$FIX/external-env" "$SCRIPT_DIR/.env"
    if _audiocpp_write_env GATHM_LLM_BACKEND llamacpp; then exit 1; fi
    assert grep -Fxq 'outside fixture' "$FIX/external-env"
}
bootstrap() {
    fixture bootstrap
    _ensure_venv() { return 0; }
    _venv_python() { echo fixture_python; }
    fixture_python() { return "$PROBE_RC"; }
    _venv_pip() { printf '%s\n' "$@" > "$FIX/bootstrap-args"; }
    PROBE_RC=0
    _upgrade_pilot_toolchain
    assert test ! -e "$FIX/bootstrap-args"
    PROBE_RC=1; GATHM_ONLINE=true
    _upgrade_pilot_toolchain
    assert grep -Fxq 'pip>=26.2' "$FIX/bootstrap-args"
    assert grep -Fxq 'setuptools>=83.0.0' "$FIX/bootstrap-args"
    rm "$FIX/bootstrap-args"
    GATHM_ONLINE=false
    if _upgrade_pilot_toolchain; then exit 1; fi
    assert test ! -e "$FIX/bootstrap-args"
}
ownership() {
    fixture ownership
    uname() { echo Darwin; }
    ps() { [[ "$BIRTH" != missing ]] && printf '%s\n' "$BIRTH"; }
    BIRTH='Tue Oct 13 12:00:00 2026'
    gathm_record_process "$FIX/owned.pid" 12345 /repo-one
    assert gathm_process_owned "$FIX/owned.pid" 12345 /repo-one
    if gathm_process_owned "$FIX/owned.pid" 12345 /repo-two; then exit 1; fi
    if gathm_process_owned "$FIX/owned.pid" 54321; then exit 1; fi
    BIRTH='Tue Oct 13 12:00:01 2026'
    if gathm_process_owned "$FIX/owned.pid" 12345; then exit 1; fi
    BIRTH=missing
    if gathm_process_owned "$FIX/owned.pid" 12345; then exit 1; fi
    if gathm_process_birth -1; then exit 1; fi
    if gathm_process_birth 1; then exit 1; fi
}
windows_ownership() {
    fixture windows
    uname() { echo MINGW64_NT-10; }
    WINPID=4321
    ps() { printf 'PID PPID PGID WINPID TTY UID STIME COMMAND\nI 12345 1 12345 %s ? 1000 12:00 python\n' "$WINPID"; }
    powershell.exe() { printf '987654321\r\n'; }
    assert test "$(gathm_process_birth 12345)" = '4321:987654321'
    gathm_record_process "$FIX/windows.pid" 12345
    WINPID=5432
    if gathm_process_owned "$FIX/windows.pid" 12345; then exit 1; fi
}
foreign_gui() {
    fixture gui
    _venv_python() { echo fixture_python; }
    fixture_python() { return 0; }
    curl() { return 0; }
    gathm_process_owned() { return 1; }
    pgrep() { touch "$FIX/unexpected-pgrep"; }
    kill() { touch "$FIX/unexpected-kill"; }
    assert start_gui_server
    assert test ! -e "$FIX/unexpected-kill"
    assert test ! -e "$FIX/unexpected-pgrep"
}
release_checksum() {
    fixture release
    _llamacpp_release_asset() { printf 'https://example.invalid/runtime.zip\tb123\tsha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'; }
    curl() {
        local dest=""
        while (( $# )); do
            if [[ "$1" == -o ]]; then dest="$2"; shift; fi
            shift
        done
        printf 'fixture archive\n' > "$dest"
    }
    _llamacpp_unpack() { touch "$FIX/unverified-unpack"; }
    if _llamacpp_install_prebuilt macos; then exit 1; fi
    assert test ! -e "$FIX/unverified-unpack"
}
partial_ollama() {
    fixture ollama
    step_begin "Fixture Ollama download"
    GATHM_ONLINE=true
    command() {
        if [[ "$1" == -v && "$2" == ollama ]]; then return 1; fi
        builtin command "$@"
    }
    curl() {
        local dest=""
        while (( $# )); do
            if [[ "$1" == -o ]]; then dest="$2"; shift; fi
            shift
        done
        printf 'touch %q\n' "$FIX/partial-installer-executed" > "$dest"
        return 22
    }
    if install_ollama linux; then exit 1; fi
    assert test ! -e "$FIX/partial-installer-executed"
}
run 'temporary allocation failures cannot trigger a download' temp_failures
run 'an unverifiable requested checksum cannot execute a binary' checksum_failure
run 'dotenv writes are private and preserve symlink targets' dotenv_privacy
run 'patched packaging tools are required before dependency installation' bootstrap
run 'process records bind PID and creation time; invalid PID rejected' ownership
run 'Windows records bind the MSYS PID to the native process creation time' windows_ownership
run 'installer cannot restart foreign servers by matching their script name' foreign_gui
run 'mismatched release digests reject an archive before unpacking' release_checksum
run 'failed downloads never execute a partial Ollama installer' partial_ollama
printf 'passed=%s failed=%s\n' "$PASS" "$FAIL"
exit "$FAIL"
TEST
