"""Typed host controls. No model-authored shell or script is ever executed.

prepare() inspects and captures targets; it never changes the system. Callers
must obtain human approval before execute() for a confirmation-required plan.
The API keeps plans private and consumes one-use approvals before execution.
"""
from __future__ import annotations

import configparser
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

from lib.sysexec import platform_name

ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT = 12000
READ_ACTIONS = {"status", "volume.get", "brightness.get", "apps.list",
                "files.list", "files.count", "processes.list"}
DOCUMENT_SUFFIXES = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif",
                     ".webp", ".csv", ".docx", ".xlsx", ".pptx", ".mp3", ".wav", ".mp4"}


def _run(argv, *, env=None, timeout=15):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL, env=env)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or
                          "The operating system refused the action.").strip()[:MAX_OUTPUT])
    return result.stdout.strip()[:MAX_OUTPUT]


def _which(name):
    value = shutil.which(name)
    if not value:
        raise ValueError(f"{name} is unavailable. Install/configure it yourself if you need this control.")
    return value


def _powershell(script, values=None):
    env = dict(os.environ)
    env.update(values or {})
    binary = shutil.which("pwsh") or shutil.which("powershell")
    if not binary:
        raise ValueError("PowerShell is unavailable on this device.")
    return _run([binary, "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference='Stop'; " + script], env=env)


def _fingerprint(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size]


def _path_within(path, parent):
    """Containment for protected paths, including common case-insensitive OSes.

    macOS Path equality is case-sensitive even on its usual case-insensitive
    volumes. Conservatively protect case variants there (and on Windows),
    including when the destination does not yet exist.
    """
    parts, parent_parts = path.parts, parent.parts
    if platform_name() in {"macos", "windows"}:
        parts = tuple(p.casefold() for p in parts)
        parent_parts = tuple(p.casefold() for p in parent_parts)
    return parts[:len(parent_parts)] == parent_parts


def _home_path(value, *, write=False, allow_missing=False):
    if not value or "\x00" in value:
        raise ValueError("Provide a file or folder path.")
    original = Path(value).expanduser()
    if not original.is_absolute():
        raise ValueError("Use an absolute path or ~/path so the target is unambiguous.")
    # Reject symlinks in any path component, including the final target.
    for part in (original, *original.parents):
        if part.is_symlink():
            raise ValueError("Symlink targets are not supported by file controls.")
    path, home = original.resolve(), Path.home().resolve()
    if not _path_within(path, home):
        raise ValueError("File controls are limited to your home folder.")
    if not allow_missing and not path.exists():
        raise ValueError("That file or folder does not exist.")
    if not path.parent.is_dir():
        raise ValueError("The parent folder must already exist.")
    if write:
        protected = [home / n for n in (".gathm", ".ssh", ".aws", ".gnupg", ".config",
                                         ".codex", ".agents", ".kube", ".docker", ".azure",
                                         ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
                                         "Library", "AppData")]
        protected.append(ROOT)
        if (_path_within(home, path) or
                any(_path_within(path, p) or _path_within(p, path) for p in protected)):
            raise ValueError("This location contains application, credential, or system configuration and is protected.")
        parts = path.parts[len(home.parts):]
        if platform_name() in {"macos", "windows"}:
            parts = tuple(p.casefold() for p in parts)
        if any(p == ".git" or p.startswith(".env") for p in parts):
            raise ValueError("Repository metadata and environment files are protected.")
    return path


def _installed_apps(plat):
    apps = {}
    if plat == "macos":
        roots = [Path("/Applications"), Path("/System/Applications"),
                 Path("/System/Applications/Utilities"), Path.home() / "Applications"]
        for root in roots:
            if root.is_dir():
                for item in sorted(root.glob("*.app")):
                    if not item.is_symlink():
                        apps[str(item)] = item.stem
    elif plat == "linux":
        for root in (Path("/usr/share/applications"), Path("/usr/local/share/applications"),
                     Path.home() / ".local/share/applications"):
            if not root.is_dir():
                continue
            for item in sorted(root.glob("*.desktop")):
                if item.is_symlink():
                    continue
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    parser.read(item)
                    entry = parser["Desktop Entry"]
                    if entry.get("Type") == "Application" and entry.get("Hidden", "false") != "true":
                        apps[str(item)] = entry.get("Name", item.stem)
                except (OSError, KeyError, configparser.Error):
                    continue
    elif plat == "windows":
        for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
            if base:
                root = Path(base) / "Microsoft/Windows/Start Menu/Programs"
                for item in sorted(root.rglob("*.lnk")):
                    if not item.is_symlink():
                        apps[str(item)] = item.stem
    else:
        raise ValueError("Listing/launching installed apps is unavailable inside this mobile terminal. Use the device launcher.")
    return apps


def _process_identity(pid, plat):
    if pid <= 1:
        raise ValueError("System processes cannot be stopped.")
    if plat == "windows":
        raw = _powershell(
            "$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$env:GATHM_CONTROL_PID); "
            "if (!$p) {throw 'Process no longer exists'}; "
            "$o=Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid; "
            "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
            "if ($o.ReturnValue -ne 0 -or $o.Sid -ne $sid) {throw 'Only your own processes can be stopped'}; "
            "$p | Select-Object ProcessId,ParentProcessId,CreationDate,Name | ConvertTo-Json -Compress",
            {"GATHM_CONTROL_PID": str(pid)})
        item = json.loads(raw)
        return [pid, str(item["CreationDate"]), item["Name"]]
    raw = _run([_which("ps"), "-p", str(pid), "-o", "uid=,lstart=,comm="])
    fields = raw.split(None, 1)
    if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) != os.getuid():
        raise ValueError("Only your own running processes can be stopped.")
    return [pid, fields[0], fields[1]]


def _ancestors(plat):
    current, found = os.getpid(), set()
    for _ in range(64):
        if current <= 1 or current in found:
            break
        found.add(current)
        try:
            if plat == "windows":
                parent = _powershell(
                    "(Get-CimInstance Win32_Process -Filter ('ProcessId='+$env:GATHM_CONTROL_PID)).ParentProcessId",
                    {"GATHM_CONTROL_PID": str(current)})
            else:
                parent = _run([_which("ps"), "-p", str(current), "-o", "ppid="])
            current = int(parent)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            raise ValueError("Could not verify the process family; stopping a process is unavailable.")
    return found


def _support(action, plat):
    """Check required programs without running a mutation or asking for privileges."""
    family = action.split(".")[0]
    if action in {"files.move", "files.trash"}:
        _rename_api()
    if action == "status" or family == "files" and action not in {"files.open"}:
        return
    if family == "apps":
        if plat not in {"macos", "linux", "windows"}:
            raise ValueError("App management is unavailable inside this mobile terminal.")
        if action == "apps.open":
            _which("open" if plat == "macos" else "gio" if plat == "linux" else
                   ("pwsh" if shutil.which("pwsh") else "powershell"))
        return
    if plat == "ios":
        raise ValueError("iOS terminal sandboxes do not support this device control.")
    if family == "processes":
        _which("powershell" if plat == "windows" and not shutil.which("pwsh") else
               "pwsh" if plat == "windows" else "ps")
        return
    if family == "volume":
        if plat not in {"macos", "linux", "termux"}:
            raise ValueError("Volume control is not supported on this platform.")
        _which({"macos": "osascript", "linux": "pactl", "termux": "termux-volume"}[plat])
        if plat == "termux" and action in {"volume.mute", "volume.unmute"}:
            raise ValueError("On Termux use 'volume set 0' to mute and set a percentage to restore volume.")
        return
    if family == "brightness":
        if plat == "macos":
            _which("brightness")
        elif plat == "linux":
            _which("brightnessctl")
        elif plat == "termux":
            if action == "brightness.get":
                raise ValueError("Termux:API supports setting brightness, not reading the current value.")
            _which("termux-brightness")
        elif plat == "windows":
            _which("pwsh" if shutil.which("pwsh") else "powershell")
        else:
            raise ValueError("Brightness control is unavailable on this platform.")
        return
    if action == "files.open":
        _which({"macos": "open", "linux": "xdg-open", "termux": "termux-open"}.get(plat,
               "pwsh" if shutil.which("pwsh") else "powershell"))
        return
    if action == "screenshot":
        if plat == "macos":
            _which("screencapture")
        elif plat == "linux":
            if not any(shutil.which(n) for n in ("gnome-screenshot", "grim", "scrot")):
                raise ValueError("No supported screenshot utility is installed (gnome-screenshot, grim, or scrot).")
        elif plat == "windows":
            _which("pwsh" if shutil.which("pwsh") else "powershell")
        else:
            raise ValueError("Android/iOS screen capture is not available to this terminal without separate OS consent.")
        return
    if family == "power":
        if plat == "macos":
            _which("osascript" if action != "power.sleep" else "pmset")
            if action == "power.lock" and not Path(_MAC_LOCK).is_file():
                raise ValueError("The macOS lock helper is unavailable on this version.")
        elif plat == "linux":
            _which("loginctl" if action == "power.lock" else "systemctl")
        elif plat == "windows":
            _which("rundll32" if action == "power.lock" else "shutdown" if action in
                   {"power.restart", "power.shutdown"} else
                   "pwsh" if shutil.which("pwsh") else "powershell")
        else:
            raise ValueError("Rootless Termux cannot lock, sleep, restart, or shut down Android. Use Android's power controls.")
        return
    raise ValueError("This control is unavailable on this platform.")


_MAC_LOCK = "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"


def prepare(command):
    if not isinstance(command, str) or len(command) > 2048 or any(ord(c) < 32 for c in command):
        raise ValueError("Provide a single typed control command (up to 2048 characters).")
    try:
        parts = shlex.split(command)
    except ValueError:
        raise ValueError("The control command has unmatched quotes.")
    if not parts:
        raise ValueError("Try control status for available actions.")
    # Small models sometimes omit the power group. These exact typed aliases
    # still produce the same plan and require the same human confirmation.
    if parts[0] in {"restart", "reboot", "shutdown", "sleep", "lock"}:
        parts = ["power", "restart" if parts[0] == "reboot" else parts[0], *parts[1:]]
    action, args = parts[0], parts[1:]
    if action in {"volume", "brightness", "apps", "files", "processes", "power"}:
        if not args:
            raise ValueError("Provide a control action, for example volume get or files list ~/Downloads.")
        action += "." + args.pop(0)
    arities = {"status": 0, "volume.get": 0, "volume.set": 1, "volume.mute": 0, "volume.unmute": 0,
               "brightness.get": 0, "brightness.set": 1, "apps.list": 0, "apps.open": 1,
               "files.list": 1, "files.count": 1, "files.open": 1, "files.mkdir": 1, "files.move": 2, "files.trash": 1,
               "processes.list": 0, "processes.stop": 1, "power.lock": 0, "power.sleep": 0,
               "power.restart": 0, "power.shutdown": 0, "screenshot": 0}
    if action not in arities or len(args) != arities[action]:
        raise ValueError("Unknown control or wrong number of arguments. Use control status for help; quote paths/app names containing spaces.")
    plat = platform_name()
    _support(action, plat)
    plan = {"action": action, "platform": plat,
            "requires_confirmation": action not in READ_ACTIONS, "warning": "", "_identity": {}}
    if action in {"volume.set", "brightness.set"}:
        if not re.fullmatch(r"\d{1,3}", args[0]) or not 0 <= int(args[0]) <= 100:
            raise ValueError("Use a whole percentage from 0 to 100.")
        args[0] = str(int(args[0]))
        summary = f"Set {action.split('.')[0]} to {args[0]}%."
    elif action.startswith("files."):
        write = action not in {"files.list", "files.count"}
        paths = [_home_path(a, write=write, allow_missing=action == "files.mkdir" or
                            (action == "files.move" and i == 1)) for i, a in enumerate(args)]
        if action in {"files.mkdir", "files.move"} and paths[-1].exists():
            raise ValueError("The destination already exists; file controls never overwrite it.")
        if action in {"files.list", "files.count"} and not paths[0].is_dir():
            raise ValueError("Provide a folder to list.")
        if action == "files.open" and not paths[0].is_dir() and paths[0].suffix.lower() not in DOCUMENT_SUFFIXES:
            raise ValueError("Opening executable/script files is not supported. Use a document, media file, or folder.")
        if action == "files.move" and (paths[0] == paths[1] or paths[0] in paths[1].parents):
            raise ValueError("A folder cannot be moved into itself.")
        args = [str(p) for p in paths]
        for path in paths:
            plan["_identity"][str(path)] = _fingerprint(path) if path.exists() else None
            # A replaced parent may change the meaning of an approved destination.
            plan["_identity"]["parent:" + str(path.parent)] = _fingerprint(path.parent)[:2]
        labels = {"files.list": "List folder", "files.count": "Count files in folder", "files.open": "Open", "files.mkdir": "Create folder",
                  "files.move": "Move", "files.trash": "Move to Gathm's recoverable trash"}
        summary = f"{labels[action]}: {args[0]}" + (f" → {args[1]}" if len(args) == 2 else "")
        if action == "files.trash":
            plan["warning"] = "The item will move to ~/.gathm/trash, with its original location recorded. It will not be permanently deleted."
    elif action == "apps.open":
        aliases = {"apple music": "Music", "email": "Mail", "settings": "System Settings",
                   "system preferences": "System Settings", "safari browser": "Safari"}
        target = aliases.get(args[0].casefold(), args[0]) if plat == "macos" else args[0]
        matches = [p for p, name in _installed_apps(plat).items()
                   if target == p or target.casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError("Choose one installed app from 'control apps list'; the name is missing or ambiguous.")
        args = matches
        plan["_identity"] = {args[0]: _fingerprint(Path(args[0]))}
        summary = f"Open installed application: {args[0]}"
    elif action == "processes.stop":
        if not re.fullmatch(r"\d{1,10}", args[0]):
            raise ValueError("Provide one numeric process ID from control processes list.")
        pid = int(args[0])
        if pid in _ancestors(plat):
            raise ValueError("Gathm and its parent processes cannot be stopped through this control.")
        plan["_identity"] = {"process": _process_identity(pid, plat)}
        args[0] = str(pid)
        summary = f"Request termination of process {pid}: {plan['_identity']['process'][-1]}"
        plan["warning"] = "Unsaved work in this process may be lost. No force-kill will be used."
    else:
        summaries = {"status": "Show supported system controls.", "volume.get": "Read output volume.",
                     "volume.mute": "Mute output audio.", "volume.unmute": "Unmute output audio.",
                     "brightness.get": "Read screen brightness.", "apps.list": "List installed applications.",
                     "processes.list": "List running processes.", "power.lock": "Lock this device.",
                     "power.sleep": "Put this device to sleep.", "power.restart": "Restart this device.",
                     "power.shutdown": "Shut down this device.",
                     "screenshot": "Capture this device's screen and save it locally in ~/.gathm/screenshots."}
        summary = summaries[action]
        if action in {"power.restart", "power.shutdown", "power.sleep"}:
            plan["warning"] = "This will interrupt Gathm and other running work. Save your work first; unsaved changes may be lost."
        elif action == "screenshot":
            plan["warning"] = "The image may include private information visible on your screen. It stays on this device. OS screen-recording permission may be required."
    plan.update(command=shlex.join(action.split(".") + args), summary=summary, _args=args)
    return plan


def _private_dir(name=""):
    root = Path.home() / ".gathm"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("Gathm's data folder is not a normal directory.")
    root.mkdir(mode=0o700, exist_ok=True)
    path = root / name if name else root
    if path.is_symlink():
        raise ValueError("Gathm's data folder must not be a symlink.")
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _audit(plan, outcome):
    path = _private_dir() / "control.log"
    if path.is_symlink():
        raise ValueError("The control audit log must not be a symlink.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "command": plan["command"], "outcome": outcome}) + "\n")


def _status(plat):
    lines = [f"Device: {socket.gethostname()} ({plat}). Controls apply to the device running Gathm.",
             "Changes require one-time approval. Raw shell access is separate."]
    for action in ("volume.get", "volume.set", "brightness.get", "brightness.set", "apps.list", "apps.open",
                   "files.list", "files.count", "files.open", "files.mkdir", "files.move", "files.trash", "processes.list",
                   "processes.stop", "screenshot", "power.lock", "power.sleep", "power.restart", "power.shutdown"):
        try:
            _support(action, plat)
            state = "available (subject to OS permissions)"
        except ValueError as exc:
            state = str(exc)
        lines.append(action.replace(".", " ") + ": " + state)
    lines.append("Examples: control volume set 40; control files list ~/Downloads; control apps open 'Calculator'.")
    return "\n".join(lines)


def _perform(plan):
    action, args, plat = plan["action"], plan["_args"], plan["platform"]
    if action == "status":
        return _status(plat)
    if action == "apps.list":
        return "\n".join(f"{name}: {path}" for path, name in _installed_apps(plat).items())[:MAX_OUTPUT] or "No supported installed apps found."
    if action == "apps.open":
        if plat == "windows":
            _powershell("Start-Process -FilePath $env:GATHM_CONTROL_TARGET", {"GATHM_CONTROL_TARGET": args[0]})
        else:
            _run([_which("open"), "-a", args[0]] if plat == "macos" else
                 [_which("gio"), "launch", args[0]])
        return "Launch requested for " + args[0]
    if action == "files.list":
        entries = sorted(Path(args[0]).iterdir(), key=lambda p: p.name.casefold())
        return "\n".join(p.name + ("/" if p.is_dir() else "") for p in entries[:300])[:MAX_OUTPUT] or "The folder is empty."
    if action == "files.count":
        counts = {"Files": 0, "Folders": 0, "Symlinks": 0, "Other": 0}
        with os.scandir(args[0]) as entries:
            for entry in entries:
                kind = ("Symlinks" if entry.is_symlink() else "Files" if entry.is_file(follow_symlinks=False)
                        else "Folders" if entry.is_dir(follow_symlinks=False) else "Other")
                counts[kind] += 1
        return ("Folder: " + args[0] + "\n" + "\n".join(f"{k}: {v}" for k, v in counts.items())
                + "\nDirect entries only; hidden entries included; symlinks not followed.")
    if action == "files.mkdir":
        Path(args[0]).mkdir()
        return "Created folder: " + args[0]
    if action == "files.move":
        _move_no_replace(Path(args[0]), Path(args[1]))
        return f"Moved {args[0]} to {args[1]}"
    if action == "files.trash":
        bucket = _private_dir("trash") / uuid.uuid4().hex
        bucket.mkdir(mode=0o700)
        metadata = bucket / "original-location.json"
        # Keep metadata separate from the item, even for this exact filename.
        destination = bucket / "item"
        metadata.write_text(json.dumps({"original": args[0], "item": str(destination)}) + "\n")
        _move_no_replace(Path(args[0]), destination)
        return f"Moved to recoverable trash: {destination}\nOriginal location: {args[0]}\nTo restore, move the item back using your file manager. Metadata: {metadata}"
    if action == "files.open":
        if plat == "windows":
            _powershell("Start-Process -FilePath $env:GATHM_CONTROL_TARGET", {"GATHM_CONTROL_TARGET": args[0]})
        else:
            _run([_which({"macos": "open", "linux": "xdg-open", "termux": "termux-open"}[plat]), args[0]])
        return "Open requested for " + args[0]
    if action == "processes.list":
        if plat == "windows":
            return _powershell("Get-Process | Select-Object Id,ProcessName,CPU | Format-Table -AutoSize | Out-String -Width 160")
        return _run([_which("ps"), "-u", str(os.getuid()), "-o", "pid=,comm="])
    if action == "processes.stop":
        pid = int(args[0])
        if plat == "windows":
            # CloseMainWindow asks a GUI app to close normally; never force kill.
            _powershell("$p=Get-Process -Id ([int]$env:GATHM_CONTROL_PID); if (!$p.CloseMainWindow()) {throw 'This process has no closeable window; it was not force-killed'}", {"GATHM_CONTROL_PID": str(pid)})
        else:
            os.kill(pid, signal.SIGTERM)
        return f"Termination requested for process {pid}; the application may still need to save or close."
    if action.startswith("volume."):
        if plat == "macos":
            scripts = {"volume.get": "get volume settings", "volume.mute": "set volume output muted true",
                       "volume.unmute": "set volume output muted false"}
            script = scripts.get(action, "set volume output volume " + (args[0] if args else "0"))
            value = _run([_which("osascript"), "-e", script])
        elif plat == "linux":
            command = {"volume.get": ["get-sink-volume", "@DEFAULT_SINK@"],
                       "volume.mute": ["set-sink-mute", "@DEFAULT_SINK@", "1"],
                       "volume.unmute": ["set-sink-mute", "@DEFAULT_SINK@", "0"],
                       "volume.set": ["set-sink-volume", "@DEFAULT_SINK@", (args[0] if args else "0") + "%"]}[action]
            value = _run([_which("pactl"), *command])
        else:
            current = json.loads(_run([_which("termux-volume")]))
            if action == "volume.get":
                value = json.dumps(current)
            else:
                music = next((s for s in current if s.get("stream") == "music"), None)
                if not music:
                    raise ValueError("Termux:API did not return the media volume range.")
                level = round(int(args[0]) * int(music["max_volume"]) / 100)
                value = _run([_which("termux-volume"), "music", str(level)])
        return value or plan["summary"] + " Requested successfully."
    if action.startswith("brightness."):
        if plat == "macos":
            value = _run([_which("brightness"), "-l"] if action.endswith("get") else
                         [_which("brightness"), str(int(args[0]) / 100)])
        elif plat == "linux":
            value = _run([_which("brightnessctl"), "info"] if action.endswith("get") else
                         [_which("brightnessctl"), "set", args[0] + "%"])
        elif plat == "termux":
            value = _run([_which("termux-brightness"), str(round(int(args[0]) * 255 / 100))])
        else:
            value = _powershell("$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness; if (!$m) {throw 'No supported display'}; $m | Select-Object CurrentBrightness | ConvertTo-Json -Compress" if action.endswith("get") else
                "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods; if (!$m) {throw 'No supported display'}; $m | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=0;Brightness=[byte]$env:GATHM_CONTROL_LEVEL}",
                {"GATHM_CONTROL_LEVEL": args[0] if args else "0"})
        return value or plan["summary"] + " Requested successfully."
    if action == "screenshot":
        path = _private_dir("screenshots") / (uuid.uuid4().hex + ".png")
        if plat == "macos":
            _run([_which("screencapture"), "-x", str(path)])
        elif plat == "linux":
            binary = next(n for n in ("gnome-screenshot", "grim", "scrot") if shutil.which(n))
            _run([_which(binary), *(["-f"] if binary == "gnome-screenshot" else []), str(path)])
        else:
            _powershell("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                        "$r=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
                        "$b=New-Object System.Drawing.Bitmap($r.Width,$r.Height); $g=[System.Drawing.Graphics]::FromImage($b); "
                        "try {$g.CopyFromScreen($r.Left,$r.Top,0,0,$r.Size); $b.Save($env:GATHM_CONTROL_TARGET)} finally {$g.Dispose(); $b.Dispose()}",
                        {"GATHM_CONTROL_TARGET": str(path)})
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("The OS did not produce a screenshot. Check screen-recording permission.")
        path.chmod(0o600)
        return "Screenshot saved locally: " + str(path)
    if action.startswith("power."):
        kind = action.split(".")[1]
        if plat == "macos":
            if kind == "lock":
                _run([_MAC_LOCK, "-suspend"])
            elif kind == "sleep":
                _run([_which("pmset"), "sleepnow"])
            else:
                _run([_which("osascript"), "-e", 'tell application "System Events" to ' +
                      ("restart" if kind == "restart" else "shut down")])
        elif plat == "linux":
            _run([_which("loginctl"), "lock-session"] if kind == "lock" else
                 [_which("systemctl"), "--no-ask-password", {"sleep": "suspend", "restart": "reboot", "shutdown": "poweroff"}[kind]])
        elif kind == "lock":
            _run([_which("rundll32"), "user32.dll,LockWorkStation"])
        elif kind == "sleep":
            _powershell("Add-Type -AssemblyName System.Windows.Forms; if (![System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)) {throw 'Sleep was refused'}")
        else:
            _run([_which("shutdown"), "/r" if kind == "restart" else "/s", "/t", "0"])
        return plan["summary"] + " Requested from the operating system; Gathm may disconnect."
    raise ValueError("Unsupported system control.")


def execute(plan):
    """Execute a prepared plan after the caller has obtained required approval.

    Rebuild all commands from typed input and compare targets. Executable paths,
    scripts and private fields supplied by a caller are never blindly trusted.
    """
    try:
        current = prepare(plan["command"])
        if current != plan:
            raise ValueError("The action or target changed since it was proposed. Request a fresh approval.")
        if current["requires_confirmation"]:
            _audit(current, "started")
        output = _perform(current)
        if current["requires_confirmation"]:
            try:
                _audit(current, "completed")
            except (OSError, ValueError):
                output += "\nThe action completed, but its final audit entry could not be written."
        return {"ok": True, "output": output}
    except (ValueError, OSError, KeyError, TypeError, StopIteration, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "output": f"System action was not completed or its result could not be verified: {exc}. It was not retried."}


def _rename_api():
    """Find the native no-replace entry point, without performing a move."""
    if os.name == "nt":
        return None
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(library, "renamex_np", None)
        # <sys/stdio.h>: RENAME_EXCL = 0x00000004 (macOS 10.12+).
        signature = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    elif sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
        # Linux: AT_FDCWD = -100, RENAME_NOREPLACE = 1. Termux uses this
        # path too; older Android/libc versions fail closed if it is missing.
        signature = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    else:
        function = None
    if function is None:
        raise ValueError("Atomic no-overwrite moves are unavailable on this platform; the source was not moved.")
    function.argtypes, function.restype = signature, ctypes.c_int
    return function


def _move_no_replace(source, destination):
    """Atomically rename, with the OS enforcing an absent destination.

    Reserving a destination and then renaming can replace a different empty
    directory. Linking then unlinking can delete a newly saved source version.
    Both files and directories therefore use the same native no-replace move.
    There is deliberately no copying or non-atomic fallback, including across
    filesystems or on a kernel/filesystem without the required operation.
    """
    function = _rename_api()
    if os.name == "nt":
        # Windows rename already fails if *any* destination exists. Do not
        # reserve that destination first, or every directory move would fail.
        os.rename(source, destination)
        return
    source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
    arguments = ((source_bytes, destination_bytes, 0x00000004) if sys.platform == "darwin" else
                 (-100, source_bytes, -100, destination_bytes, 1))
    if function(*arguments) != 0:
        number = ctypes.get_errno() or errno.EIO
        raise OSError(number, os.strerror(number), str(source), None, str(destination))
