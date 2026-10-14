#!/usr/bin/env bash
# Geolocation transport/format verification without external requests.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX=$(mktemp -d)
trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/tools/geo" "$FIX/lib"
cp "$REPO/tools/geo/geo" "$FIX/tools/geo/geo"
cat > "$FIX/lib/utils.bash" <<'STUB'
getConfiguredClient() { :; }
checkInternet() { :; }
detect_platform() { echo darwin; }
check_dependencies() { :; }
httpGet() {
    printf '%s\n' "$1" > "$GEO_URL_RECORD"
    printf '%s' "$GEO_RESPONSE"
}
STUB
export GEO_URL_RECORD="$FIX/url"
export GEO_RESPONSE='{"success":true,"ip":"8.8.8.8","city":"Example","region_code":"CA","country":"US","postal":"12345","connection":{"isp":"Example ISP"}}'
output=$(bash "$FIX/tools/geo/geo" -a 8.8.8.8 -o city,isp)
[[ "$output" == *$'Example\nExample ISP' ]] || exit 1
[[ "$(cat "$FIX/url")" == https://ipwho.is/8.8.8.8 ]] || exit 1
rm "$FIX/url"
if bash "$FIX/tools/geo/geo" -a '8.8.8.8?token=fixture' >/dev/null 2>&1; then exit 1; fi
[[ ! -e "$FIX/url" ]] || exit 1
export GEO_RESPONSE='{"success":false,"message":"fixture failure"}'
if bash "$FIX/tools/geo/geo" -a 8.8.8.8 >/dev/null 2>&1; then exit 1; fi
printf 'Geolocation security checks passed.\n'
