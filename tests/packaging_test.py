#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for installing Gathm as a package.

The failure this file exists to prevent: someone adds a directory the runtime
needs, does not add it to force-include, and `pipx install gathm` produces a
Gathm that is missing a third of itself — while every other test still passes,
because from a checkout the file is right there on disk.

    python3 tests/packaging_test.py
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")


def ok(name, cond):
    check(name, bool(cond), True)


def load_pyproject():
    try:
        import tomllib
    except ModuleNotFoundError:          # Python 3.9/3.10
        try:
            import tomli as tomllib      # type: ignore[no-redef]
        except ModuleNotFoundError:
            return None
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
        return tomllib.load(handle)


def test_metadata():
    print("\nthe package describes itself")
    cfg = load_pyproject()
    if cfg is None:
        print("  SKIP no tomllib/tomli on this Python")
        return
    project = cfg["project"]
    check("named gathm", project["name"], "gathm")
    check("the console script points at the shim",
          project["scripts"]["gathm"], "gathmcli.cli:main")
    ok("a readme is declared", project.get("readme"))
    ok("a python floor is declared", project.get("requires-python"))


def test_the_termux_install_stays_light():
    print("\nthe base install does not drag in a compiled toolchain")
    cfg = load_pyproject()
    if cfg is None:
        print("  SKIP no tomllib/tomli on this Python")
        return
    core = " ".join(cfg["project"]["dependencies"]).lower()
    extras = cfg["project"]["optional-dependencies"]

    # fastapi/uvicorn/pydantic are the web GUI's, and pydantic-core is compiled.
    # A phone should not have to build it to get a working assistant, and the
    # launcher already degrades to Pilot-only when they are absent.
    for heavy in ("fastapi", "uvicorn", "pydantic"):
        ok(f"{heavy} is not a base dependency", heavy not in core)
        ok(f"...but is available as an extra",
           any(heavy in " ".join(v).lower() for v in extras.values()))

    # playwright is a browser download. Never implied.
    ok("playwright is not a base dependency", "playwright" not in core)
    ok("playwright has its own extra",
       "playwright" in " ".join(extras["browser"]).lower())

    # What Pilot genuinely cannot start without.
    for needed in ("rich", "prompt_toolkit", "langchain-core", "langgraph"):
        ok(f"{needed} is a base dependency", needed.lower() in core)


def test_every_runtime_directory_is_packaged():
    print("\nnothing the runtime needs is left out of the wheel")
    cfg = load_pyproject()
    if cfg is None:
        print("  SKIP no tomllib/tomli on this Python")
        return
    included = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    mapped = set(included.keys())

    # Everything the launcher and orchestrator reach for at runtime. Add a
    # directory here when the runtime starts needing it — that is the point.
    for needed in ["gathm", "install", "lib", "pilot", "api", "gui", "agent",
                   "tools", "config"]:
        ok(f"{needed} is force-included", needed in mapped)
        ok(f"...and exists to be included",
           os.path.exists(os.path.join(ROOT, needed)))

    # Every mapping must land inside the bundle, or the launcher will not find
    # it: INSTALL_DIR is derived from where the launcher itself sits.
    for source, destination in included.items():
        ok(f"{source} lands in the bundle",
           destination.startswith("gathmcli/_bundle/"))

    # Anything on disk that looks like runtime but is not mapped is a trap.
    skip = {"tests", "docs", "engineer", "dist", "gathmcli", "node_modules"}
    for entry in sorted(os.listdir(ROOT)):
        if not os.path.isdir(os.path.join(ROOT, entry)):
            continue
        if entry.startswith(".") or entry in skip:
            continue
        ok(f"directory {entry!r} is accounted for", entry in mapped)


def test_the_sdist_is_whole_too():
    print("\nthe sdist carries the same tree as the wheel")
    cfg = load_pyproject()
    if cfg is None:
        print("  SKIP no tomllib/tomli on this Python")
        return

    wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    sdist = cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]

    # This is not belt and braces. `python -m build` writes the sdist and then
    # builds the wheel *from that sdist*, so a directory left out here vanishes
    # from the wheel too — and the force-include list above would still look
    # perfectly correct while it happened.
    for needed in sorted(wheel):
        ok(f"{needed} is in the sdist as well", needed in sdist)

    ok("the shim itself ships in the sdist", "gathmcli" in sdist)
    ok("...and pyproject, or it cannot be rebuilt",
       "pyproject.toml" in sdist)


def test_only_one_workflow_publishes():
    print("\nexactly one workflow talks to PyPI")
    flows = os.path.join(ROOT, ".github", "workflows")
    if not os.path.isdir(flows):
        print("  SKIP no workflows directory")
        return

    names = sorted(os.listdir(flows))
    publishers = []
    for name in names:
        with open(os.path.join(flows, name)) as handle:
            body = handle.read()
        if "gh-action-pypi-publish" in body:
            publishers.append(name)

    check("one workflow uploads to PyPI", len(publishers), 1)

    # A repo that already had release.yaml and then gained release.yml would
    # have two tag-triggered releases a single dot apart, and the trusted
    # publisher can only name one of them.
    stems = {}
    for name in names:
        stem = name.rsplit(".", 1)[0]
        stems.setdefault(stem, []).append(name)
    for stem, group in sorted(stems.items()):
        ok(f"no .yml/.yaml twins named {stem!r}", len(group) == 1)

    if publishers:
        ok("the publisher is not named release.*",
           not publishers[0].startswith("release."))


def test_the_shim():
    print("\nthe console script")
    from gathmcli import cli

    ok("it knows where the bundle should be",
       str(cli.bundle_dir()).endswith(os.path.join("gathmcli", "_bundle")))
    ok("it can name this platform",
       cli.platform_name() in ("termux", "macos", "linux", "windows", "unknown")
       or cli.platform_name())
    ok("it finds a bash here", cli.find_bash())
    ok("...and that bash is executable",
       os.access(cli.find_bash(), os.X_OK))

    # Every platform gets an estimate, because "how long will this take" is the
    # first question and silence during a 40-minute compile reads as a hang.
    for name in ("termux", "macos", "linux", "windows"):
        rows = cli.SETUP_ESTIMATES.get(name)
        ok(f"{name} has setup estimates", rows and len(rows) >= 3)
        ok(f"...each with a cost", all(len(r) == 2 and r[1] for r in rows or []))
    ok("Termux is warned about the compile",
       any("20-60" in cost for _, cost in cli.SETUP_ESTIMATES["termux"]))
    ok("and macOS is told it is much shorter",
       any("2-6" in cost for _, cost in cli.SETUP_ESTIMATES["macos"]))


def test_exec_bits_are_restored():
    print("\nthe execute bit, which a wheel does not promise to keep")
    from gathmcli import cli

    # A zip round trip can drop the execute bit, and the 56 tool scripts are
    # run directly by the orchestrator — losing it turns every tool into
    # "Permission denied".
    with tempfile.TemporaryDirectory() as tmp:
        bundle = os.path.join(tmp, "_bundle")
        os.makedirs(os.path.join(bundle, "tools", "dns"))
        os.makedirs(os.path.join(bundle, "lib"))
        made = {
            "gathm": os.path.join(bundle, "gathm"),
            "install": os.path.join(bundle, "install"),
            "tool": os.path.join(bundle, "tools", "dns", "dns"),
            "bash lib": os.path.join(bundle, "lib", "utils.bash"),
        }
        for path in made.values():
            with open(path, "w") as handle:
                handle.write("#!/usr/bin/env bash\n")
            os.chmod(path, 0o644)          # as an unlucky unpack leaves it

        for label, path in made.items():
            ok(f"{label} starts non-executable", not os.access(path, os.X_OK))

        import pathlib
        cli.ensure_executable(pathlib.Path(bundle))

        for label, path in made.items():
            ok(f"{label} is executable afterwards", os.access(path, os.X_OK))

        ok("a marker records it so it is done once",
           os.path.exists(os.path.join(bundle, ".exec-bits-set")))

        # Read-only bundle: must not raise, since refusing to run is worse.
        with tempfile.TemporaryDirectory() as tmp2:
            ro = os.path.join(tmp2, "_bundle")
            os.makedirs(ro)
            with open(os.path.join(ro, "gathm"), "w") as handle:
                handle.write("x")
            os.chmod(ro, 0o555)
            try:
                cli.ensure_executable(pathlib.Path(ro))
                ok("a read-only bundle does not raise", True)
            except Exception as exc:  # noqa: BLE001
                ok(f"a read-only bundle does not raise ({exc})", False)
            finally:
                os.chmod(ro, 0o755)


def test_the_launcher_uses_the_venv_python():
    print("\nthe launcher runs Pilot with the interpreter that has the deps")
    with open(os.path.join(ROOT, "gathm")) as handle:
        launcher = handle.read()

    # Without this, a pipx install finds no pilot/venv, falls through to a bare
    # `python3` on PATH, and Pilot cannot import rich — while the interpreter
    # holding rich sits unused two directories away.
    ok("GATHM_PYTHON is consulted", "GATHM_PYTHON" in launcher)
    ok("...before the checkout venv",
       launcher.index("GATHM_PYTHON") < launcher.index("pilot/venv/bin/python3"))
    ok("advice adapts to how Gathm was installed",
       "GATHM_INSTALL_KIND" in launcher)
    ok("...and names pipx when that is how", "pipx" in launcher)

    from gathmcli import cli
    src = open(os.path.join(os.path.dirname(cli.__file__), "cli.py")).read()
    ok("the shim sets GATHM_PYTHON", "GATHM_PYTHON" in src)
    ok("to this interpreter", "sys.executable" in src)
    ok("and execs rather than spawning a parent", "execve" in src)


def main():
    print("Packaging tests")
    print("=" * 60)
    test_metadata()
    test_the_termux_install_stays_light()
    test_every_runtime_directory_is_packaged()
    test_the_sdist_is_whole_too()
    test_only_one_workflow_publishes()
    test_the_shim()
    test_exec_bits_are_restored()
    test_the_launcher_uses_the_venv_python()
    print("=" * 60)
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
