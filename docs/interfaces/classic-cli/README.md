# Classic Launcher (`gathm`)

`gathm` is the menu-driven launcher for interactive terminal usage. It scans installed tools and lets you run one tool at a time with prompted arguments.

## Run

```bash
gathm
```

If not on `PATH`:

```bash
bash gathm
```

## Behavior

- Auto-discovers tools from `tools/*/<tool-name>`
- Displays tools in a `dialog` menu
- Prompts for tool arguments
- Runs selected tool and returns to menu

## Dependencies

`gathm` expects:

- `dialog`
- `pv`

Install via project setup:

```bash
bash install.sh
```

## Notes

- This interface is best for manual exploration.
- For automation and structured workflows, use [`gathm`](../agent/README.md).
