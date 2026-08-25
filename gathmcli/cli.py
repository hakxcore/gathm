#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The `gathm` console script: hand off to the bundled bash launcher.

Three jobs, and only three:

1. Find the bundled tree.
2. Tell the launcher which Python to use — the one pipx built, which is the
   whole point of installing this way.
3. exec the launcher, so signals and exit codes behave exactly as they do from
   a checkout.

`exec` rather than `subprocess` is deliberate. The launcher itself `exec`s Pilot
so that Ctrl+C reaches the right process group, and this session spent real time
getting that right; adding a Python parent in the middle would put a process
between the terminal and the thing meant to receive the signal.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

BUNDLE_NAME = "_bundle"

# Where bash lives, in the order worth trying. Termux is first because its bash
# is not in /bin and never will be.
_BASH_CANDIDATES = (
    "/data/data/com.termux/files/usr/bin/bash",
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/local/bin/bash",
    "/opt/homebrew/bin/bash",
)

# What the parts of setup actually cost, so nobody watches a blank screen
# wondering whether it hung. The wide ranges are honest: a phone compiling
# audio.cpp is a different machine from a laptop doing it.
SETUP_ESTIMATES = {
    "termux": [
        ("Python dependencies (pipx does this)", "2-5 minutes"),
        ("jq, ffmpeg and friends", "1-3 minutes"),
        ("audio.cpp speech runtime, compiled", "20-60 minutes, once"),
        ("Speech models, downloaded", "3-10 minutes on mobile data"),
        ("An Ollama model", "depends on the model and your connection"),
    ],
    "macos": [
        ("Python dependencies (pipx does this)", "1-2 minutes"),
        ("Homebrew packages (jq, cmake, ninja)", "1-5 minutes"),
        ("audio.cpp speech runtime, compiled", "2-6 minutes, once"),
        ("Speech models, downloaded", "1-3 minutes"),
        ("An Ollama model", "depends on the model and your connection"),
    ],
    "linux": [
        ("Python dependencies (pipx does this)", "1-2 minutes"),
        ("Distro packages (jq, ffmpeg)", "1-3 minutes"),
        ("audio.cpp speech runtime", "not built here — voice is optional"),
        ("An Ollama model", "depends on the model and your connection"),
    ],
    "windows": [
        ("Python dependencies (pipx does this)", "1-2 minutes"),
        ("A POSIX shell (Git Bash or WSL)", "required — see below"),
        ("audio.cpp speech runtime", "not built here — voice is unavailable"),
        ("An Ollama model", "depends on the model and your connection"),
    ],
}


def bundle_dir() -> Path:
    """The bundled Gathm tree that ships inside this package."""
    return Path(__file__).resolve().parent / BUNDLE_NAME


def platform_name() -> str:
    """Same vocabulary lib/sysexec.py uses, without importing it.

    The bundle is not on sys.path yet at this point, and duplicating six lines
    beats manipulating sys.path before we know the bundle is even intact.
    """
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


def find_bash() -> str:
    """A bash to run the launcher with, or "" if there is none."""
    found = shutil.which("bash")
    if found:
        return found
    for candidate in _BASH_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def ensure_executable(bundle: Path) -> None:
    """Restore the execute bit on the bundled scripts.

    Wheels are zip files, and whether the execute bit survives the round trip
    depends on the build backend, the zip implementation and the installer. The
    56 tool scripts are run directly by the orchestrator, so losing it turns
    every tool into "Permission denied". Cheap to fix and cheap to re-check, so
    it is done rather than assumed.

    A marker file keeps it to once per install. If the bundle is read-only the
    marker cannot be written, and the chmod pass simply runs each time — still
    correct, just not free.
    """
    marker = bundle / ".exec-bits-set"
    if marker.exists():
        return

    patterns = ("gathm", "install", "*.sh", "*.bash")
    targets: list = []
    for name in ("gathm", "install"):
        candidate = bundle / name
        if candidate.is_file():
            targets.append(candidate)
    for pattern in ("*.sh", "*.bash"):
        targets.extend(bundle.rglob(pattern))
    # Tool entry points are named after their directory and have no extension,
    # which is why a glob over extensions alone would miss all 56 of them.
    tools = bundle / "tools"
    if tools.is_dir():
        for tool in tools.iterdir():
            if tool.is_dir():
                entry = tool / tool.name
                if entry.is_file():
                    targets.append(entry)

    for path in targets:
        try:
            mode = path.stat().st_mode
            path.chmod(mode | 0o111)
        except OSError:
            pass          # a read-only bundle is not a reason to refuse to run

    try:
        marker.write_text("")
    except OSError:
        pass


def _print_estimates() -> None:
    name = platform_name()
    rows = SETUP_ESTIMATES.get(name, SETUP_ESTIMATES["linux"])
    print()
    print(f"Setting up Gathm on {name}. Roughly what this costs:")
    print()
    width = max(len(label) for label, _ in rows)
    for label, cost in rows:
        print(f"  {label.ljust(width)}   {cost}")
    print()
    if name == "termux":
        print("  The compile is the long one, and it happens once. It is also")
        print("  optional: skip it and Gathm works, without voice.")
    if name == "windows":
        print("  Gathm's launcher is bash. Install Git for Windows or WSL,")
        print("  then run this from that shell.")
    print()


def _missing_bundle(bundle: Path) -> int:
    sys.stderr.write(
        f"\nGathm's files are missing from {bundle}.\n\n"
        "The package installed but its bundled tree did not, which usually\n"
        "means an incomplete or hand-edited install. Reinstall with:\n"
        "    pipx reinstall gathm\n\n"
    )
    return 1


def _no_bash() -> int:
    sys.stderr.write(
        "\nGathm needs bash, and there is none on this system.\n\n"
    )
    if platform_name() == "windows":
        sys.stderr.write(
            "On Windows, install Git for Windows (which provides Git Bash) or\n"
            "enable WSL, then run gathm from that shell. The Python half is\n"
            "already installed and will be reused.\n\n"
        )
    else:
        sys.stderr.write(
            "Install bash with your package manager and try again.\n\n"
        )
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    bundle = bundle_dir()
    launcher = bundle / "gathm"

    if not launcher.is_file():
        return _missing_bundle(bundle)

    if argv and argv[0] in ("--where", "--bundle"):
        print(bundle)
        return 0

    bash = find_bash()
    if not bash:
        return _no_bash()

    ensure_executable(bundle)

    # `gathm setup` runs the bundled installer for the parts pipx cannot do:
    # the native speech runtime, jq, ffmpeg. Estimates first, because the
    # audio.cpp compile is long enough that silence reads as a hang.
    if argv and argv[0] == "setup":
        installer = bundle / "install"
        if not installer.is_file():
            sys.stderr.write("The bundled installer is missing.\n")
            return 1
        _print_estimates()
        target, rest = str(installer), argv[1:]
    else:
        target, rest = str(launcher), argv

    env = dict(os.environ)
    # The point of installing through pipx: this interpreter already has
    # langchain, rich and prompt_toolkit. Without this the launcher would look
    # for a checkout's pilot/venv, not find one, and fall back to whatever
    # python3 is on PATH — which is not the one holding the dependencies.
    env.setdefault("GATHM_PYTHON", sys.executable)
    env.setdefault("GATHM_INSTALL_KIND", "pipx")

    try:
        os.execve(bash, [bash, target, *rest], env)
    except OSError as exc:
        sys.stderr.write(f"\nCould not start {target} with {bash}: {exc}\n\n")
        return 1
    return 0          # unreachable: execve replaced the process


if __name__ == "__main__":
    raise SystemExit(main())
