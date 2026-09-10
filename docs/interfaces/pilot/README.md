# Pilot TUI (`pilot/main.py`)

Pilot is Gathm's local AI terminal assistant. It reasons over available tools and executes them through text-based tool actions.

## Setup

```bash
python3 -m venv pilot/.venv
source pilot/.venv/bin/activate
pip install -r pilot/requirements.txt
```

## Run

```bash
python3 pilot/main.py
```

## Model Selection

Pilot resolves model in this order:

1. `GATHM_OLLAMA_MODEL`
2. `OLLAMA_MODEL`
3. `~/.gathm/model`
4. default (`gemma3:12b`)

## Features

- Discovers current tools dynamically from `tools/`
- Normalizes common command mistakes before execution
- Maintains recent conversation context
- Includes high-risk query refusal for exposed-infrastructure discovery prompts

## Built-in Slash Commands

- `/help`
- `/tools`
- `/clear`
- `/model`
- `/quit`

## Troubleshooting

- Missing runtime libs: install `pilot/requirements.txt`
- Model/API issues: check the local model server with `gathm llm status`
  (`gathm llm start` loads it; `gathm llm log` says why it would not),
  or confirm the Ollama model is pulled when that is the backend
- Tool execution failures: check stderr and tool dependencies
