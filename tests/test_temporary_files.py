"""Tool temporary-file boundaries using isolated fixtures and stub HTTP data."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ToolTemporaryFilesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.temp = self.root / "temporary files"
        self.temp.mkdir()
        library = self.root / "lib"
        library.mkdir()
        (library / "utils.bash").write_text('''
check_dependencies() { :; }
getConfiguredClient() { :; }
checkInternet() { :; }
httpGet() { printf '[{"name_value":"www.example.com"}]'; }
gathm_tool_allowed() { return 1; }
print_divider() { :; }
''')
        for name in ("subdomains", "pdchain"):
            target = self.root / "tools" / name / name
            target.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "tools" / name / name, target)
            target.chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = dict(os.environ, TMPDIR=str(self.temp), HOME=str(self.root),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])

    def run_tool(self, name, *args):
        return subprocess.run(["bash", str(self.root / "tools" / name / name), *args],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def deny_temporary_files(self):
        command = self.bin / "mktemp"
        command.write_text("#!/bin/sh\nexit 1\n")
        command.chmod(0o755)

    def test_subdomains_removes_its_response_file(self):
        result = self.run_tool("subdomains", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("www.example.com", result.stdout)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_subdomains_stops_if_private_file_creation_fails(self):
        self.deny_temporary_files()
        result = self.run_tool("subdomains", "example.com")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot create a private response file", result.stderr)

    def test_default_chain_outputs_are_in_separate_private_directories(self):
        for _ in range(2):
            result = self.run_tool("pdchain", "example.com")
            self.assertEqual(result.returncode, 0, result.stderr)
        directories = list(self.temp.iterdir())
        self.assertEqual(len(directories), 2)
        for directory in directories:
            self.assertEqual((directory / "hosts.txt").read_text().splitlines(),
                             ["example.com", "www.example.com"])
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_chain_stops_if_private_directory_creation_fails(self):
        self.deny_temporary_files()
        result = self.run_tool("pdchain", "example.com")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot create a private output directory", result.stderr)
        self.assertEqual(list(self.temp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
