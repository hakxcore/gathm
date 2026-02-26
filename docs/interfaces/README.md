# Interfaces Overview

Gathm exposes the same tool ecosystem through multiple interfaces. Pick the one that fits your workflow.

| Interface | Entry Point | Best For |
|---|---|---|
| Agent Orchestrator | `gathm-agent` | Automation, chaining, planning, health/recovery |
| Classic Launcher | `gathm` | Interactive terminal menu usage |
| Pilot TUI | `python3 pilot/main.py` | Natural-language terminal assistant with tool execution |
| REST API | `python3 api/server.py` | Integrations, scripts, remote automation |
| Web GUI | `gui/index.html` + API | Chat-like browser client for API |
| Engineer Agent | `gathm-agent engineer` | Assisted code and tool development tasks |

## Interface Docs

- [Agent Orchestrator](./agent/README.md)
- [Classic Launcher](./classic-cli/README.md)
- [Pilot TUI](./pilot/README.md)
- [REST API](./api/README.md)
- [Web GUI](./gui/README.md)
- [Engineer Agent](./engineer/README.md)
