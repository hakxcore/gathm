#!/usr/bin/env bash
# Reinstall regressions: use the configured local runtime and weights without
# downloading another model or changing backends. All state is disposable;
# package installation, model pulls, and network downloads are forbidden.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FIX="$(mktemp -d)" || exit 1
export HOME="$FIX/source-home"
mkdir -p "$HOME"
export GATHM_INSTALL_SOURCE_ONLY=1
# shellcheck disable=SC1090
source "$REPO/install"
set +e
trap - ERR INT TERM
trap 'rm -rf "$FIX"' EXIT

PASS=0
FAIL=0

eq() {
    [[ "$2" == "$3" ]] && return 0
    printf '  %s: got <%s>, expected <%s>\n' "$1" "$2" "$3" >&2
    return 1
}

gguf() {
    mkdir -p "$(dirname "$1")"
    printf 'GGUF\003\000\000\000fixture weights' > "$1"
}

binary() {
    mkdir -p "$(dirname "$1")"
    printf '#!/bin/sh\nprintf "fixture llama-server\\n"\n' > "$1"
    chmod +x "$1"
}

fixture() {
    export HOME="$FIX/$1"
    SCRIPT_DIR="$HOME/repo"
    mkdir -p "$SCRIPT_DIR" "$HOME/.gathm"
    unset GATHM_LLAMACPP_BIN GATHM_LLAMACPP_MODEL GATHM_LLAMACPP_DIR
    unset GATHM_LLAMACPP_MODEL_DIR GATHM_LLAMACPP_BUILD GATHM_LLAMACPP_NO_PACKAGE
    unset GATHM_INSTALL_LLAMACPP GATHM_INSTALL_OLLAMA GATHM_LLM_BACKEND
    unset GATHM_OLLAMA_MODEL OLLAMA_MODELS
    LLAMACPP_DIR="$HOME/.gathm/llamacpp"
    LLAMACPP_BIN_DIR="$LLAMACPP_DIR/bin"
    LLAMACPP_MODELS_DIR="$HOME/.gathm/models"
    _GATHM_LLAMACPP_BIN=""
    _GATHM_PLATFORM=termux
    GATHM_ONLINE=true
    CALLS="$HOME/unexpected-downloads"
    : > "$CALLS"
    # Successful reuse must not get as far as these operations. A marker also
    # catches calls hidden inside command substitutions or subshells.
    _llamacpp_download_model() { echo gguf-download >> "$CALLS"; return 1; }
    _llamacpp_install_via_package_manager() { echo package-install >> "$CALLS"; return 1; }
    _llamacpp_install_prebuilt() { echo runtime-download >> "$CALLS"; return 1; }
    _llamacpp_build_from_source() { echo runtime-build >> "$CALLS"; return 1; }
    _pull_ollama_model() { echo ollama-pull >> "$CALLS"; return 0; }
    install_ollama() { echo ollama-install >> "$CALLS"; return 0; }
    setup_gemini_fallback() { echo backend-change >> "$CALLS"; return 0; }
    step_done() { :; }
    step_skip() { :; }
    step_fail() { :; }
}

run_case() {
    local name="$1" callback="$2"
    if (fixture "$callback"; "$callback") > "$FIX/$callback.log" 2>&1; then
        PASS=$((PASS + 1))
        printf '  ok   %s\n' "$name"
    else
        FAIL=$((FAIL + 1))
        printf '  FAIL %s\n' "$name"
        cat "$FIX/$callback.log"
    fi
}

bin_precedence() {
    local explicit="$HOME/explicit bin/llama-server"
    local dotenv="$HOME/dotenv bin/llama-server"
    local saved="$HOME/saved bin/llama-server"
    binary "$explicit"; binary "$dotenv"; binary "$saved"
    binary "$LLAMACPP_BIN_DIR/llama-server"
    printf '%s\n' "$saved" > "$HOME/.gathm/llamacpp_bin"
    printf 'GATHM_LLAMACPP_BIN="%s"\n' "$dotenv" > "$SCRIPT_DIR/.env"
    export GATHM_LLAMACPP_BIN="$explicit"
    eq 'exported binary wins' "$(_llamacpp_existing_bin)" "$explicit" || return 1
    unset GATHM_LLAMACPP_BIN
    eq 'dotenv binary precedes saved pointer' "$(_llamacpp_existing_bin)" "$dotenv" || return 1
    rm "$dotenv"
    eq 'stale dotenv falls back to saved binary' "$(_llamacpp_existing_bin)" "$saved" || return 1
    rm "$saved"
    eq 'stale pointer falls back to standard binary' "$(_llamacpp_existing_bin)" "$LLAMACPP_BIN_DIR/llama-server"
}

model_precedence() {
    local explicit="$HOME/explicit model/model.gguf"
    local dotenv="$HOME/dotenv model/model.gguf"
    local saved="$HOME/saved model/model.gguf"
    gguf "$explicit"; gguf "$dotenv"; gguf "$saved"
    gguf "$LLAMACPP_MODELS_DIR/default.gguf"
    printf '%s\n' "$saved" > "$HOME/.gathm/llamacpp_model"
    printf "GATHM_LLAMACPP_MODEL='%s'\n" "$dotenv" > "$SCRIPT_DIR/.env"
    export GATHM_LLAMACPP_MODEL="$explicit"
    eq 'exported weights win' "$(_llamacpp_existing_model)" "$explicit" || return 1
    unset GATHM_LLAMACPP_MODEL
    eq 'dotenv weights precede saved pointer' "$(_llamacpp_existing_model)" "$dotenv" || return 1
    printf '<html>not model weights</html>' > "$dotenv"
    eq 'invalid dotenv file falls back to saved weights' "$(_llamacpp_existing_model)" "$saved" || return 1
    rm "$saved"
    eq 'stale pointer falls back to default weights' "$(_llamacpp_existing_model)" "$LLAMACPP_MODELS_DIR/default.gguf"
}

home_expansion() {
    binary "$HOME/custom runtime/llama-server"
    gguf "$HOME/custom weights/model.gguf"
    # The installer, rather than this shell, must expand these literal tildes.
    # shellcheck disable=SC2088
    export GATHM_LLAMACPP_BIN='~/custom runtime/llama-server'
    # shellcheck disable=SC2088
    export GATHM_LLAMACPP_MODEL='~/custom weights/model.gguf'
    eq 'binary tilde expands' "$(_llamacpp_existing_bin)" "$HOME/custom runtime/llama-server" || return 1
    eq 'model tilde expands' "$(_llamacpp_existing_model)" "$HOME/custom weights/model.gguf"
}

model_directories() {
    local configured="$HOME/configured weights"
    local saved="$HOME/saved weights"
    gguf "$configured/model.gguf"; gguf "$saved/model.gguf"
    export GATHM_LLAMACPP_MODEL="$configured"
    printf '%s\n' "$saved" > "$HOME/.gathm/llamacpp_model"
    eq 'explicit directory resolves model' "$(_llamacpp_existing_model)" "$configured/model.gguf" || return 1
    unset GATHM_LLAMACPP_MODEL
    eq 'saved directory resolves model' "$(_llamacpp_existing_model)" "$saved/model.gguf"
}

configured_model_root() {
    local explicit="$HOME/exported model root"
    local dotenv="$HOME/dotenv model root"
    gguf "$explicit/model.gguf"; gguf "$dotenv/model.gguf"
    gguf "$HOME/.gathm/llamacpp/models/legacy.gguf"
    printf 'GATHM_LLAMACPP_MODEL_DIR="%s"\n' "$dotenv" > "$SCRIPT_DIR/.env"
    export GATHM_LLAMACPP_MODEL_DIR="$explicit"
    eq 'exported model root wins' "$(_llamacpp_existing_model)" "$explicit/model.gguf" || return 1
    unset GATHM_LLAMACPP_MODEL_DIR
    eq 'dotenv model root is used' "$(_llamacpp_existing_model)" "$dotenv/model.gguf" || return 1
    rm "$dotenv/model.gguf"
    eq 'legacy model root is reused' "$(_llamacpp_existing_model)" "$HOME/.gathm/llamacpp/models/legacy.gguf"
}

directory_filters() {
    gguf "$LLAMACPP_MODELS_DIR/aaa-00002-of-00002.gguf"
    gguf "$LLAMACPP_MODELS_DIR/mmproj-small.gguf"
    gguf "$LLAMACPP_MODELS_DIR/zzz-chat.gguf"
    eq 'directory skips projector and non-first shards' "$(_llamacpp_existing_model)" "$LLAMACPP_MODELS_DIR/zzz-chat.gguf" || return 1
    rm "$LLAMACPP_MODELS_DIR/zzz-chat.gguf"
    gguf "$LLAMACPP_MODELS_DIR/aaa-00001-of-00002.gguf"
    eq 'complete split resolves its first shard' "$(_llamacpp_existing_model)" "$LLAMACPP_MODELS_DIR/aaa-00001-of-00002.gguf" || return 1
    rm "$LLAMACPP_MODELS_DIR/aaa-00002-of-00002.gguf"
    if _llamacpp_existing_model >/dev/null; then
        echo 'incomplete split/projector must not be adopted' >&2
        return 1
    fi
}

dotenv_is_inert() {
    local model="$HOME/weights/"'literal $MODEL & `whoami`.gguf'
    local bin="$HOME/runtime/"'literal $BIN & `whoami`'
    gguf "$model"; binary "$bin"
    {
        printf 'UNRELATED=$(touch "%s")\n' "$HOME/executed"
        printf 'GATHM_LLAMACPP_MODEL="%s"\n' "$model"
        printf "GATHM_LLAMACPP_BIN='%s'\n" "$bin"
    } > "$SCRIPT_DIR/.env"
    eq 'literal dotenv model remains unchanged' "$(_llamacpp_existing_model)" "$model" || return 1
    eq 'literal dotenv binary remains unchanged' "$(_llamacpp_existing_bin)" "$bin" || return 1
    [[ ! -e "$HOME/executed" ]] || { echo '.env executed as code' >&2; return 1; }
}

offline_reinstall() {
    local bin="$HOME/existing runtime/llama-server"
    local model="$HOME/existing weights/model.gguf"
    binary "$bin"; gguf "$model"
    printf '%s\n' "$bin" > "$HOME/.gathm/llamacpp_bin"
    printf '%s\n' "$model" > "$HOME/.gathm/llamacpp_model"
    GATHM_ONLINE=false
    install_llamacpp termux || return 1
    eq 'existing runtime selected offline' "$_GATHM_LLAMACPP_BIN" "$bin" || return 1
    select_and_pull_llm_model || return 1
    eq 'saved backend remains llama.cpp' "$(cat "$HOME/.gathm/llm_backend")" llamacpp || return 1
    eq 'saved weights remain the same' "$(cat "$HOME/.gathm/llamacpp_model")" "$model" || return 1
    eq 'no install or download' "$(cat "$CALLS")" ''
}

offline_broken_runtime() {
    local broken="$HOME/runtime/llama-server"
    mkdir -p "$(dirname "$broken")"
    printf '#!/nonexistent/interpreter\n' > "$broken"
    chmod +x "$broken"
    export GATHM_LLAMACPP_BIN="$broken"
    # Keep the machine running the test from supplying its own working binary,
    # while exercising the real discovery and executable-validation functions.
    command() {
        if [[ "${1:-}" == '-v' && "${2:-}" == llama-server* ]]; then
            return 1
        fi
        builtin command "$@"
    }
    GATHM_ONLINE=false
    if install_llamacpp termux; then
        echo 'an executable with a missing interpreter is not a usable runtime' >&2
        return 1
    fi
    eq 'broken runtime is not selected' "$_GATHM_LLAMACPP_BIN" '' || return 1
    eq 'offline runtime failure does not download' "$(cat "$CALLS")" ''
}

fallback_preserves_model() {
    local model="$HOME/existing weights/model.gguf"
    gguf "$model"
    printf '%s\n' "$model" > "$HOME/.gathm/llamacpp_model"
    _install_ollama_fallback termux || return 1
    eq 'missing runtime does not install Ollama over existing weights' "$(cat "$CALLS")" '' || return 1
    if select_and_pull_llm_model; then
        echo 'missing runtime must report setup failure' >&2
        return 1
    fi
    eq 'missing runtime preserves weights' "$(cat "$HOME/.gathm/llamacpp_model")" "$model" || return 1
    eq 'missing runtime retains llama.cpp selection' "$(cat "$HOME/.gathm/llm_backend")" llamacpp || return 1
    eq 'missing runtime does not pull Ollama model' "$(cat "$CALLS")" ''
}

fallback_explicit_optout() {
    gguf "$LLAMACPP_MODELS_DIR/existing.gguf"
    export GATHM_INSTALL_LLAMACPP=0
    _install_ollama_fallback termux || return 1
    eq 'explicit llama.cpp opt-out installs Ollama' "$(cat "$CALLS")" ollama-install || return 1
    GATHM_ONLINE=false
    select_and_pull_llm_model || return 1
    eq 'explicit opt-out uses Ollama model selection' "$(cat "$CALLS")" $'ollama-install\nollama-pull'
}

fallback_explicit_alongside() {
    local model="$LLAMACPP_MODELS_DIR/existing.gguf"
    gguf "$model"
    binary "$HOME/runtime/llama-server"
    _GATHM_LLAMACPP_BIN="$HOME/runtime/llama-server"
    export GATHM_INSTALL_OLLAMA=1
    _install_ollama_fallback termux || return 1
    select_and_pull_llm_model || return 1
    eq 'explicit alongside installation does not pull a second model' "$(cat "$CALLS")" ollama-install || return 1
    eq 'alongside installation preserves llama.cpp backend' "$(cat "$HOME/.gathm/llm_backend")" llamacpp
}

main_preserves_incomplete_setup() {
    local model="$HOME/existing weights/model.gguf"
    gguf "$model"
    printf '%s\n' "$model" > "$HOME/.gathm/llamacpp_model"
    detect_platform() { echo termux; }
    setup_termux_storage() { :; }
    check_internet() { GATHM_ONLINE=false; }
    check_and_install_python() { :; }
    install_deps() { :; }
    setup_files() { :; }
    install_llamacpp() { return 1; }
    install_pilot_deps() { :; }
    install_playwright_browser() { :; }
    install_audio_cpp() { :; }
    setup_shortcuts() { :; }
    reload_shell_config() { :; }
    verify() { :; }
    step_summary() { :; }
    launch_post_install() { printf 'reached\n' > "$HOME/install-completed"; }
    step_fail() { printf '%s\n' "$1" >> "$HOME/failed-steps"; }
    main || return 1
    eq 'main does not install or pull a replacement' "$(cat "$CALLS")" '' || return 1
    eq 'main keeps original weights' "$(cat "$HOME/.gathm/llamacpp_model")" "$model" || return 1
    eq 'main continues to final verification' "$(cat "$HOME/install-completed")" reached || return 1
    [[ -s "$HOME/failed-steps" ]] || { echo 'main must flag incomplete local setup' >&2; return 1; }
}

missing_model_stays_local() {
    binary "$HOME/runtime/llama-server"
    _GATHM_LLAMACPP_BIN="$HOME/runtime/llama-server"
    export GATHM_ONLINE=false
    printf 'llamacpp\n' > "$HOME/.gathm/llm_backend"
    if select_and_pull_llm_model; then
        echo 'missing offline weights must return failure' >&2
        return 1
    fi
    eq 'missing model does not change backend' "$(cat "$HOME/.gathm/llm_backend")" llamacpp || return 1
    eq 'missing model does not try Ollama' "$(cat "$CALLS")" ''
}

download_failure_stays_local() {
    binary "$HOME/runtime/llama-server"
    _GATHM_LLAMACPP_BIN="$HOME/runtime/llama-server"
    _detect_total_ram_mb() { echo 4000; }
    _detect_free_disk_mb() { echo 20000; }
    printf 'llamacpp\n' > "$HOME/.gathm/llm_backend"
    if select_and_pull_llm_model; then
        echo 'failed download must return failure' >&2
        return 1
    fi
    eq 'failed GGUF download does not try another backend' "$(cat "$CALLS")" gguf-download || return 1
    eq 'failed download preserves backend' "$(cat "$HOME/.gathm/llm_backend")" llamacpp
}

ollama_blob_reused_online() {
    local blob="$HOME/.ollama/models/blobs/sha256-existing"
    local manifest="$HOME/.ollama/models/manifests/registry.ollama.ai/library/existing/1b"
    gguf "$blob"
    mkdir -p "$(dirname "$manifest")"
    printf '{"layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:existing"}]}\n' > "$manifest"
    binary "$HOME/runtime/llama-server"
    _GATHM_LLAMACPP_BIN="$HOME/runtime/llama-server"
    _detect_total_ram_mb() { echo 4000; }
    _detect_free_disk_mb() { echo 20000; }
    select_and_pull_llm_model || return 1
    eq 'existing blob adopted without second GGUF' "$(cat "$HOME/.gathm/llamacpp_model")" "$blob" || return 1
    eq 'blob adoption uses llama.cpp' "$(cat "$HOME/.gathm/llm_backend")" llamacpp || return 1
    eq 'blob reuse needs no download' "$(cat "$CALLS")" ''
}

config_path_roundtrip() {
    local bin="$HOME/runtime/"'llama $BIN & server'
    local model="$HOME/weights/"'model $MODEL & chat.gguf'
    binary "$bin"; gguf "$model"
    # Existing entries exercise replacement as well as first-time writes.
    printf 'GATHM_LLAMACPP_BIN=old\nGATHM_LLAMACPP_MODEL=old\nGATHM_OLLAMA_MODEL=old\nGATHM_LLM_BACKEND=ollama\nUNCHANGED=keep\n' > "$SCRIPT_DIR/.env"
    _llamacpp_save_config "$bin" "$model" || return 1
    _save_model_config "$(_llamacpp_label_for "$model")" llamacpp || return 1
    eq 'written binary survives reload' "$(_llamacpp_existing_bin)" "$bin" || return 1
    eq 'written model survives reload' "$(_llamacpp_existing_model)" "$model" || return 1
    eq 'binary pointer is literal' "$(cat "$HOME/.gathm/llamacpp_bin")" "$bin" || return 1
    eq 'model pointer is literal' "$(cat "$HOME/.gathm/llamacpp_model")" "$model" || return 1
    eq 'model label is literal' "$(cat "$HOME/.gathm/model")" 'model $MODEL & chat' || return 1
    grep -qx 'UNCHANGED=keep' "$SCRIPT_DIR/.env" || return 1
    # shlex reads a quoted dotenv value as data; it performs no expansion or
    # command execution. Check the label replacement, where sed's '&' used to
    # inject the entire previous assignment into the new value.
    local dotenv_label
    dotenv_label=$(python3 - "$SCRIPT_DIR/.env" <<'PY'
import pathlib, shlex, sys
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("GATHM_OLLAMA_MODEL="):
        value = line.split("=", 1)[1]
        print(" ".join(shlex.split(value)))
        break
PY
    ) || return 1
    eq 'dotenv model label is literal' "$dotenv_label" 'model $MODEL & chat' || return 1
    # Remove pointers so malformed .env cannot be hidden by pointer fallback.
    rm "$HOME/.gathm/llamacpp_bin" "$HOME/.gathm/llamacpp_model"
    eq 'dotenv binary preserves metacharacters' "$(_llamacpp_existing_bin)" "$bin" || return 1
    eq 'dotenv model preserves metacharacters' "$(_llamacpp_existing_model)" "$model"
}

linux_platform_names() {
    local platform
    for platform in linux debian fedora arch alpine opensuse wsl termux macos windows; do
        _GATHM_PLATFORM="$platform"
        _llamacpp_supported || { echo "$platform must support llama.cpp" >&2; return 1; }
    done
    _GATHM_PLATFORM=unknown
    if _llamacpp_supported; then
        echo 'unknown platform must remain unsupported' >&2
        return 1
    fi
}

run_case 'binary override, dotenv, pointer and default precedence' bin_precedence
run_case 'model override, dotenv, pointer and valid-file precedence' model_precedence
run_case 'configured paths expand a home-directory prefix' home_expansion
run_case 'configured model directories are reused' model_directories
run_case 'custom and legacy model roots are discovered' configured_model_root
run_case 'directory discovery excludes projectors and incomplete splits' directory_filters
run_case 'dotenv contents stay inert and literal' dotenv_is_inert
run_case 'offline reinstall preserves existing runtime and model' offline_reinstall
run_case 'offline reinstall rejects an unusable executable' offline_broken_runtime
run_case 'missing runtime preserves an existing llama.cpp model' fallback_preserves_model
run_case 'explicit llama.cpp opt-out still selects Ollama' fallback_explicit_optout
run_case 'explicit alongside Ollama install preserves llama.cpp weights' fallback_explicit_alongside
run_case 'main reports incomplete local setup without replacing it' main_preserves_incomplete_setup
run_case 'missing weights do not silently select Ollama' missing_model_stays_local
run_case 'failed model download does not silently select Ollama' download_failure_stays_local
run_case 'online install adopts existing Ollama GGUF before downloading' ollama_blob_reused_online
run_case 'saved model and binary paths round-trip without corruption' config_path_roundtrip
run_case 'every supported Linux distro reaches llama.cpp setup' linux_platform_names

printf '\npassed=%s failed=%s\n' "$PASS" "$FAIL"
exit $((FAIL > 0 ? 1 : 0))
