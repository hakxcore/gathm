"""Manifest-reader compatibility, including Bash 3.2 and Termux Bash."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gathm-schema-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "lib").mkdir()
        shutil.copy2(ROOT / "lib/schema.bash", self.root / "lib/schema.bash")

    def tool(self, name, manifest=None, executable=True):
        path = self.root / "tools" / name
        path.mkdir(parents=True)
        if executable:
            (path / name).touch()
        if manifest is not None:
            (path / "tool.yaml").write_text(manifest)

    def run_shell(self, code):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1/lib/schema.bash"; ' + code,
             "test", str(self.root)], capture_output=True, text=True, timeout=5)

    def test_manifest_fields_keep_existing_scalar_behavior(self):
        self.tool("demo", '# description: ignored\nname: demo\n'
                  'description:   "One: two"\nversion: \'3.0\'\ncategory: local')
        result = self.run_shell('get_tool_description demo; get_tool_version demo; get_tool_category demo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "One: two\n3.0\nlocal\n")

    def test_missing_field_succeeds_with_empty_output(self):
        self.tool("demo", "name: demo\n")
        result = self.run_shell('parse_manifest demo description')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_manifest_returns_failure(self):
        self.tool("demo")
        self.assertEqual(self.run_shell('parse_manifest demo description').returncode, 1)

    def test_catalog_is_sorted_and_skips_incomplete_tools(self):
        self.tool("zebra", 'category: local\nversion: "2"\ndescription: "Zebra: tool"')
        self.tool("alpha", 'description: \'Alpha\'\nversion: "1"\ncategory: local\n')
        self.tool("no_manifest")
        self.tool("no_script", 'description: hidden\n', executable=False)
        result = self.run_shell("list_tools_json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"name": "alpha", "description": "Alpha", "version": "1", "category": "local"},
            {"name": "zebra", "description": "Zebra: tool", "version": "2", "category": "local"},
        ])

    def test_catalog_handles_missing_fields_and_empty_directory(self):
        self.tool("empty", "name: empty\n")
        result = self.run_shell("list_tools_json")
        self.assertEqual(json.loads(result.stdout), [
            {"name": "empty", "description": "", "version": "", "category": ""}])
        shutil.rmtree(self.root / "tools")
        (self.root / "tools").mkdir()
        self.assertEqual(json.loads(self.run_shell("list_tools_json").stdout), [])


if __name__ == "__main__":
    unittest.main()
