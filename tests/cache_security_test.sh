#!/usr/bin/env bash
# Private cache data, argument boundaries, caller isolation and metadata parsing.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX=$(mktemp -d)
trap 'rm -rf "$FIX"' EXIT
export GATHM_CACHE_DIR="$FIX/cache"
source "$REPO/lib/cache.bash"
init_cache
first=$(_cache_key weather 'a b')
second=$(_cache_key weather a b)
[[ "$first" != "$second" ]] || exit 1
export GATHM_CHAT_OWNER=first-caller
printf 'forecast\n' | cache_set weather 'private location argument'
[[ "$(cache_get weather 'private location argument')" == forecast ]] || exit 1
if grep -Fq 'private location argument' "$GATHM_CACHE_DIR"/*.meta; then exit 1; fi
export GATHM_CHAT_OWNER=second-caller
if cache_get weather 'private location argument'; then exit 1; fi
for tool in crypt cipher pwned transcribe imganalyze transfer todo; do
    printf 'private payload\n' | cache_set "$tool" fixture
    if cache_get "$tool" fixture; then exit 1; fi
done
key=$(_cache_key weather corrupt)
printf 'forecast\n' > "$GATHM_CACHE_DIR/$key"
{
    printf 'cached_at=%s\n' "$(date +%s)"
    printf 'ttl=x[$(touch "%s")]\n' "$FIX/unexpected-execution"
    printf 'tool=weather\n'
} > "$GATHM_CACHE_DIR/$key.meta"
if cache_get weather corrupt; then exit 1; fi
cache_stats >/dev/null
cache_cleanup >/dev/null
[[ ! -e "$FIX/unexpected-execution" ]] || exit 1
printf 'Cache security checks passed.\n'
