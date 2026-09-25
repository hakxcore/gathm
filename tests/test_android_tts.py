#!/usr/bin/env python3
"""Android TTS selection and dispatch; no device or audio engine required."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import speech


def runtime(*binaries, termux=True, audiocpp=False):
    stack = ExitStack()
    stack.enter_context(patch.dict(os.environ, {
        "GATHM_SPEAK_COMMAND": "", "GATHM_SPEAK_ENGINE": "", "GATHM_SPEAK": "1",
    }))
    stack.enter_context(patch.object(speech, "_is_termux", return_value=termux))
    stack.enter_context(patch.object(speech, "_is_darwin", return_value=False))
    stack.enter_context(patch.object(speech.shutil, "which",
        side_effect=lambda name: "/mock/bin/" + name if name in binaries else None))
    stack.enter_context(patch.object(speech, "resolve", return_value={
        "bin": "/mock/audiocpp_cli" if audiocpp else "",
        "model": "/mock/voice-model" if audiocpp else "",
        "family": "pocket_tts", "voice": "alba", "src": "",
    }))
    return stack


class AndroidTtsTests(unittest.TestCase):
    def test_requires_termux_and_installed_command(self):
        with runtime("termux-tts-speak", termux=False):
            self.assertIsNone(speech.find_system_voice())
        with runtime():
            self.assertIsNone(speech.find_system_voice())
        with runtime("termux-tts-speak"):
            self.assertEqual(speech.find_system_voice(), ["termux-tts-speak", "--", "{t}"])
            self.assertEqual(speech.engine(), "system")
            self.assertTrue(speech.enabled())

    def test_native_voice_precedes_desktop_fallback_but_not_audiocpp(self):
        with runtime("termux-tts-speak", "espeak-ng", audiocpp=True):
            self.assertEqual(speech.find_system_voice()[0], "termux-tts-speak")
            self.assertEqual(speech.engine(), "audio.cpp")
            with patch.dict(os.environ, {"GATHM_SPEAK_ENGINE": "system"}):
                self.assertEqual(speech.engine(), "system")
        with runtime("espeak-ng"):
            self.assertEqual(speech.find_system_voice()[0], "espeak-ng")

    def test_explicit_command_is_preserved(self):
        with runtime("termux-tts-speak", "custom-tts"):
            with patch.dict(os.environ, {"GATHM_SPEAK_COMMAND": "custom-tts -r 2 {t}"}):
                self.assertEqual(speech.find_system_voice(), ["custom-tts", "-r", "2", "{t}"])
            with patch.dict(os.environ, {"GATHM_SPEAK_COMMAND": "custom-tts"}):
                self.assertEqual(speech.find_system_voice(), ["custom-tts", "{t}"])
            with patch.dict(os.environ, {"GATHM_SPEAK_COMMAND": "missing-command"}):
                self.assertIsNone(speech.find_system_voice())

    def test_native_voice_cannot_be_selected_for_wav_output(self):
        with runtime("termux-tts-speak"), patch.object(speech, "_run_tracked") as run:
            self.assertEqual(speech.system_voice_to_file(), "")
            self.assertFalse(speech.can_synthesize_file())
            self.assertFalse(speech.synthesize_system("hello", "/unused.wav")[0])
            run.assert_not_called()
        with runtime("termux-tts-speak", audiocpp=True):
            self.assertTrue(speech.can_synthesize_file())
            self.assertEqual(speech.system_voice_to_file(), "")

    def test_native_playback_does_not_hide_espeak_wav_output(self):
        with runtime("termux-tts-speak", "espeak-ng"), tempfile.TemporaryDirectory() as temp:
            self.assertEqual(speech.system_voice_to_file(), "espeak-ng")
            self.assertTrue(speech.can_synthesize_file())
            output = Path(temp) / "speech.wav"
            def render(argv, timeout):
                self.assertEqual(argv, ["espeak-ng", "-w", str(output), "hello"])
                self.assertEqual(timeout, 180)
                output.write_bytes(b"stub audio")
                return 0, "", ""
            with patch.object(speech, "_run_tracked", side_effect=render):
                self.assertTrue(speech.synthesize_system("hello", str(output))[0])
            with patch.dict(os.environ, {"GATHM_SPEAK_COMMAND": "termux-tts-speak"}):
                self.assertEqual(speech.system_voice_to_file(), "")

    def test_text_is_one_argument_after_option_delimiter_and_uses_existing_timeout(self):
        text = "-r 100; $(this is spoken, never executed)"
        with runtime("termux-tts-speak"), patch.object(speech, "_run_tracked",
                return_value=(0, "", "")) as run:
            self.assertTrue(speech._speak_system(text, quiet=True))
            run.assert_called_once_with(["termux-tts-speak", "--", text], 300)

    def test_native_voice_failures_remain_nonfatal(self):
        with runtime("termux-tts-speak"), patch.object(speech, "_run_tracked",
                side_effect=subprocess.TimeoutExpired("termux-tts-speak", 300)):
            self.assertFalse(speech.speak("hello", quiet=True))
        with runtime("termux-tts-speak"), patch.object(speech, "_run_tracked",
                return_value=(1, "", "Termux:API unavailable")):
            self.assertFalse(speech.speak("hello", quiet=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
