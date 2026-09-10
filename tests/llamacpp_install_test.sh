#!/usr/bin/env bash
# Tests for the llama.cpp half of ./install — the parts that pick a release
# archive, pick a GGUF out of a Hugging Face repo, and unpack what arrives.
#
# None of it touches the network. The installer's two endpoints are variables
# precisely so this suite can point them at file:// fixtures, which is the only
# way to prove the asset picker prefers the right build (a CPU archive over a
# CUDA one, llama-* over cudart-*) without downloading a hundred megabytes per
# assertion.
#
# The installer is sourced, not run: GATHM_INSTALL_SOURCE_ONLY=1 stops it at
# the function definitions.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT

GATHM_INSTALL_SOURCE_ONLY=1
export GATHM_INSTALL_SOURCE_ONLY
# shellcheck disable=SC1090
source "$REPO/install"
# The installer runs under `set -euo pipefail` and traps ERR to shout about
# aborts; a test suite must not stop at the first assertion that is MEANT to
# fail, nor narrate each one.
set +eo pipefail
trap - ERR
trap - INT TERM
trap 'rm -rf "$FIX"' EXIT

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }
check() { # name got want
    if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1" "got '$2', wanted '$3'"; fi
}
contains() { # name haystack needle
    if [[ "$2" == *"$3"* ]]; then ok "$1"; else bad "$1" "expected '$3' in '$2'"; fi
}

# --- Fixtures --------------------------------------------------------------
# A release list shaped like a real one: several platforms, several GPU
# flavours, the CUDA runtime archive that must never be mistaken for the
# binaries, and the source tarballs.
mk_release() {
    local out="$1"; shift
    local names=("$@") entries=() name
    for name in "${names[@]}"; do
        entries+=("{\"name\":\"$name\",\"browser_download_url\":\"https://example.invalid/$name\"}")
    done
    local joined; joined=$(IFS=,; echo "${entries[*]}")
    printf '{"tag_name":"b6234","assets":[%s]}\n' "$joined" > "$out"
}

mk_release "$FIX/release.json" \
    "llama-b6234-bin-macos-arm64.zip" \
    "llama-b6234-bin-macos-x64.zip" \
    "llama-b6234-bin-ubuntu-x64.zip" \
    "llama-b6234-bin-ubuntu-arm64.zip" \
    "llama-b6234-bin-ubuntu-vulkan-x64.zip" \
    "llama-b6234-bin-win-cpu-x64.zip" \
    "llama-b6234-bin-win-cpu-arm64.zip" \
    "llama-b6234-bin-win-vulkan-x64.zip" \
    "llama-b6234-bin-win-cuda-12.4-x64.zip" \
    "cudart-llama-bin-win-cuda-12.4-x64.zip" \
    "llama-b6234.tar.gz"

# An older release, from before win-cpu existed.
mk_release "$FIX/release-old.json" \
    "llama-b2000-bin-win-avx2-x64.zip" \
    "llama-b2000-bin-ubuntu-x64.zip"

# A release with nothing this machine can use.
mk_release "$FIX/release-empty.json" "llama-b6234.tar.gz"

echo "== picking a release archive =="
LLAMACPP_RELEASES_API="file://$FIX/release.json"

uname() { if [[ "${1:-}" == "-m" ]]; then echo "$FAKE_ARCH"; else command uname "$@"; fi; }
FAKE_ARCH=x86_64

got=$(_llamacpp_release_asset linux); got="${got%%$'\t'*}"
check "Linux x64 takes the ubuntu build" "${got##*/}" "llama-b6234-bin-ubuntu-x64.zip"

FAKE_ARCH=aarch64
got=$(_llamacpp_release_asset linux); got="${got%%$'\t'*}"
check "Linux arm64 takes the arm64 build" "${got##*/}" "llama-b6234-bin-ubuntu-arm64.zip"

FAKE_ARCH=arm64
got=$(_llamacpp_release_asset macos); got="${got%%$'\t'*}"
check "Apple silicon takes the arm64 build" "${got##*/}" "llama-b6234-bin-macos-arm64.zip"

FAKE_ARCH=x86_64
got=$(_llamacpp_release_asset macos); got="${got%%$'\t'*}"
check "an Intel Mac takes the x64 build" "${got##*/}" "llama-b6234-bin-macos-x64.zip"

# The important one. A CUDA archive without the separate cudart archive starts
# and then dies at the first token, so a CPU build has to win by default.
got=$(_llamacpp_release_asset windows); got="${got%%$'\t'*}"
check "Windows takes the CPU build by default" "${got##*/}" "llama-b6234-bin-win-cpu-x64.zip"

GATHM_LLAMACPP_VARIANT=vulkan
got=$(_llamacpp_release_asset windows); got="${got%%$'\t'*}"
check "and the GPU build when asked" "${got##*/}" "llama-b6234-bin-win-vulkan-x64.zip"
GATHM_LLAMACPP_VARIANT=""

got=$(_llamacpp_release_asset linux); got="${got##*$'\t'}"
check "the release tag comes back too" "$got" "b6234"

LLAMACPP_RELEASES_API="file://$FIX/release-old.json"
got=$(_llamacpp_release_asset windows); got="${got%%$'\t'*}"
check "an older naming scheme still resolves" "${got##*/}" "llama-b2000-bin-win-avx2-x64.zip"

LLAMACPP_RELEASES_API="file://$FIX/release-empty.json"
_llamacpp_release_asset windows >/dev/null 2>&1
check "no usable asset is a failure, not a guess" "$?" "1"

LLAMACPP_RELEASES_API="file://$FIX/does-not-exist.json"
_llamacpp_release_asset linux >/dev/null 2>&1
check "an unreachable release list fails cleanly" "$?" "1"

echo "== picking the weights out of a repo =="
mkdir -p "$FIX/hf/bartowski"
cat > "$FIX/hf/bartowski/Llama-3.2-3B-Instruct-GGUF" <<'JSON'
{"siblings": [
  {"rfilename": "README.md"},
  {"rfilename": "Llama-3.2-3B-Instruct-Q8_0.gguf"},
  {"rfilename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf"},
  {"rfilename": "Llama-3.2-3B-Instruct-Q4_K_S.gguf"},
  {"rfilename": "Llama-3.2-3B-Instruct-f16/Llama-3.2-3B-Instruct-f16-00001-of-00002.gguf"}
]}
JSON
cat > "$FIX/hf/bartowski/Only-Q4-0-GGUF" <<'JSON'
{"siblings": [{"rfilename": "model-Q4_0.gguf"}, {"rfilename": "model-Q8_0.gguf"}]}
JSON
cat > "$FIX/hf/bartowski/Split-Only-GGUF" <<'JSON'
{"siblings": [{"rfilename": "big/model-Q4_K_M-00001-of-00004.gguf"}]}
JSON
LLAMACPP_HF_API="file://$FIX/hf"

check "Q4_K_M is the quantisation we take" \
    "$(_llamacpp_model_filename bartowski/Llama-3.2-3B-Instruct-GGUF)" \
    "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
check "Q4_0 is accepted when K_M is not published" \
    "$(_llamacpp_model_filename bartowski/Only-Q4-0-GGUF)" "model-Q4_0.gguf"
# A shard out of a split model is not a model: llama.cpp would want all four,
# and we only ever download one file.
_llamacpp_model_filename bartowski/Split-Only-GGUF >/dev/null 2>&1
check "a split-only repo is refused" "$?" "1"
_llamacpp_model_filename bartowski/Does-Not-Exist >/dev/null 2>&1
check "a missing repo fails cleanly" "$?" "1"

echo "== the RAM ladder =="
check "an explicit repo wins" \
    "$(GATHM_LLAMACPP_MODEL_REPO=me/my-gguf _llamacpp_recommend_repo)" "me/my-gguf"
_detect_total_ram_mb() { echo "$FAKE_RAM"; }
FAKE_RAM=2048;  contains "2 GB gets the 1B model"  "$(_llamacpp_recommend_repo)" "Llama-3.2-1B"
FAKE_RAM=6000;  contains "6 GB gets the 3B model"  "$(_llamacpp_recommend_repo)" "Llama-3.2-3B"
FAKE_RAM=12000; contains "12 GB gets the 8B model" "$(_llamacpp_recommend_repo)" "8B"
FAKE_RAM=24000; contains "24 GB gets the 14B model" "$(_llamacpp_recommend_repo)" "14B"
FAKE_RAM=64000; contains "64 GB gets the 32B model" "$(_llamacpp_recommend_repo)" "32B"
FAKE_RAM=0;     contains "unknown RAM is cautious"  "$(_llamacpp_recommend_repo)" "3B"

echo "== the storage gate =="
check "a model that fits is kept" \
    "$(_llamacpp_repo_that_fits bartowski/Qwen2.5-14B-Instruct-GGUF 20000)" \
    "bartowski/Qwen2.5-14B-Instruct-GGUF"
contains "a model that does not fit is downgraded" \
    "$(_llamacpp_repo_that_fits bartowski/Qwen2.5-32B-Instruct-GGUF 6000)" "Llama-3.2-3B"
check "nothing fitting returns nothing" \
    "$(_llamacpp_repo_that_fits bartowski/Qwen2.5-32B-Instruct-GGUF 200)" ""

echo "== what came down the wire =="
printf 'GGUF\x03\x00\x00\x00rest' > "$FIX/real.gguf"
printf '<!DOCTYPE html><html>Entry not found</html>' > "$FIX/fake.gguf"
_llamacpp_is_gguf "$FIX/real.gguf"; check "a GGUF is recognised" "$?" "0"
# An HTML error page saved with a .gguf name is the failure mode that used to
# surface hours later as a parse error from llama-server.
_llamacpp_is_gguf "$FIX/fake.gguf"; check "an HTML error page is not" "$?" "1"
_llamacpp_is_gguf "$FIX/missing.gguf"; check "a missing file is not" "$?" "1"
check "the label drops the extension" "$(_llamacpp_label_for "$FIX/real.gguf")" "real"

echo "== unpacking a release archive =="
# Shaped like the real thing: the server sits in build/bin next to the shared
# libraries it loads by relative path, so unpacking the binary alone produces
# something that cannot run.
mkdir -p "$FIX/stage/build/bin"
printf '#!/bin/sh\n' > "$FIX/stage/build/bin/llama-server"
printf 'ELF' > "$FIX/stage/build/bin/libllama.so"
printf 'ELF' > "$FIX/stage/build/bin/libggml.so"
(cd "$FIX/stage" && python3 -c '
import shutil, sys
shutil.make_archive(sys.argv[1], "zip", ".")' "$FIX/release-archive")

LLAMACPP_BIN_DIR="$FIX/installed/bin"
_llamacpp_unpack "$FIX/release-archive.zip" "$FIX/unpacked"
check "unpacking succeeds" "$?" "0"
[[ -x "$LLAMACPP_BIN_DIR/llama-server" ]] && ok "llama-server is installed and executable" \
    || bad "llama-server is installed and executable" "not at $LLAMACPP_BIN_DIR/llama-server"
[[ -f "$LLAMACPP_BIN_DIR/libllama.so" ]] && ok "its shared libraries come with it" \
    || bad "its shared libraries come with it" "libllama.so was left behind"

printf 'not a zip' > "$FIX/broken.zip"
_llamacpp_unpack "$FIX/broken.zip" "$FIX/unpacked2" >/dev/null 2>&1
check "a corrupt archive is rejected" "$?" "1"

echo "== what gets written to ~/.gathm =="
HOME="$FIX/home"; mkdir -p "$HOME"
SCRIPT_DIR="$FIX/home"     # keeps _audiocpp_write_env's .env inside the fixture
_llamacpp_save_config "$FIX/installed/bin/llama-server" "$FIX/real.gguf"
check "the binary is recorded" "$(cat "$HOME/.gathm/llamacpp_bin")" "$FIX/installed/bin/llama-server"
check "the weights are recorded" "$(cat "$HOME/.gathm/llamacpp_model")" "$FIX/real.gguf"
contains "and both reach .env" "$(cat "$FIX/home/.env")" "GATHM_LLAMACPP_MODEL="

echo ""
echo "passed=$PASS failed=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
