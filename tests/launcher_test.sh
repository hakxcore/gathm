#!/usr/bin/env bash
# Tests for the gathm launcher: GUI + Pilot startup, subcommand dispatch.
# Builds a fixture tree with stub pilot/api/agent so nothing real is launched.
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }
check(){ # name haystack needle
    if [[ "$2" == *"$3"* ]]; then ok "$1"; else bad "$1" "expected '$3' in: $(printf '%s' "$2" | head -c 300)"; fi
}
absent(){
    if [[ "$2" != *"$3"* ]]; then ok "$1"; else bad "$1" "did not expect '$3'"; fi
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/lib" "$FIX/pilot" "$FIX/api" "$FIX/agent" "$FIX/tools/dummy" "$FIX/home"
cp "$REPO/gathm" "$FIX/gathm"
cp "$REPO/lib/utils.bash" "$FIX/lib/utils.bash"
# utils.bash sources siblings relative to itself; copy what exists.
for f in logging.bash schema.bash deps.bash health.bash recovery.bash; do
    [[ -f "$REPO/lib/$f" ]] && cp "$REPO/lib/$f" "$FIX/lib/$f"
done
cat > "$FIX/pilot/run.sh" <<'STUB'
#!/usr/bin/env bash
echo "PILOT_STARTED args=$*"
STUB
cat > "$FIX/agent/orchestrator.sh" <<'STUB'
#!/usr/bin/env bash
echo "AGENT args=$*"
STUB
cat > "$FIX/tools/dummy/dummy" <<'STUB'
#!/usr/bin/env bash
echo dummy
STUB
# Stub API server: serves / so the launcher's probe succeeds.
cat > "$FIX/api/server.py" <<'STUB'
import sys, http.server, socketserver
port = 8080; host = "127.0.0.1"
a = sys.argv[1:]
for i, v in enumerate(a):
    if v == "--port": port = int(a[i+1])
    if v == "--host": host = a[i+1]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/plain")
        self.end_headers(); self.wfile.write(b"stub gui")
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer((host, port), H) as s:
    print(f"stub serving on {host}:{port}", flush=True)
    s.serve_forever()
STUB
chmod +x "$FIX/gathm" "$FIX/pilot/run.sh" "$FIX/agent/orchestrator.sh" "$FIX/tools/dummy/dummy"

PORT=$(( 8300 + RANDOM % 400 ))
run() { HOME="$FIX/home" GATHM_GUI_PORT="$PORT" timeout 60 bash "$FIX/gathm" "$@" 2>&1; }

echo "== dispatch =="
out=$(run --version);              check "--version prints version" "$out" "gathm v"
out=$(run help)
check "help documents the GUI default"  "$out" "Start the web GUI"
check "help documents stop"             "$out" "gathm stop"
absent "help no longer mentions dialog" "$out" "dialog"
out=$(run tui);                    check "tui execs Pilot" "$out" "PILOT_STARTED"
absent "tui starts no GUI"         "$out" "Starting GUI server"
out=$(run pilot);                  check "pilot is an alias for tui" "$out" "PILOT_STARTED"
out=$(run --no-gui);               check "--no-gui reaches Pilot" "$out" "PILOT_STARTED"
absent "--no-gui skips the server" "$out" "Starting GUI server"
out=$(run --tui-only);             check "--tui-only reaches Pilot" "$out" "PILOT_STARTED"
out=$(run ask "hello");            check "agent subcommands still route" "$out" "AGENT args=ask hello"
out=$(run dummy x);                check "bare tool name routes to run" "$out" "AGENT args=run dummy x"
out=$(run notathing);              check "unknown command errors" "$out" "Unknown command or tool"

echo "== gui lifecycle =="
out=$(run stop);                   check "stop with nothing running" "$out" "No GUI server is running"
out=$(run gui --no-browser)
check "gui starts the server"      "$out" "GUI ready at"
check "gui prints the URL"         "$out" "127.0.0.1:$PORT"
[[ -f "$FIX/home/.gathm/gui.pid" ]] && ok "pid file written" || bad "pid file written" "missing $FIX/home/.gathm/gui.pid"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/")
check "server answers on the port" "$code" "200"
out=$(run gui --no-browser);       check "second gui reuses the server" "$out" "already running"
out=$(run --no-browser)
check "bare gathm starts the GUI"  "$out" "GUI"
check "bare gathm then starts Pilot" "$out" "PILOT_STARTED"
out=$(run stop);                   check "stop kills it" "$out" "GUI server stopped"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/" || echo 000)
check "port is free after stop"    "$code" "000"
[[ -f "$FIX/home/.gathm/gui.pid" ]] && bad "pid file removed" "still there" || ok "pid file removed"

echo "== port/host flags =="
ALT=$(( PORT + 1 ))
out=$(HOME="$FIX/home" timeout 60 bash "$FIX/gathm" gui --port "$ALT" --no-browser 2>&1)
check "--port is honoured"         "$out" "127.0.0.1:$ALT"
HOME="$FIX/home" GATHM_GUI_PORT="$ALT" timeout 30 bash "$FIX/gathm" stop >/dev/null 2>&1
out=$(HOME="$FIX/home" timeout 30 bash "$FIX/gathm" gui --host 0.0.0.0 --port "$ALT" --no-browser 2>&1)
check "0.0.0.0 advertised as loopback" "$out" "http://127.0.0.1:$ALT"
HOME="$FIX/home" GATHM_GUI_PORT="$ALT" timeout 30 bash "$FIX/gathm" stop >/dev/null 2>&1

echo "== degraded environments =="
mv "$FIX/api/server.py" "$FIX/api/server.py.off"
out=$(run --no-browser)
check "missing server.py is reported"  "$out" "API server not found"
check "and Pilot still starts"         "$out" "PILOT_STARTED"
mv "$FIX/api/server.py.off" "$FIX/api/server.py"
mv "$FIX/pilot/run.sh" "$FIX/pilot/run.sh.off"
out=$(run tui)
check "missing Pilot is reported"      "$out" "Pilot not found"
mv "$FIX/pilot/run.sh.off" "$FIX/pilot/run.sh"

echo "== symlinked launcher =="
ln -s "$FIX/gathm" "$FIX/home/gathm-link"
out=$(HOME="$FIX/home" GATHM_GUI_PORT="$PORT" timeout 30 bash "$FIX/home/gathm-link" tui 2>&1)
check "symlink resolves to the checkout" "$out" "PILOT_STARTED"

echo ""
echo "passed=$PASS failed=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
