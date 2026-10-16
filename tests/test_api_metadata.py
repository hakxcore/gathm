"""Live metadata caching must preserve edits, revocations and caller isolation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api import server


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / "policies.yaml"
        self.policy.write_text("roles:\n  admin:\n    permissions: ['*']\n")
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.manifest = self.add_tool("weather")
        state = patch.multiple(server, TOOLS_DIR=self.tools, POLICIES_FILE=self.policy,
                               _policies={}, _policies_mtime=None)
        state.start()
        self.addCleanup(state.stop)
        server._cached_tool_manifest.cache_clear()
        self.addCleanup(server._cached_tool_manifest.cache_clear)

    def add_tool(self, name):
        directory = self.tools / name
        directory.mkdir()
        (directory / name).write_text("#!/bin/sh\n")
        manifest = directory / "tool.yaml"
        manifest.write_text("description: Initial\ntags: [test]\n")
        return manifest

    def test_catalog_reuses_parsing_and_returns_independent_nested_values(self):
        with patch.object(server, "_load_yaml", wraps=server._load_yaml) as read:
            first = server.list_tools()
            first[0]["tags"].append("changed by caller")
            self.assertEqual(server.list_tools()[0]["tags"], ["test"])
            self.assertEqual(read.call_count, 1)

    def test_manifest_edit_is_visible_immediately(self):
        server.list_tools()
        self.manifest.write_text("description: Updated metadata\n")
        self.assertEqual(server.list_tools()[0]["description"], "Updated metadata")

    def test_atomic_replacement_with_original_mtime_is_not_stale(self):
        server.list_tools()
        stamp = self.manifest.stat()
        replacement = self.root / "replacement.yaml"
        replacement.write_text("description: Changed\ntags: [test]\n")
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(self.manifest)
        self.assertEqual(server.list_tools()[0]["description"], "Changed")

    def test_removal_and_recreation_do_not_restore_stale_manifest(self):
        server.list_tools()
        self.manifest.unlink()
        self.assertEqual(server.load_tool_manifest("weather"), {})
        self.manifest.write_text("description: Recreated\n")
        self.assertEqual(server.load_tool_manifest("weather")["description"], "Recreated")

    def test_permission_scope_avoids_manifest_parsing(self):
        with patch.object(server, "load_tool_manifest", side_effect=AssertionError("unneeded parse")):
            self.assertIn("weather", server._role_environment("admin")["GATHM_ALLOWED_TOOLS"])

    def test_tool_install_and_removal_are_visible_without_a_cache_timeout(self):
        self.assertEqual(server._tool_names(), ["weather"])
        self.add_tool("news")
        self.assertEqual(server._tool_names(), ["news", "weather"])
        (self.tools / "weather" / "weather").unlink()
        self.assertEqual(server._tool_names(), ["news"])

    def test_invalid_names_never_read_a_manifest(self):
        with patch.object(server, "_load_yaml", side_effect=AssertionError("unexpected read")):
            self.assertEqual(server.load_tool_manifest("../weather"), {})

    def test_missing_policy_revokes_cached_permissions_and_can_recover(self):
        self.assertTrue(server.role_has_permission("admin", "system:control"))
        self.policy.unlink()
        self.assertFalse(server.role_has_permission("admin", "system:control"))
        self.policy.write_text("roles:\n  admin:\n    permissions: ['health:check']\n")
        self.assertTrue(server.role_has_permission("admin", "health:check"))
        self.assertFalse(server.role_has_permission("admin", "system:control"))

    def test_unreadable_policy_cannot_reuse_cached_permissions(self):
        self.assertTrue(server.role_has_permission("admin", "system:control"))
        with patch.object(server, "_file_stamp", side_effect=PermissionError):
            self.assertFalse(server.role_has_permission("admin", "system:control"))

    def test_policy_replacement_preserving_mtime_revokes_permissions(self):
        self.assertTrue(server.role_has_permission("admin", "system:control"))
        stamp = self.policy.stat()
        replacement = self.root / "replacement.yaml"
        replacement.write_text("roles:\n  admin:\n    permissions: []\n")
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(self.policy)
        self.assertFalse(server.role_has_permission("admin", "system:control"))


if __name__ == "__main__":
    unittest.main()
