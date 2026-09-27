# Gathm in the Terminal (`pilot/`)

Gathm's terminal assistant supports conversation, writing, planning, and
learning, with voice input and spoken replies where available. Tools are
available when a request needs current information or an action. `pilot/`
is the implementation directory; the assistant you talk to is Gathm.

## Start

```bash
gathm tui
```

From a checkout, run `./gathm tui`. The launcher checks dependencies and the
configured model. `gathm` opens both the terminal assistant and browser GUI.

For a manual development environment from the repository root:

```bash
python3 -m venv pilot/venv
source pilot/venv/bin/activate
python -m pip install -e .
./gathm tui
```

The base Python package provides the terminal assistant. Native model and
speech runtimes are configured separately by `gathm setup` or `./install`;
see [installation](../../../README.md#installation).

## Conversation and Voice

Type a message to start, then ask follow-up questions in the same session.
Gathm keeps recent conversation context in memory while the terminal is open.
Use `/listen` for a voice message and `/speak` to inspect speech availability.
Neither voice input nor a model is required to use direct tool commands
outside the assistant, such as `gathm run dns example.com`.

| Command | Action |
|---|---|
| `/listen` | Record and transcribe a voice message, then send it to Gathm |
| `/listen 5` | Request a five-second recording where supported |
| `/speak` | Show speech configuration and availability |
| `/speak on` / `/speak off` | Enable or mute spoken replies for this session |
| `/speak hello` | Try a spoken phrase |
| `/model` | Show the selected backend and model |
| `/tools` | Explore available tools |
| `/clear` | Redraw the welcome screen; keep the current conversation context |
| `/help` or `?` | Show the command guide |
| `/quit` or `/exit` | Leave Gathm |

Restart the terminal assistant for a fresh conversation. The terminal session
and browser chat have separate histories.

## Model Configuration

Both interfaces use the shared configuration in `lib/llm.py`. Supported
backends are `llamacpp`, `ollama`, `gemini`, and `anthropic`.

Backend selection follows this order:

1. Explicit `GATHM_LLM_BACKEND`.
2. An `ANTHROPIC_API_KEY`, then `GOOGLE_API_KEY` or `GEMINI_API_KEY`.
3. The saved `~/.gathm/llm_backend` setting.
4. A usable llama.cpp installation or running server; otherwise Ollama.

Set `GATHM_LLM_BACKEND=llamacpp` to explicitly select your local GGUF setup.
Its model is resolved from `GATHM_LLAMACPP_MODEL`, the saved
`~/.gathm/llamacpp_model` path, or the configured/default model directories.
An old Ollama model tag does not replace those weights.

For other backends, model selection uses the backend-specific model variable
(such as `GATHM_OLLAMA_MODEL` or `OLLAMA_MODEL`), then `GATHM_MODEL`, then
`~/.gathm/model`, then that backend's default. `GATHM_CONFIG_DIR` changes the
configuration directory from `~/.gathm`. Use `/model` to confirm the result.

## Troubleshooting

- Run `gathm doctor` to check dependencies and model configuration.
- For llama.cpp, use `gathm llm status`, `gathm llm start`, and `gathm llm log`.
- For Ollama, make sure its server is running and the selected model is installed.
- Use `/speak` or `python3 lib/speech.py --check` for speech availability.
- If an optional tool fails, check its own dependencies and reported error.
