"""Typed adapters: mock OS controls; file operations use disposable fixtures."""
import json
import errno
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

from lib import system_control as control


class SystemControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        for name, value in (("platform_name", "macos"), ("_which", "/test/program")):
            mock = patch.object(control, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        home = patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

    def test_bad_commands_and_injections_never_reach_an_os_call(self):
        for command in ("", "volume set -1", "volume set 101", "volume set 1.2", "volume set 20; reboot",
                        "volume set $(id)", "power shutdown now", "power reboot", "status\nshutdown",
                        "processes stop -1", "files move a b c", "brightness set NaN"):
            with self.subTest(command=command), patch.object(control, "_run") as run:
                with self.assertRaises(ValueError):
                    control.prepare(command)
                run.assert_not_called()

    def test_prepare_volume_and_power_only_proposes_and_roundtrips_json(self):
        with patch.object(control, "_run") as run:
            for command in ("volume set 40", "power.restart", "power shutdown"):
                if command == "power.restart":
                    command = "power restart"
                plan = control.prepare(command)
                self.assertTrue(plan["requires_confirmation"])
                self.assertEqual(plan, json.loads(json.dumps(plan)))
                if command.startswith("power"):
                    self.assertIn("unsaved", plan["warning"])
            run.assert_not_called()

    def test_exact_power_aliases_keep_approval_and_canonical_command(self):
        for command in ("restart", "reboot", "shutdown", "sleep"):
            plan = control.prepare(command)
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(plan["command"], "power " + ("restart" if command == "reboot" else command))

    def test_exact_validated_volume_is_used_and_private_plan_tampering_fails(self):
        plan = control.prepare("volume set 040")
        with patch.object(control, "_run", return_value="") as run, patch.object(control, "_audit"):
            self.assertTrue(control.execute(plan)["ok"])
            self.assertEqual(run.call_args.args[0][-1], "set volume output volume 40")
            run.reset_mock()
            plan["_args"] = ["100"]
            self.assertFalse(control.execute(plan)["ok"])
            run.assert_not_called()

    def test_no_confirmation_for_inspection(self):
        for command in ("status", "volume get", "brightness get", "apps list", "processes list"):
            self.assertFalse(control.prepare(command)["requires_confirmation"])

    def test_linux_and_windows_power_use_fixed_non_forced_commands(self):
        for plat, command, expected in (("linux", "power shutdown", ["--no-ask-password", "poweroff"]),
                                        ("windows", "power restart", ["/r", "/t", "0"])):
            with self.subTest(plat=plat), patch.object(control, "platform_name", return_value=plat), \
                    patch.object(control, "_run", return_value="") as run, patch.object(control, "_audit"):
                plan = control.prepare(command)
                self.assertTrue(control.execute(plan)["ok"])
                self.assertEqual(run.call_args.args[0][1:], expected)

    def test_rootless_mobile_does_not_offer_power_or_screen_capture(self):
        with patch.object(control, "platform_name", return_value="termux"):
            for command in ("power restart", "power shutdown", "power lock", "screenshot", "apps list"):
                with self.assertRaises(ValueError):
                    control.prepare(command)
            self.assertTrue(control.prepare("brightness set 50")["requires_confirmation"])

    def test_termux_volume_scales_to_actual_stream_range(self):
        with patch.object(control, "platform_name", return_value="termux"), \
                patch.object(control, "_run", side_effect=[json.dumps([{"stream": "music", "max_volume": 15}]), ""]) as run, \
                patch.object(control, "_audit"):
            self.assertTrue(control.execute(control.prepare("volume set 40"))["ok"])
            self.assertEqual(run.call_args.args[0][1:], ["music", "6"])

    def test_process_self_parent_and_reused_pid_refused(self):
        with patch.object(control, "_ancestors", return_value={77, 78}), \
                patch.object(control, "_process_identity", return_value=[99, "501", "start old"]) as identity, \
                patch.object(control.os, "kill") as kill, patch.object(control, "_audit"):
            with self.assertRaises(ValueError):
                control.prepare("processes stop 78")
            plan = control.prepare("processes stop 99")
            identity.return_value = [99, "501", "start new"]
            self.assertFalse(control.execute(plan)["ok"])
            kill.assert_not_called()

    def test_process_owner_must_match(self):
        with patch.object(control, "_run", return_value="999999 Wed Jan 1 other-process"):
            with self.assertRaises(ValueError):
                control._process_identity(987, "macos")

    def test_windows_process_owner_uses_sid_not_ambiguous_username(self):
        payload = {"CreationDate": "2026-10-04T00:00:00Z", "Name": "fixture.exe"}
        with patch.object(control, "_powershell", return_value=json.dumps(payload)) as shell:
            control._process_identity(987, "windows")
        script = shell.call_args.args[0]
        self.assertIn("GetOwnerSid", script)
        self.assertIn("WindowsIdentity]::GetCurrent().User.Value", script)
        self.assertIn("$o.ReturnValue -ne 0 -or $o.Sid -ne $sid", script)
        self.assertNotIn("$env:USERNAME", script)

    def test_paths_cannot_modify_home_credentials_source_or_symlinks(self):
        (self.home / "file").write_text("keep")
        (self.home / "link").symlink_to(self.home / "file")
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/key").write_text("fixture")
        for path in (self.home, self.home / "link", self.home / ".ssh/key", Path("/etc"), control.ROOT):
            with self.subTest(path=path), self.assertRaises(ValueError):
                control.prepare("files trash " + shlex.quote(str(path)))

    def test_move_never_overwrites_and_changed_target_requires_new_proposal(self):
        source, destination = self.home / "source", self.home / "destination"
        source.write_text("source data")
        command = "files move " + shlex.join([str(source), str(destination)])
        plan = control.prepare(command)
        destination.write_text("destination data")
        result = control.execute(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(source.read_text(), "source data")
        self.assertEqual(destination.read_text(), "destination data")

    def test_case_aliases_and_other_credential_locations_are_protected(self):
        for plat in ("macos", "windows"):
            with patch.object(control, "platform_name", return_value=plat):
                for name in (".SSH", ".GATHM", ".NETRC", ".KUBE", ".DOCKER", ".AZURE", "AppData",
                             ".GIT", ".ENV", ".ENV.local", ".npmrc", ".pypirc", ".git-credentials"):
                    path = self.home / name
                    path.mkdir(exist_ok=True)
                    with self.subTest(plat=plat, name=name), self.assertRaises(ValueError):
                        control.prepare("files trash " + shlex.quote(str(path)))
                root = self.home / "fixture-repository"
                alias = self.home / "FIXTURE-REPOSITORY"
                alias.mkdir(exist_ok=True)
                with patch.object(control, "ROOT", root), self.assertRaises(ValueError):
                    control.prepare("files trash " + shlex.quote(str(alias)))

    def test_native_move_preserves_file_and_directory_identity(self):
        for directory in (False, True):
            source = self.home / ("folder" if directory else "file")
            destination = self.home / ("moved-folder" if directory else "moved-file")
            if directory:
                source.mkdir()
                (source / "note").write_text("folder contents")
            else:
                source.write_text("file contents")
            identity = control._fingerprint(source)[:2]
            control._move_no_replace(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual(control._fingerprint(destination)[:2], identity)
            self.assertEqual((destination / "note").read_text() if directory else destination.read_text(),
                             "folder contents" if directory else "file contents")

    def test_destination_appearing_at_the_native_move_is_not_replaced(self):
        source, destination = self.home / "folder", self.home / "raced-destination"
        source.mkdir()
        (source / "note").write_text("keep this")
        native = control._rename_api()
        if native is None:
            self.skipTest("native POSIX rename test")
        created_identity = []

        def race(*args):
            destination.mkdir()
            created_identity.extend(control._fingerprint(destination)[:2])
            return native(*args)

        with patch.object(control, "_rename_api", return_value=race):
            with self.assertRaises(FileExistsError):
                control._move_no_replace(source, destination)
        self.assertEqual(control._fingerprint(destination)[:2], created_identity)
        self.assertEqual((source / "note").read_text(), "keep this")

    def test_save_after_atomic_move_is_not_unlinked(self):
        source, destination = self.home / "note.txt", self.home / "moved.txt"
        source.write_text("approved version")
        native = control._rename_api()
        if native is None:
            self.skipTest("native POSIX rename test")

        def save_after_rename(*args):
            result = native(*args)
            self.assertEqual(result, 0)
            source.write_text("new save")
            return result

        with patch.object(control, "_rename_api", return_value=save_after_rename):
            control._move_no_replace(source, destination)
        self.assertEqual(source.read_text(), "new save")
        self.assertEqual(destination.read_text(), "approved version")

    def test_atomic_rename_flags_for_macos_and_linux(self):
        source, destination = self.home / "source", self.home / "destination"
        for platform, symbol, expected in (
                ("darwin", "renamex_np", (os.fsencode(source), os.fsencode(destination), 4)),
                ("linux", "renameat2", (-100, os.fsencode(source), -100, os.fsencode(destination), 1))):
            function = Mock(return_value=0)
            with self.subTest(platform=platform), patch.object(control.sys, "platform", platform), \
                    patch.object(control.os, "name", "posix"), \
                    patch.object(control.ctypes, "CDLL", return_value=SimpleNamespace(**{symbol: function})):
                control._move_no_replace(source, destination)
            function.assert_called_once_with(*expected)

    def test_windows_move_does_not_precreate_a_destination(self):
        source, destination = self.home / "folder", self.home / "moved-folder"
        source.mkdir()
        with patch.object(control.os, "name", "nt"), patch.object(control.os, "rename") as rename:
            control._move_no_replace(source, destination)
        rename.assert_called_once_with(source, destination)
        self.assertFalse(destination.exists())

    def test_native_move_failure_never_copies_or_unlinks(self):
        source, destination = self.home / "source", self.home / "destination"
        source.write_text("untouched")
        with patch.object(control, "_rename_api", return_value=Mock(return_value=-1)), \
                patch.object(control.ctypes, "get_errno", return_value=errno.EXDEV), \
                patch.object(control.os, "rename") as rename, patch.object(control.os, "link") as link:
            with self.assertRaises(OSError) as failure:
                control._move_no_replace(source, destination)
        self.assertEqual(failure.exception.errno, errno.EXDEV)
        rename.assert_not_called()
        link.assert_not_called()
        self.assertEqual(source.read_text(), "untouched")
        self.assertFalse(destination.exists())

    def test_unsupported_atomic_move_is_rejected_at_prepare(self):
        source, destination = self.home / "source", self.home / "destination"
        source.write_text("untouched")
        with patch.object(control.sys, "platform", "linux"), \
                patch.object(control.ctypes, "CDLL", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, "Atomic no-overwrite"):
                control.prepare("files move " + shlex.join([str(source), str(destination)]))
        self.assertEqual(source.read_text(), "untouched")

    def test_file_open_cannot_execute_a_script(self):
        script = self.home / "evil.command"
        script.write_text("echo no")
        with self.assertRaises(ValueError):
            control.prepare("files open " + shlex.quote(str(script)))

    def test_mkdir_move_and_trash_are_recoverable_in_fixture(self):
        folder = self.home / "new folder"
        self.assertTrue(control.execute(control.prepare("files mkdir " + shlex.quote(str(folder))))["ok"])
        source, target = folder / "note.txt", folder / "moved.txt"
        source.write_text("preserved")
        self.assertTrue(control.execute(control.prepare("files move " + shlex.join([str(source), str(target)])))["ok"])
        self.assertFalse(source.exists())
        self.assertTrue(control.execute(control.prepare("files trash " + shlex.quote(str(target))))["ok"])
        entries = list((self.home / ".gathm/trash").glob("*/original-location.json"))
        self.assertEqual(len(entries), 1)
        metadata = json.loads(entries[0].read_text())
        self.assertEqual(metadata["original"], str(target))
        self.assertEqual(Path(metadata["item"]).read_text(), "preserved")

    def test_app_must_be_discovered_and_same_app_after_approval(self):
        app = self.home / "Calculator.app"
        app.mkdir()
        with patch.object(control, "_installed_apps", return_value={str(app): "Calculator"}), \
                patch.object(control, "_run") as run:
            with self.assertRaises(ValueError):
                control.prepare('apps open "Calculator; malicious"')
            plan = control.prepare("apps open Calculator")
            (app / "new-file").write_text("changed app")
            self.assertFalse(control.execute(plan)["ok"])
            run.assert_not_called()

    def test_missing_dependencies_are_honest_in_status(self):
        with patch.object(control, "_which", side_effect=ValueError("unavailable")):
            self.assertIn("volume set: unavailable", control.execute(control.prepare("status"))["output"])

    def test_audit_failure_prevents_mutation(self):
        plan = control.prepare("power shutdown")
        with patch.object(control, "_audit", side_effect=OSError("read only")), patch.object(control, "_perform") as run:
            self.assertFalse(control.execute(plan)["ok"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
