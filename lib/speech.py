#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Speak text out loud through audio.cpp.

The installer builds `audiocpp_cli` and downloads the PocketTTS voice, then
records where both live. Nothing used to call them, which is why Gathm was
silent even with a working runtime. This module is that call site.

Standard library only, so it works inside the Termux install where pip
packages with native code cannot be built.

Configuration, in precedence order:
    GATHM_AUDIOCPP_BIN / _MODEL / _FAMILY / _VOICE   (environment)
    ~/.gathm/audiocpp_path / audiocpp_model / audiocpp_family / audiocpp_voice
    audiocpp_cli on PATH

Knobs:
    GATHM_SPEAK=0            disable speech entirely
    GATHM_SPEAK_MAX_CHARS    how much of a long reply to read (default 600)
    GATHM_SPEAK_TIMEOUT      seconds allowed for synthesis (default 180)
    GATHM_AUDIO_PLAYER       force a specific playback command

Usable directly, which is the quickest way to check a device end to end:
    python3 lib/speech.py "hello from gathm"
    python3 lib/speech.py --check
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~")) / ".gathm"

# Playback commands, best first. Termux has no default audio player, so several
# of these are worth trying before giving up; each entry is (binary, args...)
# where {f} is replaced by the wav path.
_PLAYERS = [
    ("termux-media-player", "play", "{f}"),   # termux-api (needs the Termux:API app)
    ("mpv", "--really-quiet", "--no-video", "{f}"),
    ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{f}"),
    ("play", "-q", "{f}"),                    # sox
    ("paplay", "{f}"),                        # pulseaudio
    ("aplay", "-q", "{f}"),                   # alsa
    ("afplay", "{f}"),                        # macOS
]

# One utterance at a time, and a new one cancels whatever is still talking.
# Without this, asking a second question while the first answer is being read
# out leaves two voices overlapping — synthesis of a long reply on a phone
# easily outlives the turn that produced it.
_speak_lock = threading.Lock()
_proc_lock = threading.Lock()
_procs: list = []          # live audiocpp_cli / player processes
_generation = 0            # bumped by stop(); stale workers notice and bail


def _read_config_file(name: str) -> str:
    try:
        return (CONFIG_DIR / name).read_text().strip()
    except Exception:
        return ""


def resolve() -> dict:
    """Return {bin, model, family, voice}; bin/model empty when unavailable."""
    binary = os.environ.get("GATHM_AUDIOCPP_BIN") or _read_config_file("audiocpp_path")
    if not binary or not os.path.exists(binary):
        binary = shutil.which("audiocpp_cli") or ""
    return {
        "bin": binary,
        "model": os.environ.get("GATHM_AUDIOCPP_MODEL") or _read_config_file("audiocpp_model"),
        "family": os.environ.get("GATHM_AUDIOCPP_FAMILY") or _read_config_file("audiocpp_family") or "pocket_tts",
        "voice": os.environ.get("GATHM_AUDIOCPP_VOICE") or _read_config_file("audiocpp_voice") or "alba",
    }


def enabled() -> bool:
    """False when speech is switched off or the runtime/voice is not installed."""
    if os.environ.get("GATHM_SPEAK", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    cfg = resolve()
    return bool(cfg["bin"]) and bool(cfg["model"])


def find_player() -> list | None:
    """Return the playback argv template, or None when nothing can play audio."""
    forced = os.environ.get("GATHM_AUDIO_PLAYER")
    if forced:
        parts = forced.split()
        if shutil.which(parts[0]):
            return parts + (["{f}"] if "{f}" not in forced else [])
        return None
    for entry in _PLAYERS:
        if shutil.which(entry[0]):
            return list(entry)
    return None


def speakable(text: str) -> str:
    """Reduce a markdown reply to prose worth reading aloud.

    Code blocks, URLs and table pipes are noise in speech, and a long answer
    would take minutes on a phone, so the text is trimmed at a sentence
    boundary near the limit.
    """
    t = text or ""
    t = re.sub(r"```.*?```", " code block omitted. ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)      # links/images -> label
    t = re.sub(r"https?://\S+", " a link ", t)
    t = re.sub(r"^\s*[#>*\-+|]+\s*", "", t, flags=re.M)   # md markers
    t = re.sub(r"[*_~|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    try:
        limit = int(os.environ.get("GATHM_SPEAK_MAX_CHARS", "600"))
    except ValueError:
        limit = 600
    if limit > 0 and len(t) > limit:
        cut = t[:limit]
        stop_at = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = (cut[: stop_at + 1] if stop_at > limit // 3 else cut).strip()
    return t


def _kill_tree(proc, grace: float = 0.5) -> None:
    """Stop a child and anything it started, then reap it.

    The whole process group is signalled, not just the child: audiocpp_cli may
    be reached through a small wrapper script (the installer falls back to one
    when the copied binary cannot find its build tree), and killing only the
    wrapper leaves the real synthesiser running — holding the output pipes open
    so the caller blocks anyway.
    """
    if proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), sig)
            else:  # pragma: no cover - Windows
                proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except Exception:  # noqa: BLE001 - already gone, or no permission
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace)
            return
        except Exception:  # noqa: BLE001 - still alive: escalate to SIGKILL
            continue


def _run_tracked(argv: list, timeout: int) -> tuple[int, str, str]:
    """Run a child process that stop() is able to kill mid-flight."""
    kwargs = {}
    if hasattr(os, "setsid"):
        # Own process group, so _kill_tree can take the whole thing down.
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, **kwargs)
    with _proc_lock:
        _procs.append(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            raise
        return proc.returncode, out or "", err or ""
    finally:
        with _proc_lock:
            if proc in _procs:
                _procs.remove(proc)


def stop() -> None:
    """Cancel any in-flight synthesis or playback and invalidate pending work."""
    global _generation
    with _proc_lock:
        _generation += 1
        procs, _procs[:] = list(_procs), []
    for proc in procs:
        try:
            _kill_tree(proc)
        except Exception:  # noqa: BLE001 - speech never breaks the caller
            pass


def is_speaking() -> bool:
    with _proc_lock:
        return any(p.poll() is None for p in _procs)


def synthesize(text: str, out_path: str) -> tuple[bool, str]:
    """Render text to a wav. Returns (ok, message)."""
    cfg = resolve()
    if not cfg["bin"]:
        return False, "audiocpp_cli not found (run ./install on Termux)"
    if not cfg["model"]:
        return False, "no voice model configured (~/.gathm/audiocpp_model)"

    try:
        timeout = int(os.environ.get("GATHM_SPEAK_TIMEOUT", "180"))
    except ValueError:
        timeout = 180

    cmd = [
        cfg["bin"], "--task", "tts",
        "--family", cfg["family"],
        "--model", cfg["model"],
        "--backend", "cpu",
        "--voice-id", cfg["voice"],
        "--text", text,
        "--out", out_path,
    ]
    try:
        rc, out, err = _run_tracked(cmd, timeout)
    except subprocess.TimeoutExpired:
        return False, f"synthesis timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    if rc != 0:
        tail = (err or out or "").strip().splitlines()
        return False, tail[-1] if tail else f"audiocpp_cli exited {rc}"
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return False, "audiocpp_cli produced no audio"
    return True, out_path


def synthesize_bytes(text: str) -> tuple[bool, bytes | str]:
    """Render text and return the wav bytes — used by the API/GUI path.

    The browser does the playing there, so no local player is required.
    """
    body = speakable(text)
    if not body:
        return False, "nothing to say"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="gathm-tts-", suffix=".wav")
        os.close(fd)
        ok, msg = synthesize(body, tmp)
        if not ok:
            return False, msg
        with open(tmp, "rb") as fh:
            return True, fh.read()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def play(path: str) -> tuple[bool, str]:
    """Play a wav with whatever player exists. Returns (ok, message)."""
    player = find_player()
    if not player:
        return False, ("no audio player found — install one with: pkg install mpv"
                       "  (or pkg install termux-api and the Termux:API app)")
    argv = [a.replace("{f}", path) for a in player]
    try:
        rc, _out, err = _run_tracked(argv, 300)
    except Exception as exc:  # noqa: BLE001
        return False, f"{argv[0]}: {exc}"
    if rc != 0:
        tail = (err or "").strip().splitlines()
        return False, f"{argv[0]}: {tail[-1] if tail else rc}"
    return True, argv[0]


def speak(text: str, quiet: bool = True) -> bool:
    """Say `text` out loud, blocking until it finishes.

    Best effort: never raises, returns whether the phrase was actually played.
    """
    if not enabled():
        return False
    body = speakable(text)
    if not body:
        return False

    with _proc_lock:
        my_gen = _generation

    with _speak_lock:
        with _proc_lock:
            if my_gen != _generation:      # cancelled while we queued
                return False
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix="gathm-speak-", suffix=".wav")
            os.close(fd)
            ok, msg = synthesize(body, tmp)
            if not ok:
                with _proc_lock:
                    cancelled = my_gen != _generation
                if not quiet and not cancelled:
                    print(f"[speech] {msg}", file=sys.stderr)
                return False
            with _proc_lock:
                if my_gen != _generation:  # a newer reply arrived meanwhile
                    return False
            ok, msg = play(tmp)
            if not ok and not quiet:
                print(f"[speech] {msg}", file=sys.stderr)
            return ok
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                print(f"[speech] {exc}", file=sys.stderr)
            return False
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass


def speak_async(text: str, quiet: bool = True):
    """Start speaking without blocking the caller; cancels anything talking.

    Synthesis of a couple of sentences takes seconds on a phone, and the reply
    is already on screen by then — waiting for the audio before handing back
    the prompt would make Pilot feel frozen.
    """
    if not enabled():
        return None
    stop()
    thread = threading.Thread(target=speak, args=(text, quiet),
                              name="gathm-speech", daemon=True)
    thread.start()
    return thread


def diagnose() -> int:
    """Print why speech would or would not work. Returns a shell exit code."""
    cfg = resolve()
    player = find_player()
    print("audiocpp_cli : %s" % (cfg["bin"] or "NOT FOUND"))
    print("voice model  : %s" % (cfg["model"] or "NOT CONFIGURED"))
    print("family/voice : %s / %s" % (cfg["family"], cfg["voice"]))
    print("player       : %s" % (" ".join(player) if player else
                                 "NONE (pkg install mpv)"))
    print("GATHM_SPEAK  : %s" % os.environ.get("GATHM_SPEAK", "1"))
    print("enabled      : %s" % enabled())
    return 0 if (enabled() and player) else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("--check", "-c", "--diagnose"):
        sys.exit(diagnose())
    sys.exit(0 if speak(" ".join(args), quiet=False) else 1)
