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
import sys, signal, http.server, socketserver
# uvicorn installs its own SIGINT handler, which is why a Ctrl+C that reached
# the real server shut it down even though bash had set SIGINT to ignored for
# the background job. Mirror that here or the Ctrl+C test proves nothing.
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
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
# A port nothing answers on, so the Ollama probe is deterministic wherever
# these run. Tests that want a live model server override OLLAMA_BASE_URL.
DEAD_OLLAMA=$(( 8700 + RANDOM % 200 ))
run() {
    HOME="$FIX/home" GATHM_GUI_PORT="$PORT" \
    OLLAMA_BASE_URL="http://127.0.0.1:$DEAD_OLLAMA/v1" \
    timeout 60 bash "$FIX/gathm" "$@" 2>&1
}

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

echo "== the GUI survives Ctrl+C =="
# Regression: the server used to inherit the launcher's process group. Because
# launch_pilot execs, Pilot became that group's leader — the terminal's
# foreground group — so Ctrl+C at the Pilot prompt was delivered to the GUI
# server too and killed it along with Pilot. nohup does not cover SIGINT.
CTRLC_PORT=$(( PORT + 20 ))
cat > "$FIX/ctrlc.sh" <<CTRLC
#!/usr/bin/env bash
export HOME="$FIX/home"
mypgid=\$(ps -o pgid= -p \$\$ | tr -d ' ')
GATHM_GUI_PORT=$CTRLC_PORT bash "$FIX/gathm" gui --no-browser >/dev/null 2>&1
srvpid=\$(cat "$FIX/home/.gathm/gui.pid" 2>/dev/null)
srvpgid=\$(ps -o pgid= -p "\$srvpid" | tr -d ' ')
echo "launcher_pgid=\$mypgid server_pgid=\$srvpgid"
kill -INT -"\$mypgid" 2>/dev/null
sleep 3
CTRLC
# The wrapper signals its own process group, so it needs one of its own or it
# would take this test down with it. `set -m` gives the background job one.
( set -m; bash "$FIX/ctrlc.sh" >"$FIX/ctrlc.out" 2>&1 & wait $! ) 2>/dev/null
cout=$(cat "$FIX/ctrlc.out" 2>/dev/null)
lpg=${cout#*launcher_pgid=}; lpg=${lpg%% *}
spg=${cout#*server_pgid=}; spg=${spg%%[!0-9]*}
if [[ -n "$spg" && "$lpg" != "$spg" ]]; then
    ok "server runs in its own process group"
else
    bad "server runs in its own process group" "launcher=$lpg server=$spg"
fi
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$CTRLC_PORT/" || echo 000)
check "server still serving after Ctrl+C" "$code" "200"
HOME="$FIX/home" GATHM_GUI_PORT="$CTRLC_PORT" timeout 30 bash "$FIX/gathm" stop >/dev/null 2>&1

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
# Preflight now catches this before anything is started, so the wording is
# its own rather than launch_pilot's later "Pilot not found".
check "missing Pilot is reported"      "$out" "Pilot is missing"
absent "and nothing was started"       "$out" "PILOT_STARTED"
mv "$FIX/pilot/run.sh.off" "$FIX/pilot/run.sh"

echo "== preflight =="
out=$(run doctor)
check "doctor reports Python"          "$out" "Python"
check "doctor notices Ollama is down"  "$out" "Ollama"
absent "doctor starts no Pilot"        "$out" "PILOT_STARTED"
absent "doctor starts no GUI"          "$out" "Starting GUI server"

# A dead model server is a warning, not a refusal: the TUI still opens, the
# tools still run, and the user is told what will not work.
out=$(run --no-browser)
check "a dead Ollama still lets Gathm start" "$out" "PILOT_STARTED"
check "and says so"                          "$out" "Ollama"
$(run stop >/dev/null 2>&1)

# With a model server up, the configured model is checked against what is
# actually pulled — a 404 from inside LangChain later is not a useful error.
FAKE=$(( 8900 + RANDOM % 200 ))
python3 - "$FAKE" <<'OLLAMA' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
OLLAMA
FAKE_PID=$!
sleep 1
doctor_with_model() {
    HOME="$FIX/home" GATHM_GUI_PORT="$PORT" \
    OLLAMA_BASE_URL="http://127.0.0.1:$FAKE/v1" GATHM_OLLAMA_MODEL="$1" \
    timeout 30 bash "$FIX/gathm" doctor 2>&1
}
out=$(doctor_with_model llama3.2:3b)
check "a pulled model passes"          "$out" "Model"
absent "and is not reported missing"   "$out" "is not pulled"
out=$(doctor_with_model qwen2.5:72b)
check "a missing model is reported"    "$out" "is not pulled"
check "with the pull command"          "$out" "ollama pull qwen2.5:72b"
check "and what is actually installed" "$out" "llama3.2:3b"
kill $FAKE_PID 2>/dev/null

echo "== symlinked launcher =="
ln -s "$FIX/gathm" "$FIX/home/gathm-link"
out=$(HOME="$FIX/home" GATHM_GUI_PORT="$PORT" timeout 30 bash "$FIX/home/gathm-link" tui 2>&1)
check "symlink resolves to the checkout" "$out" "PILOT_STARTED"

echo ""
echo "passed=$PASS failed=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
