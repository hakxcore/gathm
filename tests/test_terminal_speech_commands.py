"""Test terminal speech controls without a model, microphone, or audio output.

Engine selection is simulated here. These are not native Windows or Android
hardware tests; they exercise the UI's contract with the speech runtime.
"""

import ast
from contextlib import contextmanager
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import subprocess
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from lib import speech


def load_commands(console):
    # Importing main builds the model provider. Load these standalone command
    # handlers directly so the test never starts an installed inference server.
    source = Path(__file__).resolve().parent.parent / "pilot" / "main.py"
    tree = ast.parse(source.read_text())
    names = {"_voice_input", "_handle_speak_command"}
    handlers = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"console": console, "os": os, "Optional": Optional}
    exec(compile(ast.Module(body=handlers, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class TestTerminalSpeechCommands(unittest.TestCase):
    def setUp(self):
        self.output = StringIO()
        self.commands = load_commands(Console(file=self.output, width=120, color_system=None))

    @contextmanager
    def system_voice(self, name):
        with patch.object(speech, "engine", return_value="system"), \
                patch.object(speech, "find_system_voice", return_value=[name, "{t}"]), \
                patch.object(speech, "find_player", return_value=None), \
                patch.object(speech, "resolve", side_effect=AssertionError("system voice needs no audio.cpp model")), \
                patch.dict(os.environ, {"GATHM_SPEAK": "1"}):
            yield

    def test_system_voice_needs_neither_audiocpp_nor_separate_player(self):
        for voice in ("say", "termux-tts-speak", "espeak-ng"):
            with self.subTest(voice=voice), self.system_voice(voice):
                self.output.seek(0)
                self.output.truncate()
                self.commands["_handle_speak_command"]("on")
                self.commands["_handle_speak_command"]("")
                text = self.output.getvalue()
                self.assertIn("Speech on.", text)
                self.assertIn("Engine: system", text)
                self.assertIn("Voice: " + voice, text)
                self.assertIn("Enabled: True", text)
                self.assertNotIn("not installed", text)
                self.assertNotIn("Player:", text)
                self.assertNotIn("./install on Termux", text)

    def test_android_recogniser_does_not_need_a_separate_recorder(self):
        with patch.object(speech, "asr_enabled", return_value=True), \
                patch.object(speech, "asr_engine", return_value="android"), \
                patch.object(speech, "find_recorder", return_value=None), \
                patch.object(speech, "listen", return_value=(True, "Plan my day")) as listen:
            self.assertEqual(self.commands["_voice_input"](""), "Plan my day")
            listen.assert_called_once_with(None)
            self.assertIn("Android speech recognition", self.output.getvalue())
            self.assertNotIn("Listening for", self.output.getvalue())

    def test_unavailable_listening_reports_runtime_platform_advice(self):
        for reason in ("audiocpp_cli is not installed — run ./install",
                       "transcription is not available on this platform yet"):
            with self.subTest(reason=reason), \
                    patch.object(speech, "asr_enabled", return_value=False), \
                    patch.object(speech, "asr_unavailable_reason", return_value=reason), \
                    patch.object(speech, "listen") as listen:
                self.assertIsNone(self.commands["_voice_input"](""))
                listen.assert_not_called()
                self.assertIn(reason, self.output.getvalue())

    def test_audiocpp_recording_uses_safe_duration_and_preserves_failure(self):
        with patch.object(speech, "asr_enabled", return_value=True), \
                patch.object(speech, "asr_engine", return_value="audio.cpp"), \
                patch.object(speech, "listen", return_value=(False, "Microphone [denied]")) as listen, \
                patch.dict(os.environ, {"GATHM_LISTEN_SECONDS": "invalid"}):
            self.assertIsNone(self.commands["_voice_input"](""))
            listen.assert_called_once_with(8)
            self.assertIn("Microphone [denied]", self.output.getvalue())

    def test_audiocpp_missing_player_is_reported(self):
        with patch.object(speech, "engine", return_value="audio.cpp"), \
                patch.object(speech, "find_player", return_value=None), \
                patch.object(speech, "resolve", return_value={
                    "bin": "audiocpp_cli", "model": "voice-model", "voice": "alba", "family": "pocket_tts",
                }), patch.dict(os.environ, {"GATHM_SPEAK": "1"}):
            self.commands["_handle_speak_command"]("on")
            self.commands["_handle_speak_command"]("")
            self.assertIn("no audio player is available", self.output.getvalue())
            self.assertIn("Player: not available", self.output.getvalue())

    def test_disabled_system_voice_can_be_enabled(self):
        with self.system_voice("say"), patch.dict(os.environ, {"GATHM_SPEAK": "0"}):
            self.commands["_handle_speak_command"]("")
            self.assertIn("Enabled: False", self.output.getvalue())
            self.assertIn("/speak on", self.output.getvalue())

    def test_no_engine_reports_no_engine(self):
        with patch.object(speech, "engine", return_value=""), \
                patch.dict(os.environ, {"GATHM_SPEAK": "1"}):
            self.commands["_handle_speak_command"]("on")
            self.commands["_handle_speak_command"]("")
            self.assertIn("no speech engine is available", self.output.getvalue())
            self.assertIn("Engine: not available", self.output.getvalue())
            self.assertIn("Enabled: False", self.output.getvalue())


class TestSpeechProcessCleanup(unittest.TestCase):
    def test_windows_cleanup_without_sigkill_escalates_to_process_kill(self):
        proc = Mock(pid=42)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("voice", 0.5), None]
        with patch.object(speech, "os", SimpleNamespace()), \
                patch.object(speech, "signal", SimpleNamespace(SIGTERM=15)):
            speech._kill_tree(proc)
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()
        self.assertEqual(proc.wait.call_count, 2)

    def test_posix_cleanup_still_signals_the_whole_process_group(self):
        proc = Mock(pid=42)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("voice", 0.5), None]
        killpg = Mock()
        with patch.object(speech, "os", SimpleNamespace(killpg=killpg, getpgid=lambda pid: 100)), \
                patch.object(speech, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9)):
            speech._kill_tree(proc)
        self.assertEqual([call.args for call in killpg.call_args_list], [(100, 15), (100, 9)])
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
