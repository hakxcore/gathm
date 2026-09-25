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
getConfiguredClient() { return 0; }
checkInternet() { return 0; }
export -f httpGet 2>/dev/null || true

URLS="$(mktemp)"
FIX="$(mktemp -d)"
trap 'rm -f "$URLS"; rm -rf "$FIX"' EXIT

run_weather() {
    : > "$URLS"
    # Source the tool's functions without running its argument parsing.
    # shellcheck disable=SC1090
    eval "$(sed -n '/^getIPWeather()/,/^usage()/p' "$REPO/tools/weather/weather" | sed '$d')"
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

url="$(GATHM_TOOL_COMPACT=1 run_weather 'Mumbai?uM')"
contains "compact output retains imperial and wind units" "$url" '?uM&format='
url="$(GATHM_TOOL_COMPACT=1 run_weather Moon)"
absent "moon phase keeps its own output" "$url" 'format='

# Exercise option parsing as well as helpers, using a local fetch stub.
mkdir -p "$FIX/tools/weather" "$FIX/lib"
cp "$REPO/tools/weather/weather" "$FIX/tools/weather/weather"
cat > "$FIX/lib/utils.bash" <<'STUB'
getConfiguredClient() { return 0; }
checkInternet() { return 0; }
httpGet() {
    case "$1" in
        */country) echo IN ;;
        */loc) echo '19,72' ;;
        *) printf '%s\n' "$1" ;;
    esac
}
STUB
url="$(GATHM_TOOL_COMPACT=1 bash "$FIX/tools/weather/weather" -f Mumbai iM)"
contains "forecast flag preserves location and units" "$url" 'wttr.in/Mumbai?uM'
absent "forecast flag overrides compact mode" "$url" 'format='
url="$(GATHM_TOOL_COMPACT=1 bash "$FIX/tools/weather/weather" m)"
contains "automatic location preserves metric units" "$url" 'wttr.in/19,72?m&format='
GATHM_TOOL_COMPACT=1 bash "$FIX/tools/weather/weather" -z >/dev/null 2>&1
if [[ $? -eq 2 ]]; then ok "invalid flags fail before making a request"
else bad "invalid flags fail before making a request" "expected status 2"; fi

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
