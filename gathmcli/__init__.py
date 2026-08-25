"""Gathm, installed as a Python package.

Gathm itself is bash and Python in roughly equal measure: one launcher, a
handful of library scripts, an orchestrator, 56 tool directories, and the
Pilot TUI. None of that changes shape to be packaged — the tree is bundled
verbatim under `_bundle/` and `gathmcli.cli` execs the real launcher out of it.

The reason to package it at all is the virtualenv. `./install` spends most of
its effort building one and filling it with langchain, rich and prompt_toolkit;
pipx does exactly that as its normal job. What pipx cannot do is build
audio.cpp or install jq — so `./install` is still there, and `gathm setup` runs
it.
"""

__version__ = "3.0.0"
