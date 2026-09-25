#!/usr/bin/env bash
# The connectivity check must not download a web page.
#
# `checkInternet` runs before every tool, so whatever it costs is added to
# every command Gathm runs. It used to GET https://github.com in full — on a
# phone that was 20 of the 22 seconds `gathm run weather Mumbai` took, for a
# forecast the weather API returns in 2.
#
# This runs against a local stub that records the method and how many bytes it
# was asked for, so it tests the behaviour rather than the spelling.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }
check() { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1" "got '$2', wanted '$3'"; fi; }

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"; for pid in "${STUB_PID:-}" "${BLACK_PID:-}"; do [[ -z "$pid" ]] || kill "$pid" 2>/dev/null; done' EXIT

PORT=$(( 8800 + RANDOM % 300 ))
cat > "$FIX/stub.py" <<'STUB'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

RECORD = sys.argv[2]
BODY = b"x" * 400000          # a homepage-sized page, as github.com would send

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self, method):
        with open(RECORD, "a") as fh:
            fh.write(method + "\n")

    def do_HEAD(self):
        self._record("HEAD")
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()

    def do_GET(self):
        self._record("GET")
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
STUB

RECORD="$FIX/methods.txt"
python3 "$FIX/stub.py" "$PORT" "$RECORD" &
STUB_PID=$!
sleep 1

# shellcheck disable=SC1090
source "$REPO/lib/utils.bash"
getConfiguredClient >/dev/null 2>&1 || true

echo "== the check itself =="
GATHM_NET_CHECK_URL="http://127.0.0.1:$PORT/" checkInternet
check "a reachable host is online" "$?" "0"
check "and it asked for headers only" "$(tr -d '\n' < "$RECORD")" "HEAD"

echo "== when there is no network =="
DEAD=$(( 9700 + RANDOM % 200 ))
GATHM_NET_CHECK_URL="http://127.0.0.1:$DEAD/" GATHM_NET_CHECK_TIMEOUT=3 checkInternet
check "an unreachable host is offline" "$?" "1"

# A captive portal that accepts the connection and never answers must fail,
# not hang: the bound is the difference between a slow tool and a stuck one.
echo "== a host that never answers =="
python3 - "$FIX/black.port" <<'BLACKHOLE' &
import socket, sys
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
connections = []
while True:
    try: connections.append(s.accept()[0])  # retain sockets without replying
    except OSError: break
BLACKHOLE
BLACK_PID=$!
sleep 1
BLACK_PORT="$(cat "$FIX/black.port" 2>/dev/null)"
if [[ -n "$BLACK_PORT" ]]; then
    started=$SECONDS
    GATHM_NET_CHECK_URL="http://127.0.0.1:$BLACK_PORT/" GATHM_NET_CHECK_TIMEOUT=3 checkInternet
    rc=$?; elapsed=$(( SECONDS - started ))
    check "a black hole is offline, not a hang" "$rc" "1"
    if (( elapsed >= 2 && elapsed <= 8 )); then ok "and it timed out in ${elapsed}s"
    else bad "and it gave up quickly" "took ${elapsed}s"; fi
else
    bad "black hole fixture starts" "missing listening port"
fi
kill $BLACK_PID 2>/dev/null

# fetch is not present on every test host. Check its no-body argument contract.
fetch() { printf '%s\n' "$@" > "$FIX/fetch.args"; }
configuredClient=fetch GATHM_NET_CHECK_URL='https://example.test/' checkInternet
if grep -qx -- '-s' "$FIX/fetch.args"; then ok "fetch requests metadata only"
else bad "fetch requests metadata only" "missing -s"; fi

echo ""
echo "passed=$PASS failed=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
