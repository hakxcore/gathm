#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run every Gathm tool with a realistic argument and report what works.

`gathm health all` checks whether a tool's *endpoints* answer, and
tests/test_tools.py only proves each tool responds to -v. Neither runs a tool
the way a user does, so a tool can be "healthy" and still fail on a real query.
This does that pass and prints one line per tool.

The invocation comes from each tool's own manifest: a required argument gets a
sample value chosen from its name and description, so the harness stays correct
as tools change instead of encoding a stale command per tool. The exact command
run is printed, so a wrong sample is visible rather than reported as a failure.

Usage:
    python3 tests/tool_smoke.py                 # every tool
    python3 tests/tool_smoke.py dns weather     # only these
    python3 tests/tool_smoke.py --timeout 90
    python3 tests/tool_smoke.py --keep          # keep full output per tool

Exit code is the number of tools that failed (capped at 125), so it is usable
in a pipeline.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# Sample values by what an argument is called or described as. First match wins.
SAMPLES = [
    (r"asn|as number", "AS15169"),
    (r"\bip\b|address", "8.8.8.8"),
    (r"url|link", "https://example.com"),
    (r"domain|host|site|website", "example.com"),
    (r"email|account", "test@example.com"),
    (r"ticker|symbol|stock", "AAPL"),
    (r"coin|crypto", "bitcoin"),
    (r"city|location|place", "Delhi"),
    (r"cve", "CVE-2021-44228"),
    (r"language|command|cheat", "tar"),
    (r"word|term|definition", "serendipity"),
    (r"artist|song|track|title", "Imagine"),
    (r"movie|film", "Inception"),
    (r"port", "80"),
    (r"text|string|message|data", "gathm"),
    (r"query|search|keyword|topic", "gathm"),
    (r"file|path", ""),          # filled in with a real temp file
]

# Where a manifest cannot express the real shape, or the first positional is not
# what a user would pass. Kept deliberately short — each entry is a manifest
# that could be more precise.
OVERRIDES = {
    "currency": ["USD", "EUR", "100"],       # ordered triple, not one argument
    "todo": ["-l"],                          # listing is the read-only action
    "crypt": ["-h"],                         # encryption needs a real keyfile
    "transfer": ["-h"],                      # would upload a file publicly
    "shareterminal": ["-h"],                 # would open a public session
    "jukebox": ["-h"],                       # interactive player
    "gif": ["-h"],                           # renders animation to the terminal
    "meme": ["-h"],                          # interactive template picker
    "maltego": ["-h"],                       # writes CSV bundles
    "strix": ["-h"],                         # wraps a local binary
    "portscan": ["-h"],                      # scanning a host uninvited
    "naabu": ["-h"],
    "nuclei": ["-h"],
    "katana": ["-h"],
    "uncover": ["-h"],
    "pdchain": ["-h"],
    "imganalyze": ["-h"],                    # needs an image file
    "qrify": ["gathm"],
    "newton": ["derive", "x^2"],
    "dns": ["-t", "MX", "gmail.com"],
    "lyrics": ["-a", "John Lennon", "-s", "Imagine"],   # flag-driven, not positional
    "cipher": ["-e", "gathm"],
}

# Env vars a tool needs before it can do anything, from its manifest's apis
# plus the ones the code reads directly.
KEYS = {
    "shodan": ["SHODAN_API_KEY"],
    "movie": ["OMDB_API_KEY"],
    "tipcheck": ["VT_API_KEY", "VIRUSTOTAL_API_KEY", "ABUSEIPDB_API_KEY"],
    "urlscan": ["URLSCAN_API_KEY"],
}

# Tools that shell out to a binary that is installed separately.
EXTERNAL = {
    "subfinder": "subfinder", "dnsx": "dnsx", "httpx": "httpx",
    "naabu": "naabu", "katana": "katana", "nuclei": "nuclei",
    "uncover": "uncover", "shodan": "shodan", "strix": "strix",
    "maltego": "maltego",
}

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"


def manifest_args(tool: str) -> tuple[list, str]:
    """Sample argv for a tool, plus a note about how it was chosen."""
    if tool in OVERRIDES:
        return list(OVERRIDES[tool]), "override"

    path = TOOLS / tool / "tool.yaml"
    try:
        text = path.read_text()
    except OSError:
        return ["-h"], "no manifest"

    block = re.search(r"^input_schema:\s*\n(.*?)(?=^\w|\Z)", text, re.S | re.M)
    if not block:
        return ["-h"], "no input_schema"

    args: list = []
    entries = re.findall(
        r"-\s*name:\s*(\S+)(.*?)(?=^\s*-\s*name:|^\s{2}\w|\Z)",
        block.group(1), re.S | re.M)
    for name, body in entries:
        if "required: true" not in body:
            continue
        desc = " ".join(re.findall(r'description:\s*"?([^"\n]*)', body)).lower()
        haystack = f"{name.lower()} {desc}"
        for pattern, sample in SAMPLES:
            if re.search(pattern, haystack):
                if sample == "":                    # needs a real file
                    fd, tmp = tempfile.mkstemp(prefix="gathm-smoke-", suffix=".txt")
                    os.write(fd, b"gathm smoke test\n")
                    os.close(fd)
                    sample = tmp
                args.append(sample)
                break
        else:
            args.append("gathm")

    return (args or ["-h"]), ("manifest" if args else "no required args")


def missing_prereqs(tool: str) -> str:
    """Why this tool cannot possibly work here, or ""."""
    binary = EXTERNAL.get(tool)
    if binary and not any((Path(p) / binary).exists()
                          for p in os.environ.get("PATH", "").split(os.pathsep) if p):
        return f"needs the `{binary}` binary"
    needed = KEYS.get(tool)
    if needed and not any(os.environ.get(k) for k in needed):
        return f"needs {' or '.join(needed)}"
    return ""


def install_hint(tool: str) -> str:
    """The command that would make this tool usable, from lib/deps.bash."""
    binary = EXTERNAL.get(tool)
    if binary:
        try:
            out = subprocess.run(
                ["bash", "-c",
                 f'source "{ROOT}/lib/deps.bash" && gathm_dep_hint "{binary}"'],
                capture_output=True, text=True, timeout=15)
            return (out.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""
    needed = KEYS.get(tool)
    if needed:
        return f"export {needed[0]}=... (see the tool's tool.yaml for where to get one)"
    return ""


def run_tool(tool: str, argv: list, timeout: int) -> tuple[str, str, str]:
    """Return (verdict, headline, full output)."""
    path = TOOLS / tool / tool
    cmd = ["bash", "-c",
           f'source "{ROOT}/lib/utils.bash" 2>/dev/null; exec "{path}" "$@"',
           "gathm-smoke"] + [str(a) for a in argv]
    env = {**os.environ, "TERM": "dumb", "GATHM_NON_INTERACTIVE": "1",
           "NO_COLOR": "1"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"no answer within {timeout}s", ""
    except Exception as exc:  # noqa: BLE001
        return "FAIL", str(exc), ""

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    combined = out or err
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", combined)
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    headline = lines[0][:96] if lines else ""

    full = f"$ {tool} {' '.join(str(a) for a in argv)}\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n"

    if proc.returncode != 0:
        # Prefer a line that looks like the actual complaint.
        for ln in lines:
            if re.search(r"error|fail|not found|invalid|unable|refused|denied"
                         r"|timeout|no such", ln, re.I):
                headline = ln[:96]
                break
        return "FAIL", headline or f"exit {proc.returncode}", full
    if not lines:
        return "EMPTY", "exited 0 with no output", full
    if re.search(r"^(error|usage:)", plain.strip(), re.I):
        return "FAIL", headline, full
    return "OK", headline, full


def main() -> int:
    args = sys.argv[1:]
    timeout = 45
    keep = False
    wanted: list = []
    i = 0
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1]); i += 2
        elif args[i] == "--keep":
            keep = True; i += 1
        elif args[i] in ("-h", "--help"):
            print(__doc__); return 0
        else:
            wanted.append(args[i]); i += 1

    tools = sorted(d.name for d in TOOLS.iterdir()
                   if d.is_dir() and (d / d.name).is_file())
    if wanted:
        tools = [t for t in tools if t in wanted]
        missing = [t for t in wanted if t not in tools]
        for name in missing:
            print(f"{RED}no such tool:{RESET} {name}")

    logdir = Path(tempfile.mkdtemp(prefix="gathm-smoke-")) if keep else None
    print(f"{BOLD}Gathm tool smoke test{RESET}  "
          f"{DIM}{len(tools)} tools, {timeout}s each{RESET}\n")

    verdicts: dict = {}
    for tool in tools:
        skip = missing_prereqs(tool)
        if skip:
            verdicts[tool] = ("SKIP", skip)
            print(f"  {YELLOW}SKIP{RESET}  {tool:<16s} {DIM}{skip}{RESET}")
            # A skip is only useful if it says how to stop skipping.
            hint = install_hint(tool)
            if hint:
                print(f"        {DIM}→ {hint}{RESET}")
            continue
        argv, _how = manifest_args(tool)
        verdict, headline, full = run_tool(tool, argv, timeout)
        verdicts[tool] = (verdict, headline)
        colour = {"OK": GREEN, "FAIL": RED, "TIMEOUT": RED,
                  "EMPTY": YELLOW}.get(verdict, "")
        shown = " ".join(str(a) for a in argv)
        print(f"  {colour}{verdict:<4s}{RESET}  {tool:<16s} "
              f"{DIM}({shown}){RESET} {headline}")
        if logdir and full:
            (logdir / f"{tool}.log").write_text(full)

    counts: dict = {}
    for verdict, _ in verdicts.values():
        counts[verdict] = counts.get(verdict, 0) + 1
    print("\n" + BOLD + "Summary" + RESET + "  " + "  ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))

    broken = [(t, h) for t, (v, h) in verdicts.items()
              if v in ("FAIL", "TIMEOUT", "EMPTY")]
    if broken:
        print(f"\n{BOLD}Not working — paste this back{RESET}")
        for tool, headline in broken:
            print(f"  {tool}: {headline}")
        print(f"\n{DIM}Note: on a restricted network, \"empty response\" and "
              f"\"could not fetch\" mean the host is unreachable, not that the "
              f"tool is broken. Run this where the internet is open.{RESET}")
    if logdir:
        print(f"\nFull output per tool: {logdir}")
    return min(len(broken), 125)


if __name__ == "__main__":
    sys.exit(main())
