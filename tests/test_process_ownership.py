"""Stale PID files cannot authorize signals to unrelated processes."""
import json
import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import llamacpp


class ProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, {"GATHM_CONFIG_DIR": temporary.name})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = llamacpp.LlamaCppConfig.from_env()
        llamacpp.pid_file().write_text("12345\n")
        llamacpp.process_record().write_text(json.dumps({"pid": 12345, "birth": "original",
            "root": str(Path(llamacpp.__file__).resolve().parents[1])}))

    def test_reused_pid_is_left_running(self):
        with patch.object(llamacpp, "_pid_alive", return_value=True), \
                patch.object(llamacpp, "_process_birth", return_value="different"), \
                patch.object(llamacpp, "_terminate") as terminate:
            ok, message = llamacpp.stop(self.cfg)
        self.assertFalse(ok)
        self.assertIn("left running", message)
        terminate.assert_not_called()
        self.assertTrue(llamacpp.pid_file().exists())

    def test_another_checkout_is_not_owned(self):
        data = json.loads(llamacpp.process_record().read_text())
        data['root'] = '/another-checkout'
        llamacpp.process_record().write_text(json.dumps(data))
        with patch.object(llamacpp, "_process_birth", return_value="original"):
            self.assertFalse(llamacpp._owns_process(12345))

    def test_legacy_or_corrupt_record_is_not_authority(self):
        for value in (None, "not JSON", "[]", '{"pid":12345}'):
            if value is None:
                llamacpp.process_record().unlink(missing_ok=True)
            else:
                llamacpp.process_record().write_text(value)
            with patch.object(llamacpp, "_pid_alive", return_value=True), \
                    patch.object(llamacpp, "_terminate") as terminate:
                self.assertFalse(llamacpp.stop(self.cfg)[0])
                terminate.assert_not_called()

    def test_matching_process_stops_and_records_are_removed(self):
        with patch.object(llamacpp, "_pid_alive", side_effect=[True, False]), \
                patch.object(llamacpp, "_process_birth", return_value="original"), \
                patch.object(llamacpp, "_terminate") as terminate:
            self.assertTrue(llamacpp.stop(self.cfg)[0])
        terminate.assert_called_once_with(12345)
        self.assertFalse(llamacpp.pid_file().exists())
        self.assertFalse(llamacpp.process_record().exists())

    def test_identity_rechecked_before_force_signal(self):
        with patch.object(llamacpp, "_pid_alive", return_value=True), \
                patch.object(llamacpp, "_process_birth", side_effect=["original", "new"]), \
                patch.object(llamacpp.time, "sleep"), \
                patch.object(llamacpp, "_terminate") as terminate:
            ok, message = llamacpp.stop(self.cfg)
        self.assertFalse(ok)
        self.assertIn("identity changed", message)
        terminate.assert_called_once_with(12345)

    def test_invalid_and_symlinked_pid_files(self):
        for text in ("1", "0", "-9", "garbage", "9" * 100):
            llamacpp.pid_file().write_text(text)
            self.assertIsNone(llamacpp._read_pid())
        target = llamacpp.pid_file().with_suffix(".external")
        target.write_text("12345")
        llamacpp.pid_file().unlink()
        llamacpp.pid_file().symlink_to(target)
        self.assertIsNone(llamacpp._read_pid())

    def test_record_has_private_permissions_and_failed_probe_clears_old_record(self):
        with patch.object(llamacpp, "_process_birth", return_value="current"):
            llamacpp._record_process(12345)
        if os.name != "nt":
            self.assertEqual(llamacpp.process_record().stat().st_mode & 0o777, 0o600)
        with patch.object(llamacpp, "_process_birth", return_value=None):
            llamacpp._record_process(23456)
        self.assertFalse(llamacpp.process_record().exists())

    def test_runtime_log_omits_private_command_arguments(self):
        cfg = dataclasses.replace(self.cfg, binary=Path("fixture-llama"),
                                  model=Path("fixture.gguf"),
                                  extra_args=("--api-key", "test-only-credential"))
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(llamacpp, "_health", return_value="down"), \
                patch.object(llamacpp, "_spawn", return_value=process) as spawn, \
                patch.object(llamacpp, "_process_birth", return_value="original"), \
                patch.object(llamacpp, "_wait_until_ready", return_value=(True, "ready")), \
                patch.object(llamacpp.time, "sleep"):
            self.assertTrue(llamacpp.start(cfg)[0])
        self.assertNotIn("test-only-credential", llamacpp.log_file().read_text())
        self.assertTrue(spawn.call_args.args[1].closed)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_only_a_group_leader_permits_group_signal(self):
        with patch.object(os, "getpgid", return_value=22222), \
                patch.object(os, "killpg") as group, patch.object(os, "kill") as single:
            llamacpp._terminate(12345)
        group.assert_not_called()
        single.assert_called_once()


if __name__ == "__main__":
    unittest.main()
