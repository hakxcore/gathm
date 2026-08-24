#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Speak text out loud through audio.cpp.

The installer builds `audiocpp_cli` and downloads the PocketTTS voice, then
records where both live. Nothing used to call them, which is why Gathm was
silent even with a working runtime. This module is that call site.

Standard library only, so it works inside the Termux install where pip
packages with native code cannot be built.

The same binary transcribes (`--task asr`), so listening lives here too.

Configuration, in precedence order:
    GATHM_AUDIOCPP_BIN / _MODEL / _FAMILY / _VOICE   (environment)
    ~/.gathm/audiocpp_path / audiocpp_model / audiocpp_family / audiocpp_voice
    audiocpp_cli on PATH
and for transcription:
    GATHM_AUDIOCPP_ASR_MODEL / _ASR_FAMILY
    ~/.gathm/audiocpp_asr_model / audiocpp_asr_family

Knobs:
    GATHM_SPEAK=0            disable speech entirely
    GATHM_SPEAK_MAX_CHARS    how much of a long reply to read (default 600)
    GATHM_SPEAK_CHUNK_MIN    shortest utterance when streaming (default 40)
    GATHM_SPEAK_CHUNK_MAX    forced break for unpunctuated text (default 240)
    GATHM_SPEAK_TIMEOUT      seconds allowed for synthesis (default 180)
    GATHM_SPEAK_ENGINE       force audio.cpp or system (default: auto)
    GATHM_AUDIO_PLAYER       force a specific playback command
    GATHM_ASR_TIMEOUT        seconds allowed for one transcription (default 300)
    GATHM_LISTEN_SECONDS     default recording length (default 8)

Usable directly, which is the quickest way to check a device end to end:
    python3 lib/speech.py "hello from gathm"
    python3 lib/speech.py --check
    python3 lib/speech.py --transcribe clip.wav
    python3 lib/speech.py --listen 5
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
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


def _is_darwin() -> bool:
    return sys.platform == "darwin"


def _read_config_file(name: str) -> str:
    try:
        return (CONFIG_DIR / name).read_text().strip()
    except Exception:
        return ""


def _audiocpp_src(model_dir: str = "") -> str:
    """The audio.cpp checkout, or "" — needed as the working directory.

    audiocpp_cli resolves its model contract specs (model_specs/<family>.json)
    relative to where it is run from, not from where the binary lives. Run
    anywhere else and a family whose spec is not embedded in the GGUF fails with
    "model contract spec not found for family ...", which is exactly what
    sense_asr did on-device.
    """
    src = os.environ.get("GATHM_AUDIOCPP_SRC") or _read_config_file("audiocpp_src")
    if src and os.path.isdir(os.path.join(src, "model_specs")):
        return src
    if src and os.path.isdir(src):
        return src
    # Fall back to walking up from the weights: models/<Family>/<lang> sits
    # inside the checkout, so the tree with model_specs/ is a parent of it.
    path = model_dir
    for _ in range(5):
        if not path or path == os.path.dirname(path):
            break
        path = os.path.dirname(path)
        if os.path.isdir(os.path.join(path, "model_specs")):
            return path
    return ""


def resolve() -> dict:
    """Return {bin, model, family, voice, src}; bin/model empty when unavailable."""
    binary = os.environ.get("GATHM_AUDIOCPP_BIN") or _read_config_file("audiocpp_path")
    if not binary or not os.path.exists(binary):
        binary = shutil.which("audiocpp_cli") or ""
    model = os.environ.get("GATHM_AUDIOCPP_MODEL") or _read_config_file("audiocpp_model")
    return {
        "bin": binary,
        "model": model,
        "family": os.environ.get("GATHM_AUDIOCPP_FAMILY") or _read_config_file("audiocpp_family") or "pocket_tts",
        "voice": os.environ.get("GATHM_AUDIOCPP_VOICE") or _read_config_file("audiocpp_voice") or "alba",
        "src": _audiocpp_src(model),
    }


# Speech engines that need no build and no pip. audio.cpp exists because Android
# has nothing like these; macOS and most desktop Linux do, and using them means
# Gathm speaks on those platforms without a compiler or a 250 MB download.
# Each entry is (binary, args...) where {t} is replaced by the text.
_SYSTEM_VOICES = [
    ("say", "{t}"),                       # macOS, built in
    ("spd-say", "-w", "{t}"),             # speech-dispatcher (most Linux desktops)
    ("espeak-ng", "{t}"),
    ("espeak", "{t}"),
]


# ---------------------------------------------------------------------------
# Speaking a language the default voice does not have
# ---------------------------------------------------------------------------
# `say` was called with no -v, so it always used the system default voice — an
# English one. Handed Devanagari, an English voice reads nothing useful, which
# is why Gathm could write Hindi and not speak it. macOS ships voices for other
# languages (Lekha for Hindi, and others per locale); the right one just has to
# be asked for.
#
# The script the text is written in is the signal, because that is all we have:
# there is no language tag on a reply, and guessing from words would need a
# model. A script maps to the locales that use it, and the first installed
# voice for one of those locales wins.

_SCRIPT_RANGES = [
    ("devanagari", 0x0900, 0x097F),
    ("bengali",    0x0980, 0x09FF),
    ("gurmukhi",   0x0A00, 0x0A7F),
    ("gujarati",   0x0A80, 0x0AFF),
    ("oriya",      0x0B00, 0x0B7F),
    ("tamil",      0x0B80, 0x0BFF),
    ("telugu",     0x0C00, 0x0C7F),
    ("kannada",    0x0C80, 0x0CFF),
    ("malayalam",  0x0D00, 0x0D7F),
    ("sinhala",    0x0D80, 0x0DFF),
    ("thai",       0x0E00, 0x0E7F),
    ("arabic",     0x0600, 0x06FF),
    ("hebrew",     0x0590, 0x05FF),
    ("cyrillic",   0x0400, 0x04FF),
    ("greek",      0x0370, 0x03FF),
    ("hangul",     0xAC00, 0xD7AF),
    ("kana",       0x3040, 0x30FF),
    ("han",        0x4E00, 0x9FFF),
]

# Locale prefixes that write each script, best first. Hindi and Marathi both
# use Devanagari, so a Hindi voice is preferred and a Marathi one will do.
_SCRIPT_LOCALES = {
    "devanagari": ("hi", "mr", "ne", "sa"),
    "bengali":    ("bn",),
    "gurmukhi":   ("pa",),
    "gujarati":   ("gu",),
    "oriya":      ("or",),
    "tamil":      ("ta",),
    "telugu":     ("te",),
    "kannada":    ("kn",),
    "malayalam":  ("ml",),
    "sinhala":    ("si",),
    "thai":       ("th",),
    "arabic":     ("ar", "fa", "ur"),
    "hebrew":     ("he",),
    "cyrillic":   ("ru", "uk", "bg", "sr"),
    "greek":      ("el",),
    "hangul":     ("ko",),
    "kana":       ("ja",),
    "han":        ("zh",),
}


def script_of(text: str) -> str:
    """The script most of `text` is written in: "latin" when in doubt.

    Counted rather than sniffed from the first character, because a Hindi
    reply routinely contains Latin punctuation, digits and the odd English
    word, and one of those at the front should not decide the voice.
    """
    counts: dict = {}
    for ch in text or "":
        if not ch.isalpha():
            continue
        point = ord(ch)
        if point < 0x0370:
            counts["latin"] = counts.get("latin", 0) + 1
            continue
        for name, low, high in _SCRIPT_RANGES:
            if low <= point <= high:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return "latin"
    return max(counts.items(), key=lambda pair: (pair[1], pair[0] != "latin"))[0]


_VOICE_CACHE: dict = {}


def list_system_voices(refresh: bool = False) -> list:
    """[(name, locale)] from `say -v ?`, or [] where that is not available.

    Cached: shelling out per sentence would add a process to every chunk of a
    spoken reply, and the installed voices do not change mid-conversation.
    """
    if not refresh and "voices" in _VOICE_CACHE:
        return _VOICE_CACHE["voices"]

    # On disk as well as in memory: `say -v ?` enumerates ~180 voices, and an
    # in-process cache is paid again by every new process. Pilot is one
    # process, but the GUI starts a fresh chat_once.py per turn, so "once per
    # process" is closer to "every time" than it sounds.
    cache_file = CONFIG_DIR / "say_voices.tsv"
    if not refresh:
        try:
            if cache_file.exists():
                cached = [tuple(line.split("\t"))
                          for line in cache_file.read_text().splitlines()
                          if "\t" in line]
                if cached:
                    _VOICE_CACHE["voices"] = cached
                    return cached
        except Exception:  # noqa: BLE001 - a bad cache is not an error
            pass

    voices: list = []
    if shutil.which("say"):
        try:
            proc = subprocess.run(["say", "-v", "?"], capture_output=True,
                                  text=True, timeout=10)
            for line in (proc.stdout or "").splitlines():
                # Real `say -v ?` lines, which are messier than they look:
                #
                #   Lekha               hi_IN    # नमस्ते, मेरा नाम लेखा है।
                #   Majed               ar_001   # مرحبًا! اسمي ماجد.
                #   Eddy (German (Germany)) de_DE    # Hallo! Ich heiße Eddy.
                #
                # So the region is not always two letters (ar_001), and the gap
                # before the locale is not always two spaces — a long name
                # leaves only one. Anchoring on the `#` that starts the sample
                # is what makes both parse; requiring two spaces and [A-Z]{2}
                # silently dropped every Arabic voice and every long-named one.
                match = re.match(
                    r"^(.+?)\s+([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,4})?)\s+#",
                    line)
                if match:
                    voices.append((match.group(1).strip(), match.group(2)))
        except Exception:  # noqa: BLE001 - no voice list is not an error
            voices = []
    _VOICE_CACHE["voices"] = voices
    if voices:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                "\n".join(f"{name}\t{locale}" for name, locale in voices))
        except Exception:  # noqa: BLE001 - failing to cache is not failing
            pass
    return voices


def voice_for_text(text: str) -> str:
    """An installed `say` voice whose language matches the script, or "".

    Empty for Latin text, so English keeps whatever voice the user chose in
    System Settings rather than being overridden by our guess.
    """
    script = script_of(text)
    if script == "latin":
        return ""
    wanted = _SCRIPT_LOCALES.get(script)
    if not wanted:
        return ""
    voices = list_system_voices()
    for prefix in wanted:
        matches = [name for name, locale in voices
                   if locale.lower().startswith((prefix + "_", prefix + "-"))]
        if not matches:
            continue
        # macOS names its character voices per language as
        # "Eddy (Japanese (Japan))", "Grandma (Chinese (Taiwan))" and so on.
        # They sort early, so a plain alphabetical pick handed Japanese to a
        # novelty voice instead of Kyoko. A name with no bracket is a real
        # voice for that language; prefer one, and fall back to whatever
        # exists if brackets are all there is.
        plain = [name for name in matches if "(" not in name]
        return (plain or matches)[0]
    return ""


def _needs_system_voice(text: str) -> bool:
    """Whether this text needs the OS voice because audio.cpp cannot say it.

    The bundled PocketTTS voice is English. Devanagari, kana, Arabic and the
    rest are not something it renders badly — it renders nothing useful at all.
    True only when the OS actually has a voice for that script, so Termux
    (where there is no OS voice) still uses audio.cpp rather than failing.
    """
    if script_of(text) == "latin":
        return False
    return bool(voice_for_text(text))


def _with_voice(argv: list, text: str) -> list:
    """Insert `-v <voice>` into a `say` command when the script needs one."""
    if not argv or os.path.basename(argv[0]) != "say":
        return argv
    if "-v" in argv:                  # the user picked one; leave it alone
        return argv
    name = voice_for_text(text)
    if not name:
        return argv
    return [argv[0], "-v", name] + argv[1:]


def find_system_voice() -> list | None:
    """The OS's own text-to-speech command, or None."""
    forced = os.environ.get("GATHM_SPEAK_COMMAND")
    if forced:
        parts = forced.split()
        if shutil.which(parts[0]):
            return parts + (["{t}"] if "{t}" not in forced else [])
        return None
    for entry in _SYSTEM_VOICES:
        if shutil.which(entry[0]):
            return list(entry)
    return None


# Not every system voice can write a file, and the API needs bytes to hand the
# browser. `say` and espeak can; spd-say cannot, so it stays playback-only.
_SYSTEM_VOICE_FILE_ARGS = {
    # LEI16 PCM in a WAV container is what the browser and the ASR models read.
    "say": ["-o", "{f}", "--data-format=LEI16@22050", "{t}"],
    "espeak-ng": ["-w", "{f}", "{t}"],
    "espeak": ["-w", "{f}", "{t}"],
}


def system_voice_to_file() -> str:
    """Name of a system voice that can render to a WAV file, or ""."""
    voice = find_system_voice()
    if not voice:
        return ""
    name = voice[0]
    return name if name in _SYSTEM_VOICE_FILE_ARGS else ""


def can_synthesize_file() -> bool:
    """Whether speech can be produced as bytes (what the GUI needs)."""
    cfg = resolve()
    if cfg["bin"] and cfg["model"]:
        return True
    return bool(system_voice_to_file())


def engine() -> str:
    """Which engine would speak: "audio.cpp", "system", or "" for none.

    audio.cpp is the on-device path Gathm builds for Termux, where nothing else
    works. On macOS the OS voice wins even when audio.cpp IS installed: `say`
    is a resident system service with no model to load, no wav to write and no
    player to spawn, so it starts talking in milliseconds where PocketTTS takes
    a second or more. audio.cpp is still installed on a Mac — for listening,
    which is the direction the OS gives no command line for.

    GATHM_SPEAK_ENGINE=audio.cpp|system overrides the choice, falling back to
    whatever is actually available.
    """
    cfg = resolve()
    have_cpp = bool(cfg["bin"] and cfg["model"])
    have_system = bool(find_system_voice())

    forced = os.environ.get("GATHM_SPEAK_ENGINE", "").strip().lower()
    if forced in ("audio.cpp", "audiocpp"):
        return "audio.cpp" if have_cpp else ("system" if have_system else "")
    if forced == "system":
        return "system" if have_system else ("audio.cpp" if have_cpp else "")

    if _is_darwin() and have_system:
        return "system"
    if have_cpp:
        return "audio.cpp"
    return "system" if have_system else ""


def speech_disabled() -> bool:
    return os.environ.get("GATHM_SPEAK", "1").strip().lower() in ("0", "off", "false", "no")


def enabled() -> bool:
    """False when speech is switched off, or no engine can speak."""
    if speech_disabled():
        return False
    return bool(engine())


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


# A model that is asked for code does not always fence it. gemma and llama at
# small sizes routinely emit a whole C++ program as plain text, and then
# _clean_for_speech read it out — including stripping the `#` off `#include`,
# so it said "include iostream, using namespace std, void generateTable int
# rows int cols". Nobody wants a program read aloud.
#
# Detecting code by line rather than by fence: a line that ends in a semicolon
# or a brace, opens a block, declares something, or is a comment. Any single
# one of these can appear in prose ("the file is main.cpp;"), so it takes a RUN
# of them to count — three consecutive lines is a program, not a sentence.
_CODE_LINE_RE = re.compile(r"""
      ^\s*\#\s*(include|define|pragma|ifndef|endif|import)\b
    | ^\s*(using|namespace|template|typedef|struct|enum|impl|trait)\b
    | ^\s*(public|private|protected|static|final|async)\s+\S
    | ^\s*(def|class|import|from|package|func|fn|var|let|const)\s+\S
    | ^\s*(if|for|while|switch|catch|elif|else\s+if)\s*\(
    | ^\s*(return|throw|break|continue)\b[^.!?]*;\s*(//[^\n]*)?$
    | ^\s*[{}\[\]()]+\s*;?\s*$
    | ;\s*(//[^\n]*)?$
    | \{\s*(//[^\n]*)?$
    | ^\s*(//|/\*|\*/|\*\s)
    | ^\s*<\/?[a-zA-Z][^>]*>\s*$
    | \w+\s*\([^)]*\)\s*(const\s*)?\{\s*$
    | ^\s{2,}\S+.*[;{}]\s*(//[^\n]*)?$
""", re.VERBOSE)

_MIN_CODE_LINES = 3


def _strip_unfenced_code(text: str) -> str:
    """Replace runs of code-looking lines with a note, fences or not."""
    lines = (text or "").split("\n")
    out: list = []
    index = 0
    total = len(lines)
    while index < total:
        if not _CODE_LINE_RE.search(lines[index]):
            out.append(lines[index])
            index += 1
            continue
        # A candidate run. Blank lines inside it belong to the code — a
        # program has paragraphs too — but do not count towards the run.
        scan = index
        counted = 0
        last_code = index
        while scan < total:
            if _CODE_LINE_RE.search(lines[scan]):
                counted += 1
                last_code = scan
                scan += 1
            elif not lines[scan].strip():
                scan += 1
            else:
                break
        if counted >= _MIN_CODE_LINES:
            out.append(" code block omitted. ")
        else:
            out.extend(lines[index:last_code + 1])
        index = last_code + 1
    return "\n".join(out)


# Guessing at speech latency wastes everyone's time: the text work measures in
# microseconds, so anything slow is a process being started. GATHM_SPEECH_TIMING=1
# prints how long each stage took, to stderr, and costs nothing when unset.
def _timing_on() -> bool:
    return (os.environ.get("GATHM_SPEECH_TIMING") or "").strip() in ("1", "true", "yes")


def _timed(label: str, fn):
    """Run fn(), and report how long it took when timing is switched on."""
    if not _timing_on():
        return fn()
    start = time.monotonic()
    try:
        return fn()
    finally:
        print("[speech] %-22s %6.1f ms" % (label, (time.monotonic() - start) * 1000),
              file=sys.stderr)


def _clean_for_speech(text: str) -> str:
    """Strip markdown down to prose. No length limit — see speakable()."""
    t = text or ""
    t = re.sub(r"```.*?```", " code block omitted. ", t, flags=re.S)
    # Before the markdown markers are stripped, or `#include` becomes the word
    # "include" and the line stops looking like code at all.
    t = _strip_unfenced_code(t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)      # links/images -> label
    t = re.sub(r"https?://\S+", " a link ", t)
    t = re.sub(r"^\s*[#>*\-+|]+\s*", "", t, flags=re.M)   # md markers
    t = re.sub(r"[*_~|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # "code block omitted. code block omitted." helps nobody.
    return re.sub(r"(code block omitted\.\s*){2,}", "code block omitted. ", t).strip()


def speakable(text: str) -> str:
    """Reduce a markdown reply to prose worth reading aloud.

    Code blocks, URLs and table pipes are noise in speech, and a long answer
    would take minutes on a phone, so the text is trimmed at a sentence
    boundary near the limit.
    """
    t = _clean_for_speech(text)

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


def _run_tracked(argv: list, timeout: int, cwd: str | None = None) -> tuple[int, str, str]:
    """Run a child process that stop() is able to kill mid-flight."""
    kwargs = {}
    if hasattr(os, "setsid"):
        # Own process group, so _kill_tree can take the whole thing down.
        kwargs["start_new_session"] = True
    if cwd and os.path.isdir(cwd):
        kwargs["cwd"] = cwd
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
        rc, out, err = _run_tracked(cmd, timeout, cwd=cfg["src"])
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


def synthesize_system(text: str, out_path: str) -> tuple[bool, str]:
    """Render text to a wav with the OS voice. Returns (ok, message)."""
    name = system_voice_to_file()
    if not name:
        return False, ("this system's speech command cannot write a file "
                       "(set GATHM_SPEAK_COMMAND to `say` or `espeak-ng`)")
    argv = [name] + [
        text if a == "{t}" else a.replace("{f}", out_path)
        for a in _SYSTEM_VOICE_FILE_ARGS[name]
    ]
    # The GUI speaks through this path, so it needs the same voice choice the
    # terminal gets — otherwise Hindi is silent in the browser and audible in
    # the TUI.
    argv = _with_voice(argv, text)
    try:
        rc, out, err = _run_tracked(argv, 180)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if rc != 0:
        tail = (err or out or "").strip().splitlines()
        return False, f"{name}: {tail[-1] if tail else rc}"
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return False, f"{name} produced no audio"
    return True, out_path


def synthesize_bytes(text: str) -> tuple[bool, bytes | str]:
    """Render text and return the wav bytes — used by the API/GUI path.

    The browser does the playing there, so no local player is required.

    Which engine speaks is engine()'s decision, not this function's. It used to
    pick audio.cpp whenever audio.cpp existed, which on a Mac it always does —
    it is installed there for LISTENING. So the GUI spoke through PocketTTS
    while the TUI spoke through `say`, and since PocketTTS here is an
    English-only voice, a Hindi reply was audible in the terminal and silent in
    the browser.

    A script the model cannot say overrides even that. An English-only voice
    handed Devanagari is not a choice between two engines; it is one engine and
    one failure, so a matching OS voice wins when there is one.
    """
    body = speakable(text)
    if not body:
        return False, "nothing to say"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="gathm-tts-", suffix=".wav")
        os.close(fd)
        cfg = resolve()
        have_cpp = bool(cfg["bin"] and cfg["model"])
        have_system = bool(system_voice_to_file())

        use_cpp = have_cpp
        if have_system and engine() == "system":
            use_cpp = False                      # the same choice the TUI makes
        if use_cpp and have_system and _needs_system_voice(body):
            use_cpp = False                      # a script audio.cpp cannot say

        ok, msg = (synthesize(body, tmp) if use_cpp
                   else synthesize_system(body, tmp))
        # One retry on the other engine rather than a silent browser: whichever
        # was skipped is still installed, and a wrong-sounding voice beats none.
        if not ok and have_cpp and have_system:
            ok, msg = (synthesize_system(body, tmp) if use_cpp
                       else synthesize(body, tmp))
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


def _speak_system(text: str, quiet: bool) -> bool:
    """Speak through the OS's own voice — no synthesis file, no player."""
    voice = find_system_voice()
    if not voice:
        return False
    argv = [text if a == "{t}" else a.replace("{t}", text) for a in voice]
    argv = _timed("pick voice", lambda: _with_voice(argv, text))
    try:
        rc, _out, err = _timed("say", lambda: _run_tracked(argv, 300))
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"[speech] {argv[0]}: {exc}", file=sys.stderr)
        return False
    if rc != 0:
        tail = (err or "").strip().splitlines()
        if not quiet:
            print(f"[speech] {argv[0]}: {tail[-1] if tail else rc}", file=sys.stderr)
        return False
    return True


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

    if engine() == "system":
        # Serialised the same way, so a new question still cuts off the old
        # answer, but there is no wav and no separate player to arrange.
        with _speak_lock:
            with _proc_lock:
                if my_gen != _generation:
                    return False
            return _speak_system(body, quiet)

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


# ---------------------------------------------------------------------------
# Streaming speech
#
# speak() renders the whole reply before a single word is heard. With audio.cpp
# that is the slow part — a model load plus synthesis of every sentence — so a
# four-sentence answer stays silent for seconds and then plays all at once.
#
# SpeechStream cuts the text into utterances and pipelines them: sentence two
# is being synthesised while sentence one is playing, so the reply starts
# talking as soon as its first sentence exists. It takes text incrementally,
# which is what a token-streaming model will feed it.
# ---------------------------------------------------------------------------

# A sentence ends at .!?… possibly followed by a closing quote or bracket, and
# then whitespace or the end of the buffer. Requiring the trailing space is what
# keeps "3.14" and "gathm.sh" from being split down the middle.
_SENTENCE_END_RE = re.compile(r"[.!?…]['\")\]]*(?=\s|$)")


def _speak_env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def split_speech_chunks(buf: str, final: bool = False, min_chars: int = 40,
                        max_chars: int = 240,
                        first_min: int | None = None) -> tuple[list, str]:
    """Cut a growing reply into utterances. Returns (chunks, remainder).

    `buf` is raw model output; the chunks come back cleaned for speech. The
    remainder is the raw tail that is not ready to speak yet — feed it back in
    with more text appended.

    Only complete sentences are emitted, and only once they reach `min_chars`,
    so a reply does not come out as a stutter of two-word utterances. The very
    first chunk may be shorter (`first_min`), because getting *some* audio out
    quickly is the whole point. `max_chars` forces a break in text that never
    punctuates, and `final` flushes whatever is left.
    """
    if first_min is None:
        first_min = min_chars

    # An unclosed code fence is held back: speakable() turns a whole fence into
    # "code block omitted", and it can only see that once the fence closes.
    limit = len(buf)
    if not final and buf.count("```") % 2 == 1:
        limit = buf.rindex("```")

    chunks: list = []
    pos = 0
    while pos < limit:
        need = first_min if not chunks else min_chars
        cut = -1
        for m in _SENTENCE_END_RE.finditer(buf, pos, limit):
            if m.end() - pos >= need:
                cut = m.end()
                break
        if cut < 0:
            break
        piece = _clean_for_speech(buf[pos:cut])
        if piece:
            chunks.append(piece)
        pos = cut

    # Nothing punctuates — a list, a wall of prose — so break on a word boundary
    # rather than let the buffer grow without bound.
    while max_chars and not final and (limit - pos) > max_chars:
        window = buf[pos:pos + max_chars]
        space = window.rfind(" ")
        cut = pos + (space + 1 if space > max_chars // 3 else max_chars)
        piece = _clean_for_speech(buf[pos:cut])
        if piece:
            chunks.append(piece)
        pos = cut

    if final and pos < len(buf):
        piece = _clean_for_speech(buf[pos:])
        if piece:
            chunks.append(piece)
        pos = len(buf)

    return chunks, buf[pos:]


class SpeechStream:
    """Speak text as it arrives, one utterance at a time.

    Feed it text (all at once, or token by token), then close() it. Playback
    happens on background threads, so no method here blocks the caller except
    wait(). stop() cancels it like any other speech.
    """

    def __init__(self, quiet: bool = True):
        self.quiet = quiet
        self._buf = ""
        self._spoken = 0                 # characters handed to the engine
        self._emitted = 0                # utterances handed to the engine
        self._closed = False
        self._lock = threading.Lock()
        self._budget = _speak_env_int("GATHM_SPEAK_MAX_CHARS", 600)
        self._min = _speak_env_int("GATHM_SPEAK_CHUNK_MIN", 40)
        self._max = _speak_env_int("GATHM_SPEAK_CHUNK_MAX", 240)
        self._text_q: queue.Queue = queue.Queue()
        # Bounded: synthesise a little ahead, not the entire reply. Otherwise a
        # cancelled answer has already spent the phone's battery on audio
        # nobody will hear.
        self._wav_q: queue.Queue = queue.Queue(maxsize=2)
        self._threads: list = []
        with _proc_lock:
            self._generation = _generation

    # -- lifecycle ----------------------------------------------------------
    def _cancelled(self) -> bool:
        with _proc_lock:
            return self._generation != _generation

    def start(self) -> "SpeechStream":
        if self._threads:
            return self
        if engine() == "system":
            # The OS voice speaks straight to the sound card: one worker, no
            # intermediate wav and no player to arrange.
            targets = [("gathm-speech-say", self._system_worker)]
        else:
            targets = [("gathm-speech-synth", self._synth_worker),
                       ("gathm-speech-play", self._play_worker)]
        for name, target in targets:
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self._threads.append(th)
        return self

    def feed(self, text: str) -> "SpeechStream":
        """Add text. Complete sentences in it start speaking immediately."""
        if not text or self._closed:
            return self
        with self._lock:
            self._buf += text
            self._drain(final=False)
        return self

    def close(self) -> "SpeechStream":
        """No more text is coming: flush the tail and let the workers finish."""
        with self._lock:
            if self._closed:
                return self
            self._closed = True
            self._drain(final=True)
        self._text_q.put(None)
        return self

    def cancel(self) -> None:
        """Stop talking now and discard anything queued."""
        self._closed = True
        stop()                      # bumps the generation the workers watch

    def wait(self, timeout: float | None = None) -> bool:
        """Block until everything queued has been spoken (or cancelled)."""
        for th in self._threads:
            th.join(timeout)
        return not any(th.is_alive() for th in self._threads)

    join = wait                     # speak_async() used to hand back a Thread

    # -- internals ----------------------------------------------------------
    def _drain(self, final: bool) -> None:
        """Move whatever is speakable from the buffer onto the queue."""
        if self._budget and self._spoken >= self._budget:
            self._buf = ""
            return
        chunks, self._buf = split_speech_chunks(
            self._buf, final=final, min_chars=self._min, max_chars=self._max,
            # Nothing said yet: take the first sentence however short it is.
            first_min=1 if not self._emitted else self._min,
        )
        for chunk in chunks:
            if self._budget and self._spoken >= self._budget:
                break
            self._spoken += len(chunk)
            self._emitted += 1
            self._text_q.put(chunk)

    def _next_text(self):
        """Next utterance, or None when the stream is finished/cancelled."""
        while True:
            try:
                item = self._text_q.get(timeout=0.2)
            except queue.Empty:
                if self._cancelled():
                    return None
                continue
            if item is None or self._cancelled():
                return None
            return item

    def _system_worker(self) -> None:
        while True:
            text = self._next_text()
            if text is None:
                return
            with _speak_lock:
                if self._cancelled():
                    return
                _speak_system(text, self.quiet)

    def _synth_worker(self) -> None:
        while True:
            text = self._next_text()
            if text is None:
                break
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(prefix="gathm-speak-", suffix=".wav")
                os.close(fd)
                ok, msg = synthesize(text, tmp)
                if not ok:
                    if not self.quiet and not self._cancelled():
                        print(f"[speech] {msg}", file=sys.stderr)
                    _unlink(tmp)
                    continue
            except Exception as exc:  # noqa: BLE001 - speech never breaks a reply
                if not self.quiet:
                    print(f"[speech] {exc}", file=sys.stderr)
                _unlink(tmp)
                continue
            # put() blocks while the player is behind, which is the throttle.
            while True:
                if self._cancelled():
                    _unlink(tmp)
                    break
                try:
                    self._wav_q.put(tmp, timeout=0.2)
                    break
                except queue.Full:
                    continue
        self._wav_q.put(None)

    def _play_worker(self) -> None:
        while True:
            try:
                path = self._wav_q.get(timeout=0.2)
            except queue.Empty:
                if self._cancelled():
                    return
                continue
            if path is None:
                return
            try:
                if self._cancelled():
                    return
                with _speak_lock:
                    if self._cancelled():
                        return
                    ok, msg = play(path)
                if not ok and not self.quiet and not self._cancelled():
                    print(f"[speech] {msg}", file=sys.stderr)
            finally:
                _unlink(path)


def _unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except Exception:  # noqa: BLE001
        pass


def speak_async(text: str, quiet: bool = True):
    """Start speaking without blocking the caller; cancels anything talking.

    Sentence-by-sentence, so the first words are heard while the rest is still
    being synthesised — on a phone that is the difference between a reply that
    starts talking and one that sits silent for several seconds.
    """
    if not enabled():
        return None
    stop()
    return SpeechStream(quiet=quiet).start().feed(text).close()


# ---------------------------------------------------------------------------
# Listening: the same binary, --task asr
# ---------------------------------------------------------------------------

# SenseVoice-Small: ~250 MB at q8_0, 23 languages, and it runs offline on a
# phone. The Qwen3 ASR models are 3-7x larger for no benefit Gathm can use.
ASR_DEFAULT_FAMILY = "sense_asr"

# Recording commands. Android has no ALSA, so termux-microphone-record (from
# the termux-api package plus the Termux:API app) is the only mic route there;
# the rest are for desktops, where they cost nothing to try.
_RECORDERS = ["termux-microphone-record", "arecord", "rec", "ffmpeg"]

# SenseVoice prefixes its transcript with language/emotion/event tags such as
# "<|en|><|NEUTRAL|><|Speech|><|withitn|>". They are metadata, not words.
_TAG_RE = re.compile(r"<\|[^|]*\|>")


def resolve_asr() -> dict:
    """Return {bin, model, family, src} for transcription; model empty if absent."""
    base = resolve()
    model = (os.environ.get("GATHM_AUDIOCPP_ASR_MODEL")
             or _read_config_file("audiocpp_asr_model"))
    return {
        "bin": base["bin"],
        "model": model,
        "family": (os.environ.get("GATHM_AUDIOCPP_ASR_FAMILY")
                   or _read_config_file("audiocpp_asr_family")
                   or ASR_DEFAULT_FAMILY),
        "src": base["src"] or _audiocpp_src(model),
    }


# ---------------------------------------------------------------------------
# Android's own recogniser
# ---------------------------------------------------------------------------
# The reason Gboard's mic feels instant and Gathm's did not is architecture,
# not model quality. Gboard runs a STREAMING recogniser resident in a system
# service, on the NPU. Gathm recorded a whole utterance, waited for silence,
# wrote a WAV, then started a process and loaded a 250 MB model — so the
# latency was the length of the sentence plus a cold start, every time.
#
# On Android there is no need to compete with that: termux-speech-to-text
# (termux-api, the same package already used for recording) hands the job to
# Android's SpeechRecognizer, which IS the service behind the Gboard mic. It
# does its own capture and its own endpointing, works offline once the language
# pack is downloaded, and covers the languages the phone covers — including
# Hindi, which SenseVoice does not.
#
# It cannot transcribe a file, only the microphone in front of it, so the GUI's
# upload path still goes through audio.cpp. This is for `listen()`.

_ANDROID_ASR = "termux-speech-to-text"


def android_asr_available() -> bool:
    """Whether Android's recogniser can be used for listening."""
    if not _is_termux():
        return False
    return bool(shutil.which(_ANDROID_ASR))


def asr_engine() -> str:
    """Which engine `listen()` would use: "android", "audio.cpp", or "".

    GATHM_ASR_ENGINE=android|audio.cpp overrides, falling back to whatever is
    actually present — a preference for something uninstalled should not mean
    silence.
    """
    have_android = android_asr_available()
    have_cpp = _audiocpp_asr_enabled()

    forced = (os.environ.get("GATHM_ASR_ENGINE") or "").strip().lower()
    if forced in ("android", "termux") and have_android:
        return "android"
    if forced in ("audio.cpp", "audiocpp", "sense", "sensevoice") and have_cpp:
        return "audio.cpp"

    # Android first where it exists: it is faster, it needs no model, and it
    # speaks whatever the phone speaks.
    if have_android:
        return "android"
    return "audio.cpp" if have_cpp else ""


def listen_android(timeout: int = 60) -> tuple:
    """Listen through Android's recogniser. Returns (ok, text).

    No recording step, no WAV, no conversion, no model: the command captures
    and transcribes, and prints the text.
    """
    stop()  # never listen to ourselves talking
    try:
        rc, out, err = _run_tracked([_ANDROID_ASR], timeout)
    except subprocess.TimeoutExpired:
        return False, f"speech recognition timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    text = (out or "").strip()
    if rc != 0 and not text:
        # Whatever it said, plus what to do about it. The bare stderr line is
        # usually something like "no such method", which tells nobody anything;
        # the two things that actually go wrong here are the missing companion
        # app and a denied microphone.
        tail = (err or "").strip().splitlines()
        detail = tail[-1] if tail else f"exited {rc}"
        return False, (f"{_ANDROID_ASR}: {detail} — check that the Termux:API "
                       "app is installed (F-Droid, separate from the "
                       "termux-api package) and has microphone permission")
    if not text:
        return False, "nothing was recognised"
    return True, clean_transcript(text)


def _audiocpp_asr_enabled() -> bool:
    """Whether audio.cpp can transcribe (runtime + ASR weights present)."""
    cfg = resolve_asr()
    return bool(cfg["bin"]) and bool(cfg["model"]) and os.path.isdir(cfg["model"])


def asr_enabled() -> bool:
    """Whether listening can run at all, by any engine.

    This used to ask only about audio.cpp, so a Termux phone with Android's
    recogniser installed but no SenseVoice weights reported "no voice input"
    while the microphone worked perfectly.
    """
    return bool(asr_engine())


def _is_termux() -> bool:
    """Termux, by its two definitive signals.

    Deliberately not "is termux-setup-storage on PATH": a stray script of that
    name anywhere on PATH would make a desktop claim to be Android and hand the
    user Termux-only advice.
    """
    if "com.termux" in (os.environ.get("PREFIX") or ""):
        return True
    return os.path.isdir("/data/data/com.termux/files/usr")


def asr_unavailable_reason() -> str:
    """Why transcription cannot run, phrased for this platform. "" if it can.

    Advice that cannot work is worse than none: the platforms where ./install
    builds the runtime get told to run it, and the ones where it does not get
    told that plainly instead of chasing a rebuild that will skip them.
    """
    if asr_enabled():
        return ""
    cfg = resolve_asr()
    buildable = _is_termux() or _is_darwin()
    if _is_termux() and not shutil.which(_ANDROID_ASR):
        # The fastest fix on Android is not a rebuild — it is one package.
        return ("no speech-to-text. The quickest route on Android is the "
                "system recogniser: pkg install termux-api, plus the "
                "Termux:API app from F-Droid. Or run ./install to build "
                "audio.cpp's own model.")
    if not cfg["bin"]:
        if buildable:
            return "audiocpp_cli is not installed — run ./install"
        return ("voice input needs the audio.cpp speech runtime, which Gathm "
                "builds on Termux and macOS only; transcription is not "
                "available on this platform yet")
    if not buildable:
        return ("this audio.cpp build has no speech-to-text model, and Gathm "
                "only installs one on Termux and macOS")
    return ("no speech-to-text model installed — rebuild with "
            "GATHM_AUDIOCPP_MODELS=pocket_tts,sense_asr "
            "GATHM_AUDIOCPP_FORCE=1 ./install")


def _collect_text(obj, out: list) -> None:
    """Depth-first walk collecting every "text" value, in document order.

    audiocpp_cli writes structured JSON with --segments-out, but the exact
    schema differs by family (and by task), so the shape is not assumed —
    anything keyed "text" is transcript, whatever nests it.
    """
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "text" and isinstance(val, str):
                out.append(val)
            else:
                _collect_text(val, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_text(item, out)


def clean_transcript(text: str) -> str:
    """Strip model metadata tags and tidy whitespace."""
    t = _TAG_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", t).strip()


# audiocpp_cli labels the transcript on stdout, e.g.
#   text_output=How are you, hello.
# which is how "text_output=" ended up prepended to a perfectly good
# transcription on-device.
_LABEL_RE = re.compile(r"^\s*(text_output|text|transcript|result|output)\s*[=:]\s*",
                       re.I)


def _strip_label(line: str) -> str:
    return _LABEL_RE.sub("", line or "").strip()


def _transcript_from_stdout(text: str) -> str:
    """Last-resort scrape when no JSON was produced.

    Framework logs, the metrics block and progress lines all share stdout with
    the transcript. A labelled line wins outright; otherwise instrumentation is
    dropped and the longest remaining line is taken, since a transcript is prose
    and log lines are not.
    """
    noise = ("rtf", "wall", "load", "sample rate", "backend", "model", "warn",
             "error", "info", "debug", "audio duration", "threads")
    labelled = ""
    best = ""
    for raw in (text or "").splitlines():
        line = clean_transcript(raw)
        if not line:
            continue
        if _LABEL_RE.match(line):
            stripped = _strip_label(line)
            if stripped and len(stripped) > len(labelled):
                labelled = stripped
            continue
        if line.startswith(("[", "{", "#", "-")):
            continue
        low = line.lower()
        if any(tok in low for tok in noise) and ":" in line:
            continue
        if len(line) > len(best):
            best = line
    return labelled or best


def transcribe(audio_path: str) -> tuple[bool, str]:
    """Transcribe a wav. Returns (ok, text) or (False, reason)."""
    cfg = resolve_asr()
    if not cfg["bin"]:
        return False, "audiocpp_cli not found (run ./install on Termux)"
    if not cfg["model"]:
        return False, asr_unavailable_reason()
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        return False, f"no audio to transcribe ({audio_path})"

    # Everything reaches the model as 16 kHz mono. A phone recording is AAC or
    # Opus; a synthesised WAV is 22-24 kHz. Both are rejected by the model, so
    # both are converted here rather than surfacing as a decode error.
    converted = None
    original = audio_path
    good, result = ensure_wav16(audio_path)
    if not good:
        return False, result
    if result != original:
        converted = audio_path = result

    try:
        timeout = int(os.environ.get("GATHM_ASR_TIMEOUT", "300"))
    except ValueError:
        timeout = 300

    seg_fd, seg_path = tempfile.mkstemp(prefix="gathm-asr-", suffix=".json")
    os.close(seg_fd)
    cmd = [
        cfg["bin"], "--task", "asr",
        "--family", cfg["family"],
        "--model", cfg["model"],
        "--backend", "cpu",
        "--audio", audio_path,
        "--segments-out", seg_path,
    ]
    try:
        rc, out, err = _run_tracked(cmd, timeout, cwd=cfg["src"])
    except subprocess.TimeoutExpired:
        return False, f"transcription timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    try:
        if rc != 0:
            tail = (err or out or "").strip().splitlines()
            return False, tail[-1] if tail else f"audiocpp_cli exited {rc}"

        text = ""
        try:
            if os.path.getsize(seg_path) > 0:
                import json  # noqa: PLC0415 - only needed on this path
                with open(seg_path) as fh:
                    pieces: list = []
                    _collect_text(json.load(fh), pieces)
                text = clean_transcript(" ".join(_strip_label(p) for p in pieces))
        except Exception:  # noqa: BLE001 - fall back to stdout below
            text = ""
        if not text:
            text = _transcript_from_stdout(out)
        if not text:
            return False, "nothing was recognised in the recording"
        return True, text
    finally:
        for path in (seg_path, converted):
            if not path:
                continue
            try:
                os.unlink(path)
            except Exception:
                pass


def find_recorder() -> str | None:
    """Name of the first usable recording command, or None."""
    forced = os.environ.get("GATHM_AUDIO_RECORDER")
    if forced:
        return forced if shutil.which(forced.split()[0]) else None
    for name in _RECORDERS:
        if shutil.which(name):
            return name
    return None


def to_wav16(path: str) -> tuple[bool, str]:
    """Convert to 16 kHz mono WAV when ffmpeg is around; else pass through.

    termux-microphone-record cannot write WAV — it encodes AAC/Opus — so the
    Termux capture path always needs this step.
    """
    if not shutil.which("ffmpeg"):
        return (True, path) if path.lower().endswith(".wav") else (
            False, "ffmpeg is needed to convert the recording (pkg install ffmpeg)")
    fd, wav = tempfile.mkstemp(prefix="gathm-rec-", suffix=".16k.wav")
    os.close(fd)
    try:
        rc, _out, err = _run_tracked(
            ["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", "16000", wav], 120)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if rc != 0 or not os.path.exists(wav) or os.path.getsize(wav) == 0:
        tail = (err or "").strip().splitlines()
        return False, f"ffmpeg failed: {tail[-1] if tail else rc}"
    return True, wav


def wav_format(path: str) -> tuple:
    """(rate, channels) of a WAV, or (0, 0) if it is not a readable WAV.

    Header only — no decoding, no dependency.
    """
    try:
        with wave.open(path, "rb") as handle:
            return handle.getframerate(), handle.getnchannels()
    except Exception:  # noqa: BLE001 - not a WAV, or a variant wave cannot read
        return 0, 0


def ensure_wav16(path: str) -> tuple[bool, str]:
    """A path to 16 kHz mono WAV, converting only when it has to.

    SenseVoice (and the Silero VAD in front of it) accept exactly 16 kHz mono
    and reject anything else outright — "Silero VAD 16k model only supports
    sample_rate=16000". Checking only the file EXTENSION was not enough: a
    perfectly valid .wav from PocketTTS or `say -o` is 24 kHz or 22.05 kHz, and
    went to the model untouched to fail there.
    """
    if not path.lower().endswith(".wav"):
        return to_wav16(path)

    rate, channels = wav_format(path)
    if rate == 16000 and channels == 1:
        return True, path
    if rate == 0:
        # Named .wav but not one this build can parse; let ffmpeg try.
        return to_wav16(path)

    if not shutil.which("ffmpeg"):
        return False, (f"this WAV is {rate} Hz {channels}-channel and the "
                       "speech model needs 16 kHz mono; install ffmpeg so it "
                       "can be converted (brew install ffmpeg / pkg install ffmpeg)")
    return to_wav16(path)


def record_dir() -> str:
    """Where to record to.

    Not $TMPDIR: on Termux that is the app's private usr/tmp, and the recording
    is performed by the separate Termux:API app, which cannot reliably write
    there — the file simply never appears, which is why a hand-run recording
    into $HOME worked while lib/speech.py's own came back empty.
    """
    home = os.path.expanduser("~")
    for candidate in (os.path.join(home, ".gathm", "tmp"), home,
                      tempfile.gettempdir()):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".gathm-write-probe")
            with open(probe, "w") as fh:
                fh.write("x")
            os.unlink(probe)
            return candidate
        except Exception:  # noqa: BLE001 - try the next one
            continue
    return tempfile.gettempdir()


def record(seconds: int, out_path: str) -> tuple[bool, str]:
    """Record from the microphone. Returns (ok, path-or-reason)."""
    rec = find_recorder()
    if not rec:
        return False, ("no way to record — on Termux: pkg install termux-api, "
                       "plus the Termux:API app from F-Droid")

    name = rec.split()[0]
    if name == "termux-microphone-record":
        # Returns immediately and records in the background, so the wait and
        # the explicit stop are ours to do. It picks the encoder from -e; WAV
        # is not one of the options, hence the conversion afterwards.
        m4a = os.path.splitext(out_path)[0] + ".m4a"
        started = ""
        try:
            rc, out, err = _run_tracked(
                [name, "-f", m4a, "-l", str(seconds), "-e", "aac"], 30)
            started = ((out or "") + (err or "")).strip()
            if rc != 0:
                tail = started.splitlines()
                return False, (tail[-1] if tail else
                               "termux-microphone-record failed — is the "
                               "Termux:API app installed and mic permission granted?")
            time.sleep(seconds + 1)          # -l is a limit, not a wait
            _run_tracked([name, "-q"], 15)   # stop, and flush the file
            # The file is written by another process, so give it a moment to
            # appear rather than declaring failure on a race.
            for _ in range(12):
                if os.path.exists(m4a) and os.path.getsize(m4a) > 0:
                    break
                time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if not os.path.exists(m4a):
            return False, (f"the recorder never created {m4a} — "
                           + (started or "check that the Termux:API app is "
                                         "installed and has microphone permission"))
        if os.path.getsize(m4a) == 0:
            return False, (f"the recording at {m4a} is empty — "
                           "grant Termux:API microphone permission, or another "
                           "app may be holding the mic")
        return True, m4a

    if name == "arecord":
        argv = [name, "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
                "-d", str(seconds), out_path]
    elif name == "rec":
        argv = [name, "-q", "-r", "16000", "-c", "1", out_path,
                "trim", "0", str(seconds)]
    else:  # ffmpeg with a platform default input
        # avfoundation addresses devices by index ("[[video]:[audio]]") and
        # rejects the name "default" outright, so macOS needs its own default.
        # ffmpeg -f avfoundation -list_devices true -i "" names the indexes.
        mac = sys.platform == "darwin"
        device = os.environ.get("GATHM_AUDIO_INPUT",
                                ":0" if mac else "default")
        fmt = "avfoundation" if mac else "alsa"
        argv = [name, "-y", "-f", fmt, "-i", device, "-t", str(seconds),
                "-ac", "1", "-ar", "16000", out_path]
    try:
        rc, _out, err = _run_tracked(argv, seconds + 60)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        tail = (err or "").strip().splitlines()
        return False, f"{name}: {tail[-1] if tail else 'recording failed'}"
    return True, out_path


def listen(seconds: int | None = None) -> tuple[bool, str]:
    """Listen and return what was said. Returns (ok, text) or (False, reason).

    Android's recogniser does the whole job itself — capture, endpointing and
    transcription — so none of the record/convert/transcribe machinery below
    runs there. That is the entire speed difference: no fixed recording
    window, no WAV on disk, no 250 MB model loaded per utterance.
    """
    if not asr_enabled():
        return False, asr_unavailable_reason()

    if asr_engine() == "android":
        try:
            timeout = int(os.environ.get("GATHM_LISTEN_TIMEOUT", "60"))
        except ValueError:
            timeout = 60
        ok, text = listen_android(timeout)
        if ok or not _audiocpp_asr_enabled():
            return ok, text
        # Android refused (no Termux:API app, mic denied). audio.cpp is
        # installed, so try it rather than giving up on voice entirely.

    if seconds is None:
        try:
            seconds = int(os.environ.get("GATHM_LISTEN_SECONDS", "8"))
        except ValueError:
            seconds = 8
    seconds = max(1, min(int(seconds), 300))

    stop()  # never record ourselves talking
    fd, raw = tempfile.mkstemp(prefix="gathm-listen-", suffix=".wav",
                               dir=record_dir())
    os.close(fd)
    extra = []
    try:
        ok, got = record(seconds, raw)
        if not ok:
            return False, got
        if got != raw:
            extra.append(got)
        ok, wav = to_wav16(got)
        if not ok:
            return False, wav
        if wav != got:
            extra.append(wav)
        return transcribe(wav)
    finally:
        for path in [raw] + extra:
            try:
                os.unlink(path)
            except Exception:
                pass


def diagnose() -> int:
    """Print why speech would or would not work. Returns a shell exit code."""
    cfg = resolve()
    asr = resolve_asr()
    player = find_player()
    recorder = find_recorder()
    print("engine       : %s" % (engine() or "NONE"))
    print("audiocpp_cli : %s" % (cfg["bin"] or "NOT FOUND"))
    print("voice model  : %s" % (cfg["model"] or "NOT CONFIGURED"))
    print("family/voice : %s / %s" % (cfg["family"], cfg["voice"]))
    print("player       : %s" % (" ".join(player) if player else
                                 "NONE (pkg install mpv)"))
    system_voice = find_system_voice()
    print("system voice : %s" % (" ".join(system_voice) if system_voice else "none"))
    print("GATHM_SPEAK  : %s" % os.environ.get("GATHM_SPEAK", "1"))
    print("speaking     : %s" % enabled())
    print("gui audio    : %s" % ("yes" if can_synthesize_file() else
                                 "no (no engine can write a wav)"))
    print("")
    print("asr model    : %s" % (asr["model"] or "NOT INSTALLED"))
    print("asr family   : %s" % asr["family"])
    print("recorder     : %s" % (recorder or "NONE (pkg install termux-api)"))
    print("converter    : %s" % (shutil.which("ffmpeg") or
                                 "NONE (pkg install ffmpeg)"))
    print("record dir   : %s" % record_dir())
    engine_name = asr_engine() or "NONE"
    print("listening    : %s (%s)" % (asr_enabled(), engine_name))
    if engine_name == "android":
        print("             : Android's own recogniser — no model, no "
              "recording window")
    if engine() == "system":
        return 0 if enabled() else 1   # the OS voice does its own playback
    return 0 if (enabled() and player) else 1


_USAGE = """usage: python3 lib/speech.py [options] [text]

  <text>              say it out loud
  --check             report what works and what is missing
  --transcribe FILE   transcribe an audio file and print the text
  --listen [SECONDS]  record from the microphone, then transcribe
"""


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("--check", "-c", "--diagnose"):
        sys.exit(diagnose())
    if args[0] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)
    if args[0] in ("--transcribe", "-t"):
        if len(args) < 2:
            print("--transcribe needs an audio file", file=sys.stderr)
            sys.exit(2)
        good, text = transcribe(args[1])
        print(text if good else f"[speech] {text}",
              file=sys.stdout if good else sys.stderr)
        sys.exit(0 if good else 1)
    if args[0] in ("--listen", "-l"):
        secs = None
        if len(args) > 1:
            try:
                secs = int(args[1])
            except ValueError:
                print(f"not a number of seconds: {args[1]}", file=sys.stderr)
                sys.exit(2)
        print(f"[speech] listening for {secs or os.environ.get('GATHM_LISTEN_SECONDS', 8)}s...",
              file=sys.stderr)
        good, text = listen(secs)
        print(text if good else f"[speech] {text}",
              file=sys.stdout if good else sys.stderr)
        sys.exit(0 if good else 1)
    sys.exit(0 if speak(" ".join(args), quiet=False) else 1)
