#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -f install.before-termux-fix ]]; then
  echo "ERROR: install.before-termux-fix is missing."
  exit 1
fi

# Restore clean installer first.
cp -f install.before-termux-fix install

# Remove the old heavy Termux pydantic repair block by using the existing
# lightweight branch already present in the clean installer as the anchor.
python3 - <<'PY'
from pathlib import Path
p = Path("install")
s = p.read_text()

fn = s.index("install_pilot_deps() {")
start = s.index('    if [[ "$_GATHM_PLATFORM" == "termux" ]]; then', fn)
end = s.index("    else\n", start)

branch = """    if [[ "$_GATHM_PLATFORM" == "termux" ]]; then
        _ensure_venv
        local tmp_req
        tmp_req=$(mktemp)
        cat > "$tmp_req" <<'REQEOF'
python-dotenv
rich>=13.0
prompt_toolkit>=3.0
requests
beautifulsoup4
REQEOF
        if _venv_pip -r "$tmp_req"; then
            ok "Termux Pilot dependencies installed (lightweight runtime)"
        else
            warn "Termux Pilot dependency install failed"
        fi
        rm -f "$tmp_req"
    """
s = s[:start] + branch + s[end:]

# Termux should launch the lightweight Pilot.
needle = '    local pilot_run="$SCRIPT_DIR/pilot/run.sh"\n'
if needle in s:
    s = s.replace(
        needle,
        '    if [[ "$platform" == "termux" && -f "$SCRIPT_DIR/pilot/run-termux.sh" ]]; then\n'
        '        cd "$SCRIPT_DIR"\n'
        '        exec bash "$SCRIPT_DIR/pilot/run-termux.sh"\n'
        '    fi\n\n' + needle,
        1
    )

p.write_text(s)
PY

cat > pilot/termux.py <<'PY'
import json, os, re, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
mf = Path.home() / ".gathm" / "model"
MODEL = os.getenv("GATHM_OLLAMA_MODEL") or (mf.read_text().strip() if mf.is_file() else "gemma3:4b")

def tools():
    d = ROOT / "tools"
    return {x.name for x in d.iterdir()
            if x.is_dir() and (x/x.name).is_file() and os.access(x/x.name, os.X_OK)} if d.is_dir() else set()

def chat(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(URL + "/api/chat", data=body,
        headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"].strip()

def tool(name, args):
    r = subprocess.run([str(ROOT/"agent"/"orchestrator.sh"),"run",name,*args],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    return (r.stdout.strip() + ("\n"+r.stderr.strip() if r.returncode and r.stderr.strip() else "")) or "(no output)"

def main():
    names = tools()
    msgs = [{"role":"system","content":
        "You are Gathm, a local Android/Termux AI assistant. "
        "When a listed tool is needed output exactly TOOL: <tool> <args>. "
        "Otherwise answer normally. Never invent tools.\n"
        + "\n".join("- "+x for x in sorted(names))}]
    print(f"Gathm Termux Pilot • Ollama • {MODEL}")
    print("Type /exit to quit.\n")
    while True:
        try: u = input("You: ").strip()
        except (EOFError, KeyboardInterrupt): print(); return
        if u.lower() in {"/exit","/quit","exit","quit"}: return
        if not u: continue
        msgs.append({"role":"user","content":u})
        for _ in range(4):
            try: a = chat(msgs)
            except Exception as e:
                print("[Ollama]", e); msgs.pop(); break
            m = re.match(r"^\s*TOOL:\s+([A-Za-z0-9_.-]+)(?:\s+(.*))?$", a)
            if not m:
                print("\nGathm:", a, "\n")
                msgs.append({"role":"assistant","content":a})
                break
            n, args = m.group(1), (m.group(2) or "").split()
            result = tool(n,args) if n in names else "Unknown tool: "+n
            msgs += [{"role":"assistant","content":a},
                     {"role":"user","content":f"TOOL RESULT:\n{result}\nAnswer the original request."}]

if __name__ == "__main__": main()
PY

cat > pilot/run-termux.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x venv/bin/python ]]; then
  exec venv/bin/python termux.py "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 termux.py "$@"
else
  exec python termux.py "$@"
fi
EOF
chmod +x pilot/run-termux.sh pilot/termux.py

bash -n install
echo "SUCCESS: install syntax is valid."
echo "Now run: ./install"
