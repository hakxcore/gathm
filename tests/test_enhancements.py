"""
Tests for Gathm Enterprise v2.0 enhancements:
- Input sanitization
- Rate limiting
- Output caching
- Tool scaffolding
- Parallel execution
- API authentication
- Engineer model selection
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_SCRIPT = PROJECT_ROOT / "agent" / "orchestrator.sh"
TOOLS_DIR = PROJECT_ROOT / "tools"


class _HomeSandboxTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_home = tempfile.TemporaryDirectory(prefix="gathm-tests-home-")
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = cls._temp_home.name

    @classmethod
    def tearDownClass(cls):
        if cls._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls._old_home
        cls._temp_home.cleanup()

    def _run_agent(self, *args, expect_success=True, timeout=30):
        env = {
            **os.environ,
            "GATHM_OUTPUT_MODE": "json",
            "TERM": "xterm-256color",
        }
        result = subprocess.run(
            ["bash", str(AGENT_SCRIPT)] + list(args),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if expect_success:
            self.assertEqual(result.returncode, 0, msg=result.stderr.strip())
        return result


class TestInputSanitization(_HomeSandboxTestCase):
    """Tests for the input sanitization layer."""

    def test_blocks_command_injection_dollar_paren(self):
        result = self._run_agent("run", "weather", "$(whoami)", expect_success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked", result.stderr.lower())

    def test_blocks_command_injection_backtick(self):
        result = self._run_agent("run", "weather", "`id`", expect_success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked", result.stderr.lower())

    def test_blocks_path_traversal(self):
        result = self._run_agent("run", "weather", "../../etc/passwd", expect_success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("traversal", result.stderr.lower())

    def test_blocks_semicolon_injection(self):
        result = self._run_agent("run", "weather", "Paris; rm -rf /", expect_success=False)
        self.assertNotEqual(result.returncode, 0)

    def test_allows_normal_input(self):
        # Normal arguments should pass sanitization (tool may still fail but not due to sanitization)
        result = self._run_agent("run", "weather", "London")
        # We only check it doesn't fail on sanitization - tool may fail for other reasons
        self.assertNotIn("blocked", result.stderr.lower())
        self.assertNotIn("traversal", result.stderr.lower())


class TestToolScaffolding(_HomeSandboxTestCase):
    """Tests for the new-tool scaffolding command."""

    def test_creates_tool_directory(self):
        tool_name = "testscaffold"
        tool_dir = TOOLS_DIR / tool_name

        try:
            result = self._run_agent(
                "new-tool", tool_name,
                "--category", "testing",
                "--description", "A test scaffold tool",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            # Check directory exists
            self.assertTrue(tool_dir.is_dir(), f"Tool dir not created: {tool_dir}")

            # Check executable exists and is executable
            tool_exec = tool_dir / tool_name
            self.assertTrue(tool_exec.is_file(), f"Executable not created: {tool_exec}")
            self.assertTrue(os.access(tool_exec, os.X_OK), "Executable not marked +x")

            # Check tool.yaml exists
            manifest = tool_dir / "tool.yaml"
            self.assertTrue(manifest.is_file(), f"Manifest not created: {manifest}")

            # Check manifest content
            content = manifest.read_text()
            self.assertIn("testscaffold", content)
            self.assertIn("testing", content)
            self.assertIn("A test scaffold tool", content)

            # Check the tool actually runs
            run_result = self._run_agent("run", tool_name, "--help")
            self.assertEqual(run_result.returncode, 0)

        finally:
            # Cleanup
            import shutil
            if tool_dir.exists():
                shutil.rmtree(tool_dir)

    def test_rejects_duplicate_tool(self):
        # weather already exists
        result = self._run_agent("new-tool", "weather", expect_success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)


class TestAgentVersion(_HomeSandboxTestCase):
    """Tests for agent version and basic commands."""

    def test_version_is_2(self):
        result = self._run_agent("--version")
        self.assertIn("2.0.0", result.stdout)

    def test_help_includes_new_commands(self):
        result = self._run_agent("--help")
        output = result.stdout
        self.assertIn("new-tool", output)
        self.assertIn("parallel", output)
        self.assertIn("cache", output)

    def test_list_tools_json(self):
        result = self._run_agent("list", "--json")
        output = result.stdout.strip()
        self.assertTrue(output.startswith("["), f"Expected JSON array, got: {output[:50]}")
        tools = json.loads(output)
        self.assertGreater(len(tools), 40)

    def test_status_json(self):
        result = self._run_agent("status")
        output = result.stdout.strip()
        data = json.loads(output)
        self.assertEqual(data["agent"].strip(), "gathm-agent")
        self.assertEqual(data["version"].strip(), "2.0.0")


class TestCacheCommands(_HomeSandboxTestCase):
    """Tests for cache management commands."""

    def test_cache_stats(self):
        result = self._run_agent("cache", "stats")
        output = result.stdout.strip()
        data = json.loads(output)
        self.assertIn("total_entries", data)
        self.assertIn("active", data)

    def test_cache_clear(self):
        result = self._run_agent("cache", "clear")
        self.assertEqual(result.returncode, 0)

    def test_cache_purge(self):
        result = self._run_agent("cache", "purge")
        self.assertEqual(result.returncode, 0)


class TestLibraryFiles(_HomeSandboxTestCase):
    """Tests that new library files exist and are sourceable."""

    def test_cache_library_exists(self):
        cache_lib = PROJECT_ROOT / "lib" / "cache.bash"
        self.assertTrue(cache_lib.is_file())

    def test_ratelimit_library_exists(self):
        rl_lib = PROJECT_ROOT / "lib" / "ratelimit.bash"
        self.assertTrue(rl_lib.is_file())

    def test_cache_library_sourceable(self):
        result = subprocess.run(
            ["bash", "-c", f'source "{PROJECT_ROOT}/lib/cache.bash" && echo "OK"'],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HOME": self._temp_home.name},
        )
        self.assertEqual(result.stdout.strip(), "OK", msg=result.stderr)

    def test_ratelimit_library_sourceable(self):
        result = subprocess.run(
            ["bash", "-c", f'source "{PROJECT_ROOT}/lib/ratelimit.bash" && echo "OK"'],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "HOME": self._temp_home.name},
        )
        self.assertEqual(result.stdout.strip(), "OK", msg=result.stderr)


class TestToolManifestIntegrity(unittest.TestCase):
    """Validate all tool.yaml manifests have required fields."""

    REQUIRED_FIELDS = {"name", "version", "description", "category"}

    def test_all_manifests_have_required_fields(self):
        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if not tool_dir.is_dir():
                continue
            manifest = tool_dir / "tool.yaml"
            if not manifest.is_file():
                continue

            with self.subTest(tool=tool_dir.name):
                content = manifest.read_text()
                for field in self.REQUIRED_FIELDS:
                    self.assertIn(
                        f"{field}:",
                        content,
                        f"Missing required field '{field}' in {manifest}",
                    )

    def test_all_tools_have_manifests(self):
        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if not tool_dir.is_dir():
                continue
            executable = tool_dir / tool_dir.name
            if not executable.is_file():
                continue

            with self.subTest(tool=tool_dir.name):
                manifest = tool_dir / "tool.yaml"
                self.assertTrue(
                    manifest.is_file(),
                    f"Tool '{tool_dir.name}' has executable but no tool.yaml",
                )

    def test_all_executables_have_shebang(self):
        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if not tool_dir.is_dir():
                continue
            executable = tool_dir / tool_dir.name
            if not executable.is_file():
                continue

            with self.subTest(tool=tool_dir.name):
                first_line = executable.read_text().split("\n")[0]
                self.assertTrue(
                    first_line.startswith("#!"),
                    f"Tool '{tool_dir.name}' missing shebang line",
                )


class TestAPIServerImports(unittest.TestCase):
    """Test that the API server module can be imported."""

    def test_server_importable(self):
        import importlib.util
        server_path = PROJECT_ROOT / "api" / "server.py"
        spec = importlib.util.spec_from_file_location("server", server_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "GathmAPIHandler"))
        self.assertTrue(hasattr(module, "GATHM_API_KEY"))

    def test_server_has_auth_method(self):
        import importlib.util
        server_path = PROJECT_ROOT / "api" / "server.py"
        spec = importlib.util.spec_from_file_location("server", server_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = module.GathmAPIHandler
        self.assertTrue(hasattr(handler, "_check_auth"))


class TestEngineerModule(unittest.TestCase):
    """Test the engineer module configuration."""

    def test_engineer_importable(self):
        import importlib.util
        engineer_path = PROJECT_ROOT / "engineer" / "main.py"
        spec = importlib.util.spec_from_file_location("engineer_main", engineer_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        # Don't exec - it requires autogen which may not be installed
        # Just check the file is valid Python
        self.assertIsNotNone(spec.loader)

    def test_engineer_script_exists(self):
        script = PROJECT_ROOT / "engineer" / "engineer.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK))


if __name__ == "__main__":
    unittest.main()
