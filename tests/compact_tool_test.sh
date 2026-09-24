#!/usr/bin/env bash
# Tools render for humans; the agent asks for data.
#
# `weather` returns 9,026 characters of ASCII cloud art. After cleaning and the
# observation cap the model reads ~740 tokens of it — twenty-seven seconds of
# prefill on a phone, to learn a temperature that fits in seventy characters.
# GATHM_TOOL_COMPACT=1 asks the same endpoint for one line instead.
#
# httpGet is stubbed, so this asserts the URL the tool would request without
# touching the network.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }
contains() { if [[ "$2" == *"$3"* ]]; then ok "$1"; else bad "$1" "expected '$3' in '$2'"; fi; }
absent()   { if [[ "$2" != *"$3"* ]]; then ok "$1"; else bad "$1" "did not expect '$3' in '$2'"; fi; }

# Record the URL instead of fetching it.
httpGet() { echo "$*" >> "$URLS"; echo "stub"; }
getConfiguredClient() { configuredClient=curl; }
checkInternet() { return 0; }
export -f httpGet 2>/dev/null || true

URLS="$(mktemp)"
trap 'rm -f "$URLS"' EXIT

run_weather() {
    : > "$URLS"
    # Source the tool's functions without running its argument parsing.
    # shellcheck disable=SC1090
    eval "$(sed -n '/^getIPWeather()/,/^usage()/p' "$REPO/tools/weather/weather" | head -n -1)"
    eval "$(grep -n 'WEATHER_COMPACT_FORMAT=' "$REPO/tools/weather/weather" | cut -d: -f2-)"
    _weather_compact() { [[ "${GATHM_TOOL_COMPACT:-0}" == "1" ]]; }
    getLocationWeather "$@" >/dev/null
    cat "$URLS"
}

echo "== what a person gets =="
url="$(GATHM_TOOL_COMPACT=0 run_weather Mumbai)"
contains "the location is requested"   "$url" "wttr.in/Mumbai"
absent   "and no format is forced"     "$url" "format="

echo "== what the agent gets =="
url="$(GATHM_TOOL_COMPACT=1 run_weather Mumbai)"
contains "the same location"           "$url" "wttr.in/Mumbai"
contains "but a one-line format"       "$url" "format="
contains "with the condition as text"  "$url" "%C"
# %c is the emoji. Several tokens, and nothing %C does not already say.
absent   "and no emoji"                "$url" "%c,"

echo "== Pilot asks for it, a terminal does not =="
grep -q 'GATHM_TOOL_COMPACT": "1"' "$REPO/pilot/main.py" \
    && ok "Pilot sets it for every tool it runs" \
    || bad "Pilot sets it for every tool it runs" "not found in run_gathm_tool_raw"
grep -q 'GATHM_TOOL_COMPACT' "$REPO/tools/weather/weather" \
    && ok "and weather honours it" \
    || bad "and weather honours it" "the tool does not read the variable"

echo ""
echo "passed=$PASS failed=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
