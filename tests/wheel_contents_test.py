"""Build from a dirty fixture: local environments and credentials must stay out."""
from pathlib import Path
import shutil
import tempfile
import zipfile

from hatchling.builders.wheel import WheelBuilder


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(source / name, root / name)
        required = [
            "gathmcli/__init__.py", "gathmcli/cli.py", "gathm", "install", "uninstall",
            "lib/install_common.bash", "pilot/main.py", "api/server.py",
            "gui/index.html", "gui/logo.png", "agent/orchestrator.sh",
            "tools/example/example", "tools/example/tool.yaml", "config/policies.yaml",
        ]
        unwanted = [
            "pilot/venv/lib/local-state", "pilot/.venv/lib/local-state",
            "pilot/__pycache__/main.pyc", "api/.env", "api/.env.production",
            "gui/node_modules/example/index.js", "tools/example/screenshot.png",
            "pilot/.ruff_cache/local-state", "pilot/.pytest_cache/local-state",
        ]
        for name in required + unwanted:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        builder = WheelBuilder(str(root))
        wheel = Path(next(builder.build(directory=str(root / "dist"), versions=["standard"])))
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
        def packaged(name):
            return name if name.startswith("gathmcli/") else "gathmcli/_bundle/" + name
        for name in required:
            assert packaged(name) in names, f"Missing runtime asset: {name}"
        for name in unwanted:
            assert packaged(name) not in names, f"Local file leaked into wheel: {name}"
        print(f"Wheel contents: {len(required)} required files present, {len(unwanted)} local files excluded")


if __name__ == "__main__":
    main()
