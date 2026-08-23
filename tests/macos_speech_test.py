#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the macOS speech path: engine choice, mic device, error wording.

Runs on any platform — sys.platform and the runtime lookups are stubbed, so
what is under test is the decision logic rather than this machine's hardware.

    python3 tests/macos_speech_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from lib import speech  # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")


def ok(name: str, cond) -> None:
    check(name, bool(cond), True)


class Env:
    """Pretend to be a platform with a given set of speech pieces installed."""

    def __init__(self, platform: str, audiocpp: bool, system_voice: bool,
                 asr_model: bool = False):
        self.platform = platform
        self.audiocpp = audiocpp
        self.system_voice = system_voice
        self.asr_model = asr_model
        self._saved: dict = {}

    def __enter__(self):
        self._saved = {
            "platform": speech.sys.platform,
            "resolve": speech.resolve,
            "resolve_asr": speech.resolve_asr,
            "find_system_voice": speech.find_system_voice,
            "is_termux": speech._is_termux,
            "isdir": speech.os.path.isdir,
        }
        speech.sys.platform = self.platform
        speech.resolve = lambda: {
            "bin": "/usr/local/bin/audiocpp_cli" if self.audiocpp else "",
            "model": "/models/pocket_tts" if self.audiocpp else "",
            "family": "pocket_tts", "voice": "alba", "src": "/src",
        }
        speech.resolve_asr = lambda: {
            "bin": "/usr/local/bin/audiocpp_cli" if self.audiocpp else "",
            "model": "/models/sense_asr" if self.asr_model else "",
            "family": "sense_asr", "src": "/src",
        }
        speech.find_system_voice = lambda: (["say", "{t}"]
                                            if self.system_voice else None)
        speech._is_termux = lambda: self.platform == "termux-fake"
        speech.os.path.isdir = lambda p: True
        return self

    def __exit__(self, *exc):
        speech.sys.platform = self._saved["platform"]
        speech.resolve = self._saved["resolve"]
        speech.resolve_asr = self._saved["resolve_asr"]
        speech.find_system_voice = self._saved["find_system_voice"]
        speech._is_termux = self._saved["is_termux"]
        speech.os.path.isdir = self._saved["isdir"]
        return False


def test_engine_choice() -> None:
    print("\nengine(): macOS keeps `say` for speaking")

    with Env("darwin", audiocpp=True, system_voice=True):
        check("mac with both installed prefers say", speech.engine(), "system")
    with Env("darwin", audiocpp=True, system_voice=False):
        check("mac with no say falls back to audio.cpp",
              speech.engine(), "audio.cpp")
    with Env("linux", audiocpp=True, system_voice=True):
        check("elsewhere audio.cpp still wins", speech.engine(), "audio.cpp")
    with Env("darwin", audiocpp=False, system_voice=False):
        check("nothing installed means no engine", speech.engine(), "")

    print("\nengine(): GATHM_SPEAK_ENGINE overrides")
    try:
        os.environ["GATHM_SPEAK_ENGINE"] = "audio.cpp"
        with Env("darwin", audiocpp=True, system_voice=True):
            check("forced audio.cpp on a mac", speech.engine(), "audio.cpp")
        with Env("darwin", audiocpp=False, system_voice=True):
            check("forced audio.cpp falls back when absent",
                  speech.engine(), "system")
        os.environ["GATHM_SPEAK_ENGINE"] = "system"
        with Env("linux", audiocpp=True, system_voice=True):
            check("forced system on linux", speech.engine(), "system")
    finally:
        os.environ.pop("GATHM_SPEAK_ENGINE", None)


def test_asr_reason() -> None:
    print("\nasr_unavailable_reason(): advice that can actually work")

    with Env("darwin", audiocpp=False, system_voice=True):
        reason = speech.asr_unavailable_reason()
        ok("mac is told to run the installer", "./install" in reason)
        ok("mac is not told it is unsupported",
           "not available on this platform" not in reason)
    with Env("linux", audiocpp=False, system_voice=True):
        reason = speech.asr_unavailable_reason()
        ok("linux is told plainly that it is unsupported",
           "not" in reason and "available on this platform" in reason)
        ok("linux names the platforms that do build it",
           "Termux and macOS" in reason)
    with Env("darwin", audiocpp=True, system_voice=True, asr_model=True):
        check("with the model present there is no complaint",
              speech.asr_unavailable_reason(), "")


def test_recorder_device() -> None:
    print("\nrecord(): avfoundation needs a device index, not 'default'")
    captured: dict = {}

    def fake_run(argv, timeout, cwd=None):
        captured["argv"] = argv
        return 1, "", "stubbed"

    saved_run, saved_which = speech._run_tracked, speech.shutil.which
    saved_platform = speech.sys.platform
    try:
        speech._run_tracked = fake_run
        speech.shutil.which = lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
        speech.sys.platform = "darwin"
        speech.record(2, "/tmp/gathm-test.wav")
        argv = captured.get("argv", [])
        ok("ffmpeg was chosen", argv and argv[0].endswith("ffmpeg"))
        check("avfoundation is the input format",
              argv[argv.index("-f") + 1], "avfoundation")
        check("the device is an index, not 'default'",
              argv[argv.index("-i") + 1], ":0")

        os.environ["GATHM_AUDIO_INPUT"] = ":1"
        speech.record(2, "/tmp/gathm-test.wav")
        check("GATHM_AUDIO_INPUT overrides it",
              captured["argv"][captured["argv"].index("-i") + 1], ":1")
        os.environ.pop("GATHM_AUDIO_INPUT", None)

        speech.sys.platform = "linux"
        speech.record(2, "/tmp/gathm-test.wav")
        argv = captured["argv"]
        check("linux still uses alsa", argv[argv.index("-f") + 1], "alsa")
        check("linux still uses the default device",
              argv[argv.index("-i") + 1], "default")
    finally:
        speech._run_tracked = saved_run
        speech.shutil.which = saved_which
        speech.sys.platform = saved_platform
        os.environ.pop("GATHM_AUDIO_INPUT", None)


def _gate_says(platform: str) -> str:
    """Run the installer's real platform gate for one platform string.

    The functions are pulled out of `install` and evaluated on their own, so
    this tests the shipped code rather than a copy of its logic.
    """
    script = (
        f'_GATHM_PLATFORM={platform}\n'
        'detect_platform() { echo "$_GATHM_PLATFORM"; }\n'
        'eval "$(sed -n \'/^_is_termux()/,/^}/p\' install)"\n'
        'eval "$(sed -n \'/^_is_darwin()/,/^}/p\' install)"\n'
        'eval "$(sed -n \'/^_audiocpp_supported()/,/^}/p\' install)"\n'
        '_audiocpp_supported && echo builds || echo skipped\n'
    )
    res = subprocess.run(["bash", "-c", script], capture_output=True,
                         text=True, cwd=ROOT, timeout=30)
    return (res.stdout or "").strip()


def test_sample_rate_guard() -> None:
    print("\nensure_wav16: the model only accepts 16 kHz mono")
    import struct
    import tempfile
    import wave as wavemod

    def make(rate, channels):
        path = os.path.join(tempfile.mkdtemp(), f"t{rate}x{channels}.wav")
        with wavemod.open(path, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack("<h", 0) * rate * channels)
        return path

    check("a 16 kHz mono wav is read correctly",
          speech.wav_format(make(16000, 1)), (16000, 1))
    check("so is 22050 stereo", speech.wav_format(make(22050, 2)), (22050, 2))
    check("a non-wav reports nothing rather than raising",
          speech.wav_format(os.path.join(ROOT, "README.md")), (0, 0))

    # Already correct: passed through untouched, no ffmpeg needed.
    good = make(16000, 1)
    ok_flag, out = speech.ensure_wav16(good)
    check("16 kHz mono is not converted", (ok_flag, out), (True, good))

    # Wrong rate: must NOT be handed to the model as-is. That was the bug —
    # PocketTTS writes 24 kHz and `say -o` writes 22.05 kHz, and checking only
    # the .wav extension let both through to fail inside the engine.
    wrong = make(22050, 1)
    saved = speech.shutil.which
    try:
        speech.shutil.which = lambda name: None          # no ffmpeg
        ok_flag, msg = speech.ensure_wav16(wrong)
        check("without ffmpeg it refuses rather than failing in the engine",
              ok_flag, False)
        ok("the refusal names the rate and what is needed",
           "22050" in msg and "16 kHz mono" in msg and "ffmpeg" in msg)
    finally:
        speech.shutil.which = saved

    src = open(os.path.join(ROOT, "install")).read()
    ok("the installer's ASR test goes through lib/speech.py",
       "lib/speech.py\" --transcribe" in src or "--transcribe" in src)


def test_installer_gate() -> None:
    print("\ninstall: the audio.cpp gate, run for real")

    # This is the assertion that was missing. The old test only checked that
    # _is_darwin() appeared in the source — which it did, while comparing
    # against "darwin" when this installer's own detect_platform says "macos".
    # The step skipped itself on every Mac and the test stayed green.
    check("a mac builds it", _gate_says("macos"), "builds")
    check("...by either spelling of Darwin", _gate_says("darwin"), "builds")
    check("termux builds it", _gate_says("termux"), "builds")
    check("linux does not", _gate_says("linux"), "skipped")
    check("windows does not", _gate_says("windows"), "skipped")

    src = open(os.path.join(ROOT, "install")).read()
    ok("a supported-platform helper replaces the termux-only test",
       "_audiocpp_supported()" in src)
    ok("a skip names the platform it detected",
       "platform is '${platform}'" in src)
    ok("the speech runtime can be installed on its own",
       "--audio-only|--speech)" in src)

    # A flaky connection was burning 20 minutes and losing everything, because
    # one combined clone means one failure discards the whole transfer.
    ok("the clone is retried", "GATHM_AUDIOCPP_GIT_RETRIES" in src)
    ok("submodules are fetched separately from the main repo",
       "--no-recurse-submodules" in src
       and "submodule update --init --recursive" in src)
    ok("a failed clone directory is cleared before retrying",
       'rm -rf "$src"' in src)
    ok("the failure tells the user they can clone it themselves",
       "clone it yourself" in src)

    # install runs under `set -euo pipefail`, so a bare call to anything that
    # can return non-zero exits the WHOLE installer. Speech is optional; a Mac
    # with no Xcode command line tools must not lose its shortcuts, its verify
    # step and its completion message over it.
    ok("install still runs under set -e (this test assumes it)",
       "set -euo pipefail" in src)
    unguarded = []
    for i, line in enumerate(src.splitlines(), 1):
        if "_audiocpp_install_build_deps" in line:
            body = line.strip()
            if body.endswith("() {") or body.startswith("#"):
                continue
            if not any(g in body for g in ("if !", "||", "&&", "if ")):
                unguarded.append(f"{i}: {body}")
    check("no bare call to the toolchain installer", unguarded, [])

    # `timeout` does not exist on a stock macOS, so a bare `timeout N cmd`
    # there silently means no limit at all.
    ok("a portable time limit exists", "_bounded()" in src)
    ok("it falls back past GNU timeout",
       "gtimeout" in src and "alarm shift" in src)
    # Mentioning it in a comment is fine; *calling* it is not, since this file
    # never sources the library that defines it.
    calls = [ln for ln in src.splitlines()
             if "run_bounded" in ln and not ln.lstrip().startswith("#")]
    check("nothing calls the undefined run_bounded", calls, [])
    ok("the entry point uses it", "if ! _audiocpp_supported; then" in src)
    ok("the skip message names both platforms",
       "built on Termux and macOS only" in src)
    ok("brew is used for the mac toolchain",
       "brew install" in src and "xcode-select" in src)
    ok("afplay is a known player", "afplay" in src)
    ok("the mac recorder path exists",
       "avfoundation" in src or "ffmpeg's avfoundation" in src)
    ok("OpenMP is not demanded of Apple clang",
       "libomp" in src)
    ok("CMake 4 gets the pre-3.5 policy escape hatch",
       "CMAKE_POLICY_VERSION_MINIMUM=3.5" in src)
    ok("...only when CMake is actually 4 or newer",
       "cmake_major >= 4" in src)
    ok("brew goes through the guarded helper", "_brew_install()" in src)
    ok("brew cannot upgrade unrelated dependents",
       "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1" in src)
    ok("brew does not auto-update the formula index",
       "HOMEBREW_NO_AUTO_UPDATE=1" in src)
    # Every brew *invocation* carries the guards; the remaining plain
    # "brew install ..." strings are advice printed to the user.
    import re as _re
    # Join line continuations: the env guards live on the line above the call.
    joined = _re.sub(r"\\\n\s*", " ", src)
    calls = [m for m in _re.findall(r"^[^#\n]*\bbrew install\b.*$", joined,
                                    _re.M)
             if "note_missing" not in m and "warn " not in m
             and "fail " not in m and "echo " not in m]
    unguarded = [c for c in calls
                 if "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK" not in c
                 and '"$@"' not in c]
    check("every brew invocation is guarded", unguarded, [])
    ok("dialog is no longer a mac dependency",
       "brew install bash curl jq git python3 pv wget" in src)
    ok("the sentencepiece patch is still Termux-gated",
       "if _is_termux; then\n        _audiocpp_patch_termux_sentencepiece" in src)
    ok("the stale 'Termux-only on purpose' claim is gone",
       "This is Termux-only on purpose" not in src)


def test_speaking_a_non_latin_script():
    print("\nspeaking a language the default voice does not have")
    # `say` was called with no -v, so it always used the system default voice —
    # an English one. Handed Devanagari it reads nothing useful, which is why
    # Gathm could write Hindi and not speak it.
    check("English is latin", speech.script_of("Hello there"), "latin")
    check("Hindi is devanagari",
          speech.script_of("नमस्ते, आप कैसे हैं?"), "devanagari")
    check("Chinese is han", speech.script_of("这是中文"), "han")
    check("Japanese kana", speech.script_of("こんにちは"), "kana")
    check("Korean hangul", speech.script_of("안녕하세요"), "hangul")
    check("Arabic", speech.script_of("مرحبا بك"), "arabic")
    check("Cyrillic", speech.script_of("Здравствуйте"), "cyrillic")
    check("Tamil", speech.script_of("வணக்கம்"), "tamil")

    # Counted, not sniffed from the first character: a Hindi reply carries
    # Latin punctuation, digits and stray English words.
    check("mostly Hindi with some English is still devanagari",
          speech.script_of("मेरे Desktop पर 9 फ़ाइलें हैं, ठीक है"), "devanagari")
    check("mostly English with one Hindi word stays latin",
          speech.script_of("Your Desktop has नौ files on it right now"), "latin")
    check("digits and punctuation alone decide nothing",
          speech.script_of("123 ... !!! 456"), "latin")
    check("empty text is latin", speech.script_of(""), "latin")

    # Voice selection, against a known voice list rather than this machine's.
    saved = speech._VOICE_CACHE.get("voices")
    try:
        speech._VOICE_CACHE["voices"] = [
            ("Alex", "en_US"), ("Samantha", "en_US"), ("Lekha", "hi_IN"),
            ("Kyoko", "ja_JP"), ("Ting-Ting", "zh_CN"),
        ]
        check("Hindi text picks the Hindi voice",
              speech.voice_for_text("नमस्ते"), "Lekha")
        check("Japanese picks the Japanese voice",
              speech.voice_for_text("こんにちは"), "Kyoko")
        check("Chinese picks the Chinese voice",
              speech.voice_for_text("这是中文"), "Ting-Ting")
        check("English picks nothing, so the user's own default is kept",
              speech.voice_for_text("Hello there"), "")
        check("a script with no installed voice picks nothing",
              speech.voice_for_text("வணக்கம்"), "")

        # The command actually changes.
        argv = speech._with_voice(["say", "नमस्ते"], "नमस्ते")
        check("say gains -v for Hindi", argv[:3], ["say", "-v", "Lekha"])
        check("...keeping the text last", argv[-1], "नमस्ते")
        check("English is left alone",
              speech._with_voice(["say", "hello"], "hello"), ["say", "hello"])
        check("a voice the user chose is not overridden",
              speech._with_voice(["say", "-v", "Daniel", "नमस्ते"], "नमस्ते"),
              ["say", "-v", "Daniel", "नमस्ते"])
        check("espeak is untouched — this is a say flag",
              speech._with_voice(["espeak-ng", "नमस्ते"], "नमस्ते"),
              ["espeak-ng", "नमस्ते"])

        # The file path is what the GUI uses, so it has to get the same voice
        # or Hindi is audible in the terminal and silent in the browser.
        file_argv = ["say", "-o", "/tmp/x.wav", "--data-format=LEI16@22050", "नमस्ते"]
        check("the GUI's render command gains it too",
              speech._with_voice(file_argv, "नमस्ते")[:3], ["say", "-v", "Lekha"])
    finally:
        if saved is None:
            speech._VOICE_CACHE.pop("voices", None)
        else:
            speech._VOICE_CACHE["voices"] = saved


def test_voice_list_parsing():
    print("\nreading the installed voice list")
    # tests/fixtures/say_voices.txt is real `say -v ?` output from a Mac, not a
    # sample I made up — which matters, because the invented one had none of
    # the two shapes that broke the parser.
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "say_voices.txt")
    with open(fixture, encoding="utf-8") as handle:
        lines = [ln for ln in handle.read().splitlines() if ln.strip()]

    parsed = []
    for line in lines:
        match = speech.re.match(
            r"^(.+?)\s+([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,4})?)\s+#", line)
        if match:
            parsed.append((match.group(1).strip(), match.group(2)))

    check("every line parses", len(parsed), len(lines))
    found = dict(parsed)

    # ar_001 is a real macOS locale: the region is not always two letters, and
    # a [A-Z]{2} region pattern dropped every Arabic voice.
    check("a numeric region is read", found.get("Majed"), "ar_001")
    # A long name leaves only ONE space before the locale, so requiring two
    # dropped these entirely.
    check("a long parenthesised name is read",
          found.get("Eddy (German (Germany))"), "de_DE")
    check("a name with a space in it survives", found.get("Bad News"), "en_US")
    check("...and one with brackets and two spaces",
          found.get("Flo (English (UK))"), "en_GB")
    check("the Hindi voice is there", found.get("Lekha"), "hi_IN")
    check("a three-letter region is not required", found.get("Sinji"), "zh_HK")

    # And the selection works against that real list.
    saved = speech._VOICE_CACHE.get("voices")
    try:
        speech._VOICE_CACHE["voices"] = parsed
        for text, want in [
            ("नमस्ते, आप कैसे हैं?", "Lekha"),      # Hindi
            ("வணக்கம்", "Vani"),                    # Tamil
            ("নমস্কার", "Piya"),                     # Bengali
            ("ನಮಸ್ಕಾರ", "Soumya"),                  # Kannada
            ("నమస్కారం", "Geeta"),                   # Telugu
            ("مرحبا بك", "Majed"),                  # Arabic, the ar_001 one
            ("Здравствуйте", "Milena"),             # Russian
            ("Χαίρετε", "Melina"),                  # Greek
            ("שלום", "Carmit"),                     # Hebrew
            ("สวัสดี", "Kanya"),                     # Thai
            ("안녕하세요", "Yuna"),                   # Korean
            ("Hello there", ""),                    # English keeps the default
        ]:
            check(f"{text[:14]!r} -> {want or '(default)'}",
                  speech.voice_for_text(text), want)

        # Japanese and Chinese both have several installed, including Apple's
        # character voices named "Eddy (Japanese (Japan))". Those sort early,
        # so a plain alphabetical pick handed Japanese to a novelty voice.
        by_name = dict(parsed)
        for text, prefix in [("こんにちは", "ja"), ("你好", "zh")]:
            picked = speech.voice_for_text(text)
            ok(f"{text} picks a {prefix} voice ({picked})",
               picked and by_name.get(picked, "").lower().startswith(prefix))
            ok(f"...and a real one, not a character voice ({picked})",
               "(" not in picked)
        check("Japanese gets Kyoko, not Eddy",
              speech.voice_for_text("こんにちは"), "Kyoko")

        # If brackets are all that is installed for a language, use them
        # rather than falling back to an English voice.
        speech._VOICE_CACHE["voices"] = [("Samantha", "en_US"),
                                         ("Eddy (Japanese (Japan))", "ja_JP")]
        check("a character voice is better than the wrong language",
              speech.voice_for_text("こんにちは"), "Eddy (Japanese (Japan))")
        speech._VOICE_CACHE["voices"] = parsed

        # No Gujarati, Punjabi or Malayalam voice is installed on that Mac, so
        # nothing is picked and the default is kept rather than a wrong one.
        for text, script in [("ગુજરાતી", "gujarati"), ("ਪੰਜਾਬੀ", "gurmukhi"),
                             ("മലയാളം", "malayalam")]:
            check(f"no {script} voice installed -> default",
                  speech.voice_for_text(text), "")
    finally:
        if saved is None:
            speech._VOICE_CACHE.pop("voices", None)
        else:
            speech._VOICE_CACHE["voices"] = saved


def main() -> int:
    print("macOS speech-path tests")
    print("=" * 60)
    test_engine_choice()
    test_asr_reason()
    test_recorder_device()
    test_sample_rate_guard()
    test_installer_gate()
    test_speaking_a_non_latin_script()
    test_voice_list_parsing()
    print("=" * 60)
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
