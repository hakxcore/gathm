# Shared by install and uninstall; compatible with the macOS Bash 3.2 shell.

gathm_detect_platform() {
    local kernel
    kernel=$(uname -s 2>/dev/null || printf unknown)
    if [[ "${PREFIX:-}" == */com.termux/files/usr || "${OSTYPE:-}" == linux-android* \
            || -d /data/data/com.termux/files/usr ]]; then
        printf 'termux\n'; return
    fi
    case "$kernel" in
        Darwin) printf 'macos\n' ;;
        MINGW*|MSYS*|CYGWIN*) printf 'windows\n' ;;
        Android) printf 'termux\n' ;;
        Linux)
            if grep -qi microsoft /proc/version 2>/dev/null; then printf 'wsl\n'
            else gathm_linux_distribution; fi ;;
        *) printf 'unknown\n' ;;
    esac
}

# WSL can host Fedora, Arch, or Alpine as well as Debian/Ubuntu.
gathm_linux_distribution() {
    if command -v apt-get >/dev/null 2>&1; then printf 'debian\n'
    elif command -v dnf >/dev/null 2>&1; then printf 'fedora\n'
    elif command -v pacman >/dev/null 2>&1; then printf 'arch\n'
    elif command -v apk >/dev/null 2>&1; then printf 'alpine\n'
    elif command -v zypper >/dev/null 2>&1; then printf 'opensuse\n'
    else printf 'linux\n'; fi
}

gathm_shortcut_owned() {
    local path="$1" target="$2" link
    if [[ -L "$path" ]]; then
        link=$(readlink "$path") || return 1
        [[ "$link" == "$target" ]]
    elif [[ -f "$path" ]]; then
        # MSYS can implement ln by copying; recognize an unchanged copy too.
        cmp -s "$path" "$target" && return 0
        grep -Fxq "# Gathm install: $SCRIPT_DIR" "$path" 2>/dev/null && return 0
        # Compatibility with the old two-line API wrapper.
        [[ "$target" == "$SCRIPT_DIR/api/server.py" ]] &&
            [[ "$(head -n 1 "$path")" == '#!'*bash ]] &&
            grep -Fq "$(printf '%q' "$target")" "$path"
    else
        return 1
    fi
}

gathm_run_privileged() {
    if [[ "$(id -u)" == 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        if [[ "${GATHM_NON_INTERACTIVE:-0}" == 1 || ! -t 0 ]]; then
            sudo -n "$@"
        else
            sudo "$@"
        fi
    elif command -v doas >/dev/null 2>&1; then
        if [[ "${GATHM_NON_INTERACTIVE:-0}" == 1 || ! -t 0 ]]; then
            doas -n "$@"
        else
            doas "$@"
        fi
    else
        printf 'No privilege helper available; run this package operation as root: %s\n' "$*" >&2
        return 1
    fi
}

# The checksum records the exact binary we installed, without claiming a
# pre-existing or subsequently replaced audio runtime as ours.
gathm_audio_owned() {
    local path="$1" record="$HOME/.gathm/audiocpp-install" owner checksum
    [[ -f "$path" && ! -L "$path" && -f "$record" ]] || return 1
    owner=$(sed -n '1p' "$record")
    checksum=$(sed -n '2p' "$record")
    [[ "$owner" == "$SCRIPT_DIR" && "$checksum" == "$(cksum < "$path")" ]]
}

# Publish an already-validated temporary file in the destination directory.
# mv replaces a link itself; cp or redirection would overwrite its target.
gathm_publish_audio() {
    local prepared="$1" target="$HOME/.local/bin/audiocpp_cli" record
    if [[ -e "$target" || -L "$target" ]]; then
        gathm_audio_owned "$target" || {
            printf 'Preserving existing audio command: %s\n' "$target" >&2
            return 1
        }
    fi
    mkdir -p "$HOME/.gathm" || return 1
    record=$(mktemp "$HOME/.gathm/.audiocpp-install.XXXXXX") || return 1
    if { printf '%s\n' "$SCRIPT_DIR"; cksum < "$prepared"; } > "$record" \
            && chmod +x "$prepared" && mv -f "$prepared" "$target" \
            && mv -f "$record" "$HOME/.gathm/audiocpp-install"; then
        return 0
    fi
    rm -f "$record"
    return 1
}
