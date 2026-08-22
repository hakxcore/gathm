#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run shell commands on the host machine, on behalf of the agent.

This is the most dangerous thing Gathm can do, and the model deciding what to
run may be a 1B model on a phone. So the question this module answers is not
"how do I run a command" — that is one line — but "which commands may run, and
which need a human first".

Three tiers:

    safe      read-only inspection (uname, df, ps, ls, Get-Process …) with no
              shell metacharacters. Runs immediately: it cannot change anything.
    confirm   everything else. Needs explicit approval from a human before it
              runs. Without an approver, it is refused.
    blocked   catastrophic, irreversible, or a remote-code-execution pattern.
              Never runs, approval or not.

Two rules do most of the work:

  * A shell metacharacter demotes anything to `confirm`. `ls` is safe;
    `ls; rm -rf ~` starts with a safe binary and is not.
  * The allowlist is of binaries that cannot write. Anything absent is not
    assumed hostile, just unproven — it goes to a human.

Platforms
---------

Termux, Linux and macOS are all POSIX: commands run through `bash -lc`, or
`sh -c` where bash is missing, and the POSIX allowlist applies.

Windows has no bash, so commands run through PowerShell (`pwsh` if present,
otherwise `powershell`) and a second allowlist applies — `Get-ChildItem` and
`ipconfig` rather than `ls` and `ifconfig`. PowerShell writes `$env:PATH`
constantly, so `$` on its own is not treated as a metacharacter there, though
`$(...)` still is. Anyone running Git Bash or WSL can say so with
GATHM_SHELL=bash and get the POSIX rules back.

iOS cannot run Gathm as an app at all. What it can run is a terminal that
ships a UNIX userland — iSH (Alpine under emulation) or a-Shell — and inside
those Gathm is just a small Linux, so it is treated as one, with `sh -c`
because neither guarantees bash. a-Shell's sandbox forbids spawning
processes from Python; when that is what is happening, `run()` says so
instead of reporting a mysterious failure.

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

# The same idea in PowerShell and cmd. Stored lower-case because Windows
# command names are case-insensitive and models capitalise them inconsistently.
WINDOWS_SAFE_BINARIES = {
    # identity and platform
    "systeminfo", "hostname", "whoami", "ver", "getmac", "chcp",
    # filesystem inspection
    "dir", "tree", "type", "where", "vol", "fc", "comp", "attrib",
    # processes and resources
    "tasklist", "qwinsta", "driverquery", "gpresult",
    # network inspection
    "ipconfig", "netstat", "route", "ping", "nslookup", "tracert", "pathping",
    "arp", "netsh", "nbtstat",
    # queried, not changed — see WINDOWS_WRITING_SUBCOMMANDS
    "reg", "net", "sc", "wmic", "schtasks", "certutil",
    # PowerShell aliases for the read-only cmdlets, which models love
    "gci", "gc", "gcm", "gps", "gsv", "gm", "gp", "gl", "gu", "gdr", "gal",
    "ls", "cat", "ps", "pwd", "echo", "history", "man", "help",
}

# Cmdlet verbs that are read-only by PowerShell's own naming convention.
# Format- is deliberately absent: Format-Table is harmless, Format-Volume is
# not, and the convention does not distinguish them.
WINDOWS_SAFE_VERBS = (
    "get-", "test-", "measure-", "resolve-", "compare-", "select-", "sort-",
    "convertfrom-", "convertto-", "out-string", "read-host", "show-command",
)

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

# The same, for the Windows tools that are only read-only in their query mode.
WINDOWS_WRITING_SUBCOMMANDS = {
    "reg":     {"add", "delete", "import", "restore", "load", "unload", "copy",
                "save"},
    "net":     {"user", "localgroup", "group", "stop", "start", "use", "share",
                "accounts", "session"},
    "sc":      {"config", "create", "delete", "start", "stop", "pause",
                "failure", "sdset"},
    "wmic":    {"call", "create", "delete", "set"},
    "schtasks":{"/create", "/delete", "/change", "/run", "/end"},
    "netsh":   {"set", "add", "delete", "reset", "import"},
    "certutil":{"-addstore", "-delstore", "-urlcache", "-decode", "-encode",
                "-importpfx"},
    "attrib":  {"+r", "-r", "+h", "-h", "+s", "-s"},
}

# Anything a shell would interpret. Present, and the command is no longer just
# the binary it starts with.
SHELL_METACHARACTERS = re.compile(r"[;&|`$><\n]|\$\(|\|\|")

# PowerShell reads variables with `$` in almost every useful command
# (`Get-ChildItem $env:USERPROFILE`), so a bare `$` cannot mean "suspicious"
# there. `$(...)` still runs code, and still counts.
WINDOWS_METACHARACTERS = re.compile(r"[;&|`><\n]|\$\(")

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

# The Windows half. Checked on every platform — none of these mean anything on
# a Mac, and a model that has confused itself about the platform is exactly the
# case worth catching. Case-insensitive, because Windows is.
BLOCKED_WINDOWS = [
    (r"\bremove-item\b[^|;]*-recurse\b[^|;]*-force\b"
     r"|\bremove-item\b[^|;]*-force\b[^|;]*-recurse\b",
     "a recursive forced delete"),
    (r"\b(del|erase)\b[^|;]*\s/s\b|\brd\b[^|;]*\s/s\b|\brmdir\b[^|;]*\s/s\b",
     "a recursive delete"),
    (r"\bformat\s+[a-z]:|\bformat-volume\b|\bclear-disk\b|\bdiskpart\b"
     r"|\binitialize-disk\b",
     "formatting a disk"),
    (r"\b(stop|restart)-computer\b|\blogoff\b|\bbcdedit\b",
     "shutting the machine down, or changing how it boots"),
    (r"\b(iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod)\b[^|]*\|"
     r"\s*(iex|invoke-expression)\b",
     "piping a download straight into a shell"),
    (r"\b(iex|invoke-expression)\b|\bdownloadstring\b",
     "running text as code"),
    (r"\bvssadmin\b[^|;]*\bdelete\b|\bwbadmin\b[^|;]*\bdelete\b",
     "deleting the shadow copies the machine restores from"),
    (r"\bwevtutil\b\s+cl\b|\bclear-eventlog\b|\bclear-history\b",
     "erasing the event log"),
    (r"\bset-mppreference\b[^|;]*-disable|\badd-mppreference\b"
     r"[^|;]*-exclusionpath",
     "turning off the machine's own defences"),
    (r"\bset-executionpolicy\b[^|;]*\b(bypass|unrestricted)\b",
     "removing PowerShell's script restrictions"),
    (r"\bremove-localuser\b|\bnet\s+user\b[^|;]*\s/delete\b",
     "deleting a user account"),
    (r"\bcipher\b\s+/w|\bsdelete\b", "wiping free space"),
    (r"\bremove-item\b[^|;]*\bhk(lm|cu|cr):|\breg\b\s+delete\b[^|;]*"
     r"\\(windows|microsoft)\\",
     "deleting system registry keys"),
]
BLOCKED_WINDOWS = [(re.compile(p, re.IGNORECASE), why)
                   for p, why in BLOCKED_WINDOWS]


def platform_name() -> str:
    """The platform, in the words the rest of Gathm uses."""
    if "com.termux" in (os.environ.get("PREFIX") or ""):
        return "termux"
    if os.path.isdir("/data/data/com.termux/files/usr"):
        return "termux"
    # iOS terminals, before the darwin/linux checks: iSH emulates x86 Linux and
    # a-Shell runs a Darwin build, so both answer to those otherwise.
    if sys.platform == "ios" or os.path.isdir("/proc/ish"):
        return "ios"
    if sys.platform == "darwin":
        if (platform.machine() or "").startswith(("iPhone", "iPad", "iPod")):
            return "ios"
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform or "unknown"


# How to hand a command line to each shell. The name is what the user would
# put in GATHM_SHELL; the flags are what that shell wants before the command.
_SHELL_FLAGS = {
    "bash": ["-lc"],
    "zsh": ["-lc"],
    "sh": ["-c"],
    "ash": ["-c"],
    "dash": ["-c"],
    "ksh": ["-c"],
    "fish": ["-c"],
    "powershell": ["-NoProfile", "-NonInteractive", "-Command"],
    "pwsh": ["-NoProfile", "-NonInteractive", "-Command"],
    "cmd": ["/d", "/s", "/c"],
}


def _shell_leaf(path: str) -> str:
    """`C:\\Program Files\\PowerShell\\pwsh.exe` -> `pwsh`."""
    leaf = re.split(r"[\\/]", path.strip())[-1]
    if leaf.lower().endswith(".exe"):
        leaf = leaf[:-4]
    return leaf.lower()


def shell_spec(plat: str = "") -> tuple:
    """(argv-prefix, dialect) for running a command on this platform.

    dialect is "posix" or "windows" — it is what the classifier keys off, and
    it follows the shell rather than the OS, so Git Bash on Windows is graded
    by the POSIX rules it will actually use.
    """
    plat = plat or platform_name()

    override = (os.environ.get("GATHM_SHELL") or "").strip()
    if override:
        name = _shell_leaf(override)
        flags = _SHELL_FLAGS.get(name, ["-c"])
        exe = override if (os.sep in override or "/" in override) else \
            (shutil.which(override) or override)
        return [exe] + flags, ("windows" if name in ("powershell", "pwsh", "cmd")
                               else "posix")

    if plat == "windows":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command"], "windows"

    if plat == "ios":
        # Neither iSH nor a-Shell promises bash; both have a POSIX sh.
        return [shutil.which("sh") or "/bin/sh", "-c"], "posix"

    bash = shutil.which("bash")
    if bash:
        return [bash, "-lc"], "posix"
    return [shutil.which("sh") or "/bin/sh", "-c"], "posix"


def shell_dialect(plat: str = "") -> str:
    return shell_spec(plat)[1]


def shell_label(plat: str = "") -> str:
    """The name of the shell commands will run in, for the model's prompt."""
    return _shell_leaf(shell_spec(plat)[0][0])


def platform_summary() -> str:
    """One line describing this machine, for the model's prompt."""
    name = platform_name()
    bits = [name, platform.machine() or "unknown arch"]
    if name == "macos":
        bits.append("macOS " + (platform.mac_ver()[0] or "?"))
    elif name == "termux":
        bits.append("Android/Termux")
    elif name == "ios":
        bits.append("iOS terminal")
    elif name == "windows":
        bits.append("Windows " + (platform.release() or "?"))
    else:
        rel = platform.release()
        if rel:
            bits.append(rel)
    bits.append(shell_label(name))
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


def _leaf(token: str) -> str:
    """The command name out of a Windows token, path and extension removed."""
    name = re.split(r"[\\/]", token.strip().strip('"').strip("'"))[-1]
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return name.lower()


def _first_binary(command: str, dialect: str = "posix") -> tuple:
    """(binary, args) of the command, or ("", []) if it cannot be parsed."""
    try:
        parts = shlex.split(command, posix=(dialect != "windows"))
    except ValueError:
        return "", []
    if not parts:
        return "", []
    if dialect == "windows":
        # Windows has no `env`/`nohup` wrappers worth unwrapping, and its paths
        # are full of backslashes that POSIX splitting would eat.
        return _leaf(parts[0]), [p.strip('"') for p in parts[1:]]
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


def _windows_is_read_only(binary: str) -> bool:
    if binary in WINDOWS_SAFE_BINARIES or binary in SAFE_BINARIES:
        return True
    return binary.startswith(WINDOWS_SAFE_VERBS)


def classify(command: str, dialect: str = "") -> tuple:
    """(tier, reason) for a command: "safe", "confirm" or "blocked"."""
    text = (command or "").strip()
    if not text:
        return "blocked", "empty command"

    dialect = dialect or shell_dialect()

    for pattern, why in BLOCKED:
        if pattern.search(text):
            return "blocked", why
    for pattern, why in BLOCKED_WINDOWS:
        if pattern.search(text):
            return "blocked", why

    # A shell metacharacter means the command is no longer just its first
    # binary, so nothing about it is proven read-only any more.
    metacharacters = (WINDOWS_METACHARACTERS if dialect == "windows"
                      else SHELL_METACHARACTERS)
    if metacharacters.search(text):
        return "confirm", "it uses shell operators (pipe, redirect, chain)"

    binary, args = _first_binary(text, dialect)
    if not binary:
        return "confirm", "the command could not be parsed"

    if dialect == "windows":
        if binary in ("runas", "start-process", "sudo", "gsudo"):
            return "confirm", "it asks for administrator rights"
        if not _windows_is_read_only(binary):
            return "confirm", f"'{binary}' is not on the read-only list"
        writing = WINDOWS_WRITING_SUBCOMMANDS.get(binary)
        if writing:
            for arg in args:
                if arg.lower() in writing:
                    return "confirm", f"'{binary} {arg}' can change things"
        return "safe", "read-only"

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


def _spawn_failure(exc: Exception, plat: str, argv: list) -> str:
    """Explain a failure to start the shell, rather than repeating errno."""
    if plat == "ios":
        return ("could not start a shell. On iOS, a-Shell's sandbox does not "
                "let Python spawn processes at all; iSH can. If you are in "
                "a-Shell, system control cannot work there — use iSH, or run "
                "Gathm on another machine. " + str(exc))
    if isinstance(exc, FileNotFoundError):
        return (f"could not find the shell '{argv[0]}'. Set GATHM_SHELL to one "
                f"that exists on this machine. " + str(exc))
    return str(exc)


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

    plat = platform_name()
    argv_prefix, dialect = shell_spec(plat)
    tier, reason = classify(command, dialect)

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
        proc = subprocess.run(argv_prefix + [command], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _audit(command, tier, f"timeout-{timeout}s")
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        _audit(command, tier, "spawn-failed")
        return False, _spawn_failure(exc, plat, argv_prefix)
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
        print(f"platform: {platform_summary()}")
        print(f"shell   : {' '.join(shell_spec()[0])}  ({shell_dialect()})")
        print(f"enabled : {enabled()}")
        print("usage: python3 lib/sysexec.py <command…>")
        raise SystemExit(0)
    cmd = " ".join(sys.argv[1:])
    verdict, why = classify(cmd)
    print(f"{verdict}: {why}")
