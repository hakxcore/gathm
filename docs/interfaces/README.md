# Interfaces Overview

Gathm is a personal AI assistant you can talk to or type to. Choose the browser
for voice conversation with visible chat, or the terminal for conversation
alongside your command-line work. Tools and automation remain available when
needed.

| Interface | Entry Point | Best For |
|---|---|---|
| Browser | `gathm gui` | Voice conversation, typed chat, and saved browser history |
| Terminal | `gathm tui` | Conversation, writing, planning, and optional voice or tools |
| Combined launcher | `gathm` | Start the browser and terminal assistant together |
| REST API | `python3 api/server.py` | Integrate chat, speech, and explicit tool calls |
| Tool orchestrator | `gathm run`, `chain`, `parallel`, and other commands | Scripts, direct tools, health, and recovery |
| Engineer agent | `gathm engineer` | Assisted code and tool development tasks |

From a source checkout, prefix launcher commands with `./`. Voice capabilities
vary with the installed speech engines; text chat remains available.

## Interface Docs

- [Browser](./gui/README.md)
- [Terminal](./pilot/README.md)
- [REST API](./api/README.md)
- [Tool Orchestrator](./agent/README.md)
- [Launcher](./classic-cli/README.md)
- [Engineer Agent](./engineer/README.md)
