# Launcher (`gathm`)

`gathm` is the front door. Run it with no arguments and it starts the web GUI,
opens it in your browser, and hands the terminal over to the Pilot TUI.

## Run

```bash
gathm
```

If not on `PATH`:

```bash
bash gathm
```

## Behavior

1. Starts `api/server.py` in the background on port 8080 (reusing an already
   running server if one answers).
2. Opens `http://127.0.0.1:8080` in the default browser.
3. Execs the Pilot TUI (`pilot/run.sh`) in the foreground.

The GUI server outlives Pilot on purpose — closing the chat should not kill the
browser tab. Stop it with `gathm stop`.

## Subcommands and flags

| Command | What it does |
|---|---|
| `gathm` | GUI in the browser + Pilot here |
| `gathm tui` (`pilot`, `chat`) | Pilot only |
| `gathm gui` (`web`) | GUI server + browser, no Pilot |
| `gathm stop` | Stop the GUI server Gathm started |
| `gathm <tool> [args]` | Shortcut for `gathm run <tool> [args]` |

| Flag | Effect |
|---|---|
| `--no-gui`, `--tui-only` | Skip the server and the browser |
| `--no-browser` | Start the server, do not open a browser |
| `--port <n>` | GUI port (env: `GATHM_GUI_PORT`, default 8080) |
| `--host <addr>` | Bind address (env: `GATHM_GUI_HOST`, default 127.0.0.1) |

## Dependencies

Python 3 with the Pilot venv (`pilot/venv`, built by `./install`). The launcher
prefers that interpreter and falls back to `python3` on `PATH`. `curl` is used
to probe the port when present, with `wget` and Python as fallbacks.

The old `dialog`/`pv` menu is gone, so neither is required to launch any more.

```bash
./install
```

## Files

- `~/.gathm/gui.pid` — pid of the server this launcher started
- `~/.gathm/gui.log` — its stdout/stderr

## Notes

- For automation and structured workflows, use [`gathm`](../agent/README.md).
