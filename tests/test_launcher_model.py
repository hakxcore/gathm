"""Preflight uses the same model configuration as Pilot, without inference."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherModelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="gathm-config-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "lib").mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        for name in ("llm.py", "llamacpp.py"):
            shutil.copy(ROOT / "lib" / name, self.root / "lib" / name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GATHM_", "OLLAMA_", "ANTHROPIC_", "GOOGLE_", "GEMINI_"))}
        self.env.update(INSTALL_DIR=str(self.root), PREFLIGHT_PY=sys.executable,
                        GATHM_CONFIG_DIR=str(self.state), GATHM_STATE_DIR=str(self.state))
        source = (ROOT / "gathm").read_text()
        self.helpers = source[source.index('_LLM_CONFIG_CHECKED=0'):source.index('ollama_responding() {')]
        # Exercise the real configuration subprocess with no server actions.
        self.helpers = 'run_bounded() { shift; "$@"; }\n' + self.helpers

    def resolve(self, extra=""):
        script = self.helpers + '''
load_llm_config
llm_backend
llm_model
ollama_root
echo "$_LLM_CONFIG_HAS_KEY"
''' + extra
        result = subprocess.run(['bash', '-c', script], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return result.stdout.splitlines()

    def test_dotenv_model_endpoint_and_environment_override(self):
        (self.root / '.env').write_text(
            'GATHM_LLM_BACKEND=ollama\nGATHM_OLLAMA_MODEL=local:4b\n'
            'OLLAMA_BASE_URL=http://127.0.0.1:12345/v1\n')
        self.assertEqual(self.resolve(), ['ollama', 'local:4b', 'http://127.0.0.1:12345', '0'])
        self.env['GATHM_OLLAMA_MODEL'] = 'override:8b'
        self.assertEqual(self.resolve()[1], 'override:8b')

    def test_saved_model_and_generic_override(self):
        (self.state / 'llm_backend').write_text('ollama')
        (self.state / 'model').write_text('saved:4b')
        self.assertEqual(self.resolve()[1], 'saved:4b')
        self.env['GATHM_MODEL'] = 'generic:8b'
        self.assertEqual(self.resolve()[1], 'generic:8b')

    def test_hosted_backend_matches_provider_without_exposing_key(self):
        (self.root / '.env').write_text('ANTHROPIC_API_KEY=test-secret-not-for-output\n')
        result = self.resolve()
        self.assertEqual(result[0], 'anthropic')
        self.assertEqual(result[-1], '1')
        self.assertNotIn('test-secret-not-for-output', '\n'.join(result))

    def test_dotenv_is_inert_and_configuration_is_cached(self):
        marker = self.root / 'must-not-exist'
        model = '$(touch ' + str(marker) + ')'
        (self.root / '.env').write_text("GATHM_LLM_BACKEND=ollama\nGATHM_MODEL='" + model + "'\n")
        result = self.resolve("export GATHM_MODEL=changed\nllm_model\n")
        self.assertEqual(result[1], model)
        self.assertEqual(result[-1], model)
        self.assertFalse(marker.exists())

    def test_llamacpp_cli_honors_dotenv_and_environment(self):
        (self.root / '.env').write_text('GATHM_LLAMACPP_PORT=12345\n')
        command = [sys.executable, str(self.root / 'lib' / 'llamacpp.py'), 'url']
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'http://127.0.0.1:12345/v1')
        self.env['GATHM_LLAMACPP_PORT'] = '12346'
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.stdout.strip(), 'http://127.0.0.1:12346/v1')


if __name__ == '__main__':
    unittest.main()
