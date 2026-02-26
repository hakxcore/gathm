# Engineer Interface (`engineer/`)

The Engineer interface is an AI-assisted code and tooling workflow, callable through `gathm engineer`.

## Run via Orchestrator

```bash
gathm engineer "create a new tool called mytool"
```

Direct entry:

```bash
python3 engineer/main.py "your engineering task"
```

## Setup

```bash
python3 -m venv engineer/.venv
source engineer/.venv/bin/activate
pip install -r engineer/requirements.txt
```

## Model Behavior

- Prefers Anthropic client if `ANTHROPIC_API_KEY` is set.
- Falls back to local Ollama-compatible endpoint otherwise.

Relevant variables:

- `ANTHROPIC_API_KEY`
- `GATHM_OLLAMA_MODEL` / `OLLAMA_MODEL`
- `OLLAMA_BASE_URL`

## Notes

- This interface is intended for maintenance/dev workflows, not routine tool execution.
- Use with a clean, reviewable git workflow and run tests after generated changes.
