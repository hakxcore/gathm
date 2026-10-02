"""Latency regressions without starting a model or contacting a network.

Blocking probes use events so the tests check ordering, not machine-dependent
millisecond budgets. Each loaded assistant has its own cache and lock.
"""

import importlib.util
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from lib.llm import LLMConfig, LLMProvider


ROOT = Path(__file__).resolve().parent.parent


def load_assistant():
    spec = importlib.util.spec_from_file_location(
        "assistant_latency_main", ROOT / "pilot" / "main.py")
    assistant = importlib.util.module_from_spec(spec)
    # Explicit fake config prevents discovery of the user's installed model.
    with patch.object(LLMConfig, "from_env", return_value=LLMConfig(
            backend="llamacpp", model="test-model", base_url="http://invalid.test/v1")), \
            patch.object(LLMProvider, "langchain_chat_model") as construct, \
            patch.object(LLMProvider, "ensure_ready") as ensure_ready:
        spec.loader.exec_module(assistant)
    return assistant, construct, ensure_ready


class ChatClientLatencyTests(unittest.TestCase):
    def setUp(self):
        self.assistant, self.import_construct, self.import_ready = load_assistant()
        self.provider = Mock()
        self.provider.langchain_chat_model.side_effect = lambda: object()
        self.provider_patch = patch.object(
            self.assistant, "LLMProvider", return_value=self.provider)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def test_import_does_not_construct_client_or_start_inference_server(self):
        self.import_construct.assert_not_called()
        self.import_ready.assert_not_called()

    def test_repeated_turns_reuse_client_but_recheck_llama_server(self):
        first = self.assistant._build_llm()
        self.assertIs(self.assistant._build_llm(), first)
        self.assertIs(self.assistant._build_llm(), first)
        self.provider.langchain_chat_model.assert_called_once_with()
        # The provider handles readiness during construction; cached clients
        # still need recovery if the local server died between turns.
        self.assertEqual(self.provider.ensure_ready.call_count, 2)

    def test_cached_remote_client_does_not_probe_local_llama_server(self):
        self.assistant._llm_config = LLMConfig(
            backend="anthropic", model="test-remote", api_key="test-key")
        self.assertIs(self.assistant._build_llm(), self.assistant._build_llm())
        self.provider.langchain_chat_model.assert_called_once_with()
        self.provider.ensure_ready.assert_not_called()

    def test_config_changes_replace_the_client(self):
        for field, value in (("model", "replacement-model"),
                             ("base_url", "http://replacement.test/v1"),
                             ("model_path", "/test/replacement.gguf"),
                             ("api_key", "replacement-key"),
                             ("backend", "ollama")):
            with self.subTest(field=field):
                previous = self.assistant._build_llm()
                setattr(self.assistant._llm_config, field, value)
                replacement = self.assistant._build_llm()
                self.assertIsNot(replacement, previous)
                self.assertIs(self.assistant._build_llm(), replacement)

    def test_generation_setting_changes_replace_the_client(self):
        changes = {"GATHM_LLM_TEMPERATURE": "0.2",
                   "GATHM_LLM_TIMEOUT": "120",
                   "GATHM_OLLAMA_KEEP_ALIVE": "10m",
                   "GATHM_OLLAMA_NUM_PREDICT": "256",
                   "GATHM_OLLAMA_NUM_CTX": "2048"}
        # Isolate both additions and removals from the developer's settings.
        with patch.dict(os.environ):
            for name in changes:
                os.environ.pop(name, None)
            for name, value in changes.items():
                with self.subTest(setting=name):
                    previous = self.assistant._build_llm()
                    os.environ[name] = value
                    replacement = self.assistant._build_llm()
                    self.assertIsNot(replacement, previous)
                    self.assertIs(self.assistant._build_llm(), replacement)
                    del os.environ[name]
                    self.assertIsNot(self.assistant._build_llm(), replacement)

    def test_failed_construction_can_be_retried(self):
        self.provider.langchain_chat_model.side_effect = [
            RuntimeError("server could not start"), object()]
        with self.assertRaisesRegex(RuntimeError, "server could not start"):
            self.assistant._build_llm()
        recovered = self.assistant._build_llm()
        self.assertIs(self.assistant._build_llm(), recovered)
        self.assertEqual(self.provider.langchain_chat_model.call_count, 2)

    def test_failed_readiness_is_reported_even_with_cached_client(self):
        self.assistant._build_llm()
        self.provider.ensure_ready.side_effect = RuntimeError("server stopped")
        with self.assertRaisesRegex(RuntimeError, "server stopped"):
            self.assistant._build_llm()
        self.provider.langchain_chat_model.assert_called_once_with()


class ConnectivityLatencyTests(unittest.TestCase):
    def setUp(self):
        self.assistant, _, _ = load_assistant()
        self.now = 100.0
        self.assistant.time = SimpleNamespace(monotonic=lambda: self.now)
        self.threads = []

        def tracked_thread(**kwargs):
            worker = threading.Thread(**kwargs)
            self.threads.append(worker)
            return worker

        self.assistant.threading = SimpleNamespace(Thread=tracked_thread)

    def finish_refresh(self):
        for worker in self.threads:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "connectivity refresh did not finish")
        self.assertFalse(self.assistant._CONN_LOCK.locked())

    def test_slow_initial_probe_does_not_block_and_is_single_flight(self):
        started = threading.Event()
        release = threading.Event()

        def probe():
            started.set()
            release.wait(timeout=2)
            return "online"

        with patch.object(self.assistant, "check_connectivity", side_effect=probe) as check:
            try:
                self.assertEqual(self.assistant.current_connectivity(), "unknown")
                self.assertTrue(started.wait(timeout=1))
                for _ in range(10):
                    self.assertEqual(self.assistant.current_connectivity(), "unknown")
                self.assertEqual(len(self.threads), 1)
                check.assert_called_once_with()
            finally:
                release.set()
                self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "online")
            check.assert_called_once_with()

    def test_welcome_renders_before_slow_network_probe_finishes(self):
        release = threading.Event()

        def probe():
            release.wait(timeout=2)
            return "online"

        with patch.object(self.assistant, "check_connectivity", side_effect=probe), \
                patch.object(self.assistant, "discover_tools", return_value={}), \
                patch.object(self.assistant, "_detect_platform", return_value="Test platform"), \
                patch.object(self.assistant, "render_welcome") as welcome, \
                patch.object(self.assistant, "print_status_bar") as status_bar:
            try:
                self.assistant.print_tricolor_banner()
                self.assertEqual(welcome.call_args.kwargs["connectivity"], "unknown")
                self.assertEqual(len(self.threads), 1)
                status_bar.assert_called_once_with()
            finally:
                release.set()
                self.finish_refresh()

    def test_fresh_cached_status_avoids_probe_then_expiry_refreshes(self):
        with patch.object(self.assistant, "check_connectivity", return_value="offline") as check:
            self.assistant._CONN_CACHE.update(status="online", ts=self.now)
            self.now += self.assistant._CONN_TTL_SECONDS / 2
            self.assertEqual(self.assistant.current_connectivity(), "online")
            check.assert_not_called()
            self.now += self.assistant._CONN_TTL_SECONDS
            self.assistant.current_connectivity()
            self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "offline")
            check.assert_called_once_with()

    def test_stale_known_status_is_available_while_refresh_is_in_flight(self):
        release = threading.Event()
        self.assistant._CONN_CACHE.update(status="offline", ts=1.0)

        def probe():
            release.wait(timeout=2)
            return "online"

        with patch.object(self.assistant, "check_connectivity", side_effect=probe):
            try:
                self.assertEqual(self.assistant.current_connectivity(), "offline")
                self.assertEqual(self.assistant.current_connectivity(), "offline")
                self.assertEqual(len(self.threads), 1)
            finally:
                release.set()
                self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "online")

    def test_probe_failure_releases_lock_and_caches_unknown_until_retry(self):
        with patch.object(self.assistant, "check_connectivity", side_effect=[
                RuntimeError("probe failed"), "online"]) as check:
            self.assistant.current_connectivity()
            self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "unknown")
            check.assert_called_once_with()
            self.now += self.assistant._CONN_TTL_SECONDS + 1
            self.assistant.current_connectivity()
            self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "online")
            self.assertEqual(check.call_count, 2)

    def test_thread_start_failure_does_not_permanently_lock_refresh(self):
        with patch.object(self.assistant.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start thread")
            self.assertEqual(self.assistant.current_connectivity(), "unknown")
            self.assertFalse(self.assistant._CONN_LOCK.locked())
        with patch.object(self.assistant, "check_connectivity", return_value="online") as check:
            self.assistant.current_connectivity()
            self.finish_refresh()
            self.assertEqual(self.assistant.current_connectivity(), "online")
            check.assert_called_once_with()


class ConnectivitySocketTests(unittest.TestCase):
    def test_welcome_distinguishes_unknown_network_from_offline(self):
        from pilot import tui
        for status, label in (("unknown", "Network status unknown"),
                              ("offline", "Offline"), ("online", "Online")):
            with self.subTest(status=status), \
                    patch.object(tui, "check_connectivity") as probe, \
                    patch.object(tui.os, "system"), \
                    patch.object(tui, "_print_panel") as panel:
                tui.render_welcome("Test model", 0, "Test platform", status)
                content = panel.call_args.args[0].plain
                self.assertIn("Test platform · " + label, content)
                if status == "unknown":
                    self.assertNotIn("Offline", content)
                probe.assert_not_called()

    def test_probe_closes_socket_and_uses_short_timeout(self):
        from pilot import tui
        connection = MagicMock()
        with patch.object(tui.socket, "create_connection", return_value=connection) as connect:
            self.assertEqual(tui.check_connectivity(), "online")
        connection.__exit__.assert_called_once_with(None, None, None)
        self.assertLessEqual(connect.call_args.kwargs["timeout"], 1.0)

    def test_unreachable_network_is_offline(self):
        from pilot import tui
        with patch.object(tui.socket, "create_connection", side_effect=OSError("unreachable")):
            self.assertEqual(tui.check_connectivity(), "offline")


if __name__ == "__main__":
    unittest.main()
