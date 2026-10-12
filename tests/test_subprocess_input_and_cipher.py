"""Regression checks for terminal input ownership and dependency-free help."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SpeechInputTests(unittest.TestCase):
    def test_speech_child_cannot_consume_the_next_user_command(self):
        # A real pipe models unread input owned by the terminal assistant.
        # Run in a separate interpreter so this cannot alter the test runner's
        # own stdin, and let the child try to read all available input.
        runner = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from lib import speech
result = speech._run_tracked(
    [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
    timeout=5,
)
print(json.dumps({"child": result, "remaining": sys.stdin.read()}))
"""
        pending_command = "next user command\n"
        result = subprocess.run(
            [sys.executable, "-c", runner, str(ROOT)],
            input=pending_command, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["child"], [0, "", ""])
        self.assertEqual(observed["remaining"], pending_command)


class CipherDependencyTests(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        if self.bash is None:
            self.skipTest("bash is required to run cipher")
        self.directory = tempfile.TemporaryDirectory(prefix="gathm-cipher-test-")
        self.addCleanup(self.directory.cleanup)
        fixture = Path(self.directory.name)
        binary_directory = fixture / "bin"
        binary_directory.mkdir()
        # Exercise the actual tool and utility library with a PATH that has
        # shell utilities but deliberately lacks both encoding programs.
        for command in ("dirname", "cat", "mkdir", "touch", "uname"):
            executable = shutil.which(command)
            if executable is None:
                self.skipTest(f"{command} is required for the cipher fixture")
            (binary_directory / command).symlink_to(executable)
        self.environment = {
            "PATH": str(binary_directory),
            "GATHM_LOG_DIR": str(fixture / "logs"),
        }

    def run_cipher(self, *args):
        return subprocess.run(
            [self.bash, str(ROOT / "tools" / "cipher" / "cipher"), *args],
            env=self.environment, capture_output=True, text=True, timeout=5,
        )

    def test_help_and_version_work_without_encoding_dependencies(self):
        for arguments, expected in (((), "Usage: cipher"),
                                    (("-h",), "Usage: cipher"),
                                    (("-v",), "Version 3.0.0")):
            with self.subTest(arguments=arguments):
                result = self.run_cipher(*arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertEqual(result.stderr, "")

    def test_encoding_and_decoding_still_require_dependencies(self):
        for mode in ("-e", "-d"):
            with self.subTest(mode=mode):
                result = self.run_cipher(mode, "sample")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Required command 'base32' is not installed", result.stdout)
                self.assertIn("Required command 'base64' is not installed", result.stdout)


if __name__ == "__main__":
    unittest.main()
