#!/usr/bin/env bash
# Knowing how to install what a tool needs.
#
# Gathm already tried to auto-install missing dependencies (see
# auto_install_deps in lib/recovery.bash), but it only ever guessed that the
# package is named after the command. That is true for `telnet` and false for
# every ProjectDiscovery binary, the Shodan CLI, and anything that ships under
# a different package name — so those tools failed with
# "Please install the missing dependencies and try again" and no command to run.
#
# This file holds the recipes: what to run, per platform, for the things Gathm's
# tools actually depend on. Curated deliberately rather than searched for at
# runtime — an install command is code, and code that came from a web page is
# not something to run on a user's machine unseen.

# Which platform's package manager applies.
gathm_dep_platform() {
    if [[ -n "${PREFIX:-}" && "${PREFIX}" == *com.termux* ]]; then
        echo termux; return
    fi
    case "$(uname -s 2>/dev/null)" in
        Darwin) echo darwin ;;
        Linux)  echo linux ;;
        MINGW*|MSYS*|CYGWIN*) echo windows ;;
        *)      echo unknown ;;
    esac
}

# The generic package-manager command for a package name.
gathm_pkg_command() {
    local pkg="$1"
    case "$(gathm_dep_platform)" in
        termux) echo "pkg install -y $pkg" ;;
        darwin) command -v brew >/dev/null 2>&1 \
                    && echo "brew install $pkg" \
                    || echo "install Homebrew (https://brew.sh), then: brew install $pkg" ;;
        linux)
            if command -v apt-get >/dev/null 2>&1; then echo "sudo apt-get install -y $pkg"
            elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install -y $pkg"
            elif command -v pacman >/dev/null 2>&1; then echo "sudo pacman -S --noconfirm $pkg"
            elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install -y $pkg"
            elif command -v apk >/dev/null 2>&1; then echo "apk add $pkg"
            else echo "install '$pkg' with your package manager"
            fi ;;
        windows) command -v scoop >/dev/null 2>&1 \
                    && echo "scoop install $pkg" || echo "choco install -y $pkg" ;;
        *) echo "install '$pkg' with your package manager" ;;
    esac
}

# The exact command that installs a missing dependency, or "" when there is no
# recipe (the caller then falls back to guessing the package name).
gathm_dep_hint() {
    local dep="$1"
    local platform; platform=$(gathm_dep_platform)

    case "$dep" in
        # ProjectDiscovery binaries: Go modules, not distro packages. The repo's
        # own script installs the set, so point at that rather than duplicating
        # seven module paths that would drift out of date here.
        subfinder|dnsx|httpx|naabu|katana|nuclei|uncover)
            if command -v go >/dev/null 2>&1; then
                echo "./install-projectdiscovery-tools.sh   (installs $dep into \$HOME/go/bin — add it to PATH)"
            else
                echo "install Go first ($(gathm_pkg_command golang)), then: ./install-projectdiscovery-tools.sh"
            fi
            return ;;
        shodan)
            if command -v pipx >/dev/null 2>&1; then echo "pipx install shodan"
            else echo "pip install --user shodan   (then: shodan init YOUR_API_KEY)"
            fi
            return ;;
        strix)
            echo "strix is a local binary Gathm wraps — put it on PATH, or set GATHM_STRIX_BIN"
            return ;;
        maltego)
            echo "install Maltego from https://maltego.com and put its launcher on PATH"
            return ;;
        telnet)
            # Not a package called "telnet" on Termux or most distros.
            case "$platform" in
                termux) echo "pkg install -y inetutils" ;;
                darwin) echo "brew install telnet" ;;
                *)      echo "$(gathm_pkg_command telnet)  (or inetutils)" ;;
            esac
            return ;;
        whois)
            case "$platform" in
                termux) echo "pkg install -y whois" ;;
                darwin) echo "whois ships with macOS; if missing: brew install whois" ;;
                *)      echo "$(gathm_pkg_command whois)" ;;
            esac
            return ;;
        go|golang)   echo "$(gathm_pkg_command golang)" ; return ;;
        python3)     echo "$(gathm_pkg_command python3)" ; return ;;
        ffmpeg|mpv|sox|dialog|pv|jq|curl|openssl|nmap|qrencode)
            echo "$(gathm_pkg_command "$dep")" ; return ;;
        *) echo "" ; return ;;
    esac
}

# Print an actionable line for a missing dependency. Used by check_dependencies,
# so every tool that stops for a missing command says how to get it.
gathm_dep_report() {
    local dep="$1"
    local hint; hint=$(gathm_dep_hint "$dep")
    [[ -z "$hint" ]] && hint=$(gathm_pkg_command "$dep")
    echo "       Install it with:  $hint"
}

# May Gathm install things by itself? config/agent.yaml has carried
# allow_auto_install_deps for a while but nothing read it, so auto-install was
# unconditional — including `sudo apt-get install` on a desktop. It is honoured
# now, and GATHM_AUTO_INSTALL overrides it either way.
gathm_auto_install_allowed() {
    local override="${GATHM_AUTO_INSTALL:-}"
    case "$override" in
        1|true|yes|on)   return 0 ;;
        0|false|no|off)  return 1 ;;
    esac
    local cfg="${SCRIPT_DIR_RECOVERY:-${GATHM_ROOT:-.}}/config/agent.yaml"
    [[ -f "$cfg" ]] || cfg="$(dirname "${BASH_SOURCE[0]}")/../config/agent.yaml"
    if [[ -f "$cfg" ]] && grep -qE '^\s*allow_auto_install_deps:\s*false' "$cfg"; then
        return 1
    fi
    return 0
}

# Whether installing this dependency would need sudo — worth not doing silently.
gathm_dep_needs_sudo() {
    local hint; hint=$(gathm_dep_hint "$1")
    [[ -z "$hint" ]] && hint=$(gathm_pkg_command "$1")
    [[ "$hint" == sudo* ]]
}
