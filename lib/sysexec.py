#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run shell commands on the host machine, on behalf of the agent.

This is the most dangerous thing Gathm can do, and the model deciding what to
run may be a 1B model on a phone. So the question this module answers is not
"how do I run a command" — that is one line — but "which commands may run, and
which need a human first".

Three tiers:

    safe      read-only inspection (uname, df, ps, ls …) with no shell
              metacharacters. Runs immediately: it cannot change anything.
    confirm   everything else. Needs explicit approval from a human before it
              runs. Without an approver, it is refused.
    blocked   catastrophic, irreversible, or a remote-code-execution pattern.
              Never runs, approval or not.

Two rules do most of the work:

  * A shell metacharacter demotes anything to `confirm`. `ls` is safe;
    `ls; rm -rf ~` starts with a safe binary and is not.
  * The allowlist is of binaries that cannot write. Anything absent is not
    assumed hostile, just unproven — it goes to a human.

Off unless switched on, because an LLM with a shell should be a decision
someone made:

    GATHM_ALLOW_SHELL=1          (environment)
    ~/.gathm/allow_shell         (a file, any contents)

Every attempt is appended to ~/.gathm/shell.log with its verdict, whether it
ran or not.

Standard library only, so it works in the Termux install.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~")) / ".gathm"
AUDIT_LOG = CONFIG_DIR / "shell.log"

DEFAULT_TIMEOUT = 60
MAX_OUTPUT_CHARS = 4000

# Read-only by nature. Every one of these can be run on a stranger's machine
# without changing it — that is the entry requirement, not "usually harmless".
SAFE_BINARIES = {
    # identity and platform
    "uname", "hostname", "whoami", "id", "groups", "arch", "sw_vers",
    "getprop", "lsb_release", "sysctl", "uptime", "date", "locale",
    # filesystem inspection
    "ls", "pwd", "df", "du", "stat", "file", "find", "readlink", "basename",
    "dirname", "wc", "head", "tail", "cat", "less", "tree", "realpath",
    # processes and resources
    "ps", "top", "vm_stat", "free", "nproc", "sysctl", "lsof", "vmstat",
    "iostat", "who", "w",
    # packages and tooling, queried not changed
    "which", "type", "command", "env", "printenv", "python3", "python",
    "node", "git", "brew", "pkg", "apt", "dpkg", "rpm", "pip", "pip3",
    # network inspection
    "ifconfig", "ip", "netstat", "ss", "route", "ping", "dig", "nslookup",
    "host", "traceroute", "arp",
    # text
    "echo", "printf", "grep", "awk", "sed", "sort", "uniq", "cut", "tr",
    "diff", "md5sum", "shasum", "sha256sum",
}

# Subcommands that turn an otherwise read-only tool into a writing one.
# `git status` is inspection; `git push` is not. `brew list` is inspection;
# `brew install` is not.
WRITING_SUBCOMMANDS = {
    "git": {"push", "commit", "reset", "clean", "rebase", "merge", "checkout",
            "switch", "restore", "rm", "mv", "add", "apply", "am", "cherry-pick",
            "revert", "tag", "gc", "prune", "filter-branch", "config", "init",
            "clone", "fetch", "pull", "submodule", "stash", "worktree"},
    "brew": {"install", "uninstall", "remove", "upgrade", "update", "link",
             "unlink", "cleanup", "tap", "untap", "reinstall", "pin", "unpin"},
    "pkg":  {"install", "uninstall", "remove", "upgrade", "update", "autoclean",
             "clean", "reinstall"},
    "apt":  {"install", "remove", "purge", "upgrade", "update", "autoremove",
             "dist-upgrade"},
    "pip":  {"install", "uninstall", "download"},
    "pip3": {"install", "uninstall", "download"},
    "ip":   {"link", "addr", "route", "netns", "rule"},   # `ip … add/del` writes
    "sysctl": {"-w"},
    # Interpreters run arbitrary code; only --version style flags stay safe.
    "python3": {"-c", "-m"},
    "python":  {"-c", "-m"},
    "node":    {"-e", "-p", "--eval", "--print"},
}

# Anything a shell would interpret. Present, and the command is no longer just
# the binary it starts with.
SHELL_METACHARACTERS = re.compile(r"[;&|`$><\n]|\$\(|\|\|")

# Never, with or without approval. Ordered roughly by how often each shows up
# in a model's output when it has misunderstood the question.
BLOCKED = [
    (re.compile(r"\brm\b[^|;]*\s-[a-zA-Z]*[rR][a-zA-Z]*f|\brm\b[^|;]*\s-[a-zA-Z]*f[a-zA-Z]*[rR]"),
     "a recursive forced delete"),
    (re.compile(r"\brm\b\s+(-\S+\s+)*/(\s|$)"), "deleting the filesystem root"),
    (re.compile(r"\bmkfs\b|\bnewfs\b|\bdiskutil\s+(erase|partition)"),
     "formatting a disk"),
    (re.compile(r"\bdd\b[^|;]*\bof=\s*/dev/"), "writing raw blocks to a device"),
    (re.compile(r">\s*/dev/(sd|nvme|disk|hd)"), "overwriting a block device"),
    (re.compile(r":\s*\(\s*\)\s*\{.*\}\s*;?\s*:"), "a fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "shutting the machine down"),
    (re.compile(r"\bchmod\b\s+(-\S+\s+)*(777|-R\s+777)\s+/(\s|$)"),
     "making the filesystem root world-writable"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|k|)sh\b"),
     "piping a download straight into a shell"),
    (re.compile(r"\bchown\b\s+-R\s+\S+\s+/(\s|$)"), "reowning the filesystem root"),
    (re.compile(r"\bkillall\b\s+-9\b|\bkill\b\s+-9\s+1\b"),
     "killing everything, or init"),
    (re.compile(r"\bhistory\s+-c\b|\b>\s*~?/?\.bash_history\b"),
     "erasing shell history"),
    (re.compile(r"\bcrontab\b\s+-r\b"), "wiping the crontab"),
    (re.compile(r"\buserdel\b|\bdscl\b.*-delete"), "deleting a user account"),
    (re.compile(r"\b(nc|ncat|netcat)\b.*\s-e\s"), "opening a reverse shell"),
    (re.compile(r"/etc/(passwd|shadow|sudoers)\b.*>|>\s*/etc/"),
     "overwriting system configuration"),
]


def platform_name() -> str:
    """The platform, in the words the rest of Gathm uses."""
    if "com.termux" in (os.environ.get("PREFIX") or ""):
        return "termux"
    if os.path.isdir("/data/data/com.termux/files/usr"):
        return "termux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform or "unknown"


def platform_summary() -> str:
    """One line describing this machine, for the model's prompt."""
    name = platform_name()
    bits = [name, platform.machine() or "unknown arch"]
    if name == "macos":
        bits.append("macOS " + (platform.mac_ver()[0] or "?"))
    elif name == "termux":
        bits.append("Android/Termux")
    else:
        rel = platform.release()
        if rel:
            bits.append(rel)
    shell = os.environ.get("SHELL", "")
    if shell:
        bits.append(os.path.basename(shell))
    return ", ".join(b for b in bits if b)


def enabled() -> bool:
    """Whether running commands is switched on at all."""
    env = (os.environ.get("GATHM_ALLOW_SHELL") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return (CONFIG_DIR / "allow_shell").exists()


def disabled_reason() -> str:
    return ("running system commands is switched off. Turn it on with "
            "GATHM_ALLOW_SHELL=1, or permanently with: "
            "touch ~/.gathm/allow_shell")


def _first_binary(command: str) -> tuple:
    """(binary, args) of the command, or ("", []) if it cannot be parsed."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return "", []
    if not parts:
        return "", []
    # Skip a leading `sudo`/`env`, but remember it: sudo never reaches `safe`.
    idx = 0
    wrappers = ("env", "nice", "nohup", "time")
    while idx < len(parts) and parts[idx] in wrappers:
        idx += 1
    if idx >= len(parts):
        # The whole command was wrappers, so the last one IS the command:
        # bare `env` prints the environment and is read-only.
        return os.path.basename(parts[-1]), []
    return os.path.basename(parts[idx]), parts[idx + 1:]


def classify(command: str) -> tuple:
    """(tier, reason) for a command: "safe", "confirm" or "blocked"."""
    text = (command or "").strip()
    if not text:
        return "blocked", "empty command"

    for pattern, why in BLOCKED:
        if pattern.search(text):
            return "blocked", why

    # A shell metacharacter means the command is no longer just its first
    # binary, so nothing about it is proven read-only any more.
    if SHELL_METACHARACTERS.search(text):
        return "confirm", "it uses shell operators (pipe, redirect, chain)"

    binary, args = _first_binary(text)
    if not binary:
        return "confirm", "the command could not be parsed"

    if binary in ("sudo", "doas", "su"):
        return "confirm", "it asks for root"

    if binary not in SAFE_BINARIES:
        return "confirm", f"'{binary}' is not on the read-only list"

    writing = WRITING_SUBCOMMANDS.get(binary)
    if writing:
        for arg in args:
            if arg in writing:
                return "confirm", f"'{binary} {arg}' can change things"

    return "safe", "read-only"


def _audit(command: str, tier: str, outcome: str) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG, "a") as handle:
            handle.write(f"{stamp}\t{tier}\t{outcome}\t{command}\n")
    except Exception:  # noqa: BLE001 - never fail a command over its own log
        pass


def _cap(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (text[:MAX_OUTPUT_CHARS]
            + f"\n… output truncated at {MAX_OUTPUT_CHARS} characters")


def run(command: str, approve=None, timeout: int = DEFAULT_TIMEOUT) -> tuple:
    """Run `command` if it is allowed to run. Returns (ok, output).

    `approve` is called with (command, reason) for anything in the `confirm`
    tier and must return True for it to run. Without one — the API, a
    non-interactive shell — those commands are refused rather than assumed
    fine, because there is nobody to ask.
    """
    if not enabled():
        _audit(command, "disabled", "refused")
        return False, disabled_reason()

    tier, reason = classify(command)

    if tier == "blocked":
        _audit(command, "blocked", "refused")
        return False, (f"refused: that command is {reason}. This is not a "
                       "confirmation prompt — commands in this class are never "
                       "run.")

    if tier == "confirm":
        if approve is None:
            _audit(command, "confirm", "no-approver")
            return False, (f"needs confirmation ({reason}), and there is no way "
                           "to ask from here. Run it in the terminal (gathm "
                           "tui), where Gathm can prompt.")
        try:
            granted = bool(approve(command, reason))
        except Exception:  # noqa: BLE001 - a broken approver is a refusal
            granted = False
        if not granted:
            _audit(command, "confirm", "declined")
            return False, "not run — you declined it."

    try:
        proc = subprocess.run(["bash", "-lc", command], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _audit(command, tier, f"timeout-{timeout}s")
        return False, f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        _audit(command, tier, "error")
        return False, str(exc)

    _audit(command, tier, f"exit-{proc.returncode}")
    out = (proc.stdout or "") + (proc.stderr or "")
    out = _cap(out.strip())
    if proc.returncode != 0:
        return False, (out or f"exited {proc.returncode} with no output")
    return True, (out or "(no output)")


if __name__ == "__main__":  # a quick way to see how something is classified
    if len(sys.argv) < 2:
        print("usage: python3 lib/sysexec.py <command…>")
        print(f"platform: {platform_summary()}")
        print(f"enabled : {enabled()}")
        raise SystemExit(0)
    cmd = " ".join(sys.argv[1:])
    verdict, why = classify(cmd)
    print(f"{verdict}: {why}")
