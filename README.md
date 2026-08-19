# Gathm Framework

Formerly **Termux-Snippets**.

Gathm is a modular, local-first command intelligence framework for security, networking, and operator workflows. It combines:

- A large tool catalog (53 tools in this branch)
- An orchestration layer (`gathm`) with planning, health, retry, recovery, and caching
- Multiple user interfaces (CLI, TUI, API server, GUI)
- Cross-platform support (Linux, macOS, Termux, WSL/Git Bash/MSYS2)

## Documentation

- [Documentation Index](./docs/README.md)
- [Interfaces Overview](./docs/interfaces/README.md)
- [Examples](./docs/examples/README.md)
- [Use Cases](./docs/use-cases/README.md)

## Why Gathm

Gathm is built for practical operations work:

- Run one tool quickly
- Ask in natural language and route to the right tool
- Chain or parallelize workflows
- Monitor health and auto-heal common failures
- Expose capabilities over HTTP for automation/integration

## Architecture

```text
gathm/
├── gathm                      # Interactive launcher (dialog menu)
├── agent/
│   ├── orchestrator.sh        # Main control plane (run/ask/plan/chain/parallel/health)
│   └── planner.sh             # Task decomposition planner
├── lib/
│   ├── logging.bash           # Structured logs, audit, metrics
│   ├── health.bash            # Health checks + circuit breaker
│   ├── recovery.bash          # Retry, fallback, self-heal
│   ├── cache.bash             # Output caching with TTL
│   ├── ratelimit.bash         # Per-tool rate limiting
│   └── schema.bash            # JSON helpers + manifest parsing
├── tools/                     # 53 tool wrappers/scripts + tool.yaml manifests
├── pilot/                     # Local AI TUI assistant
├── engineer/                  # AutoGen-based engineering agent
├── api/server.py              # REST API server
├── gui/                       # Web chat UI for the API
└── tests/                     # Python regression and integration tests
```

## Installation

Installation is intentionally unified. `./install` detects the environment and applies the correct setup internally.

```bash
git clone https://github.com/hakxcore/gathm.git
cd gathm
./install
```

### Verify Install

```bash
./install --check
```

### Uninstall

```bash
./install --uninstall
```

After setup, restart shell or source your shell rc file (`~/.bashrc` / `~/.zshrc`).

### Voice on Termux (Pilot speech)

On Termux, `./install` builds [audio.cpp](https://github.com/0xShug0/audio.cpp)
— a native C++/ggml speech runtime — and installs it to
`~/.local/bin/audiocpp_cli`. No Python audio packages are involved, which is the
point: the usual speech wheels cannot load on Android at all.

This step is **Termux-only**. Other platforms are untouched and keep whatever
Python/audio dependencies they already use.

The install does four things:

1. Installs the build toolchain (`clang cmake ninja`, plus `openmp`).
2. Clones audio.cpp and patches the vendored `sentencepiece` with
   `-U__ANDROID__`. Without that patch the build compiles for 20-60 minutes and
   then dies at the final link with
   `ld.lld: error: undefined symbol: __android_log_write`.
3. Builds the `audiocpp_cli` target, CPU-only, with just the `pocket_tts`
   family compiled in. Compile parallelism is capped by available RAM, since
   concurrent clang processes otherwise get OOM-killed partway through.
4. Downloads the `pocket_tts_english_q8_0` voice package with the project's own
   model manager, then synthesizes a throwaway WAV to prove the chain works.

Expect 20-60 minutes for the build on a phone. Everything is skipped on re-runs
once installed.

Once installed you can speak a phrase directly:

```bash
audiocpp_cli \
  --task tts \
  --family pocket_tts \
  --model ~/.gathm/audio.cpp/models/PocketTTS-GGUF/english \
  --backend cpu \
  --voice-id alba \
  --text "Hello from Gathm, running entirely on-device." \
  --out hello.wav \
  --metrics

termux-open hello.wav
```

`alba` is the package's built-in voice, so no reference recording is needed.
For voice cloning, swap `--voice-id alba` for `--voice-ref your.wav`.

#### How Gathm speaks

`lib/speech.py` is the call site: it resolves the runtime and voice, strips
markdown down to prose, synthesizes, and plays the result. Both interfaces use
it, and both degrade to text silently when the runtime is missing — speech is
never on the critical path of an answer.

- **Pilot (TUI)** speaks every reply from `render_response`, in the background,
  so the prompt comes back immediately instead of waiting on synthesis. Asking
  the next question cuts off the answer still being read out.
- **GUI** posts the reply to `POST /api/v1/speech` and plays the returned WAV in
  the browser, so audio comes out of the device you are looking at rather than
  the machine running the server. The speaker button appears only when
  `GET /api/v1/speech/status` reports a working runtime, and the on/off choice
  is remembered. The browser may need one tap on the page before it allows the
  first playback.

Synthesis is only half of speaking — Android ships no command-line audio player,
so `./install` also installs `mpv`. `termux-media-player`, `ffplay`, `play`,
`paplay` and `aplay` are used if already present, or force one with
`GATHM_AUDIO_PLAYER`.

In Pilot:

```text
/speak              runtime, voice, model, player, and whether speech is on
/speak off          silence replies for this session
/speak on           turn them back on
/speak hello there  say a phrase, printing the reason if nothing comes out
```

Useful checks:

```bash
python3 lib/speech.py "hello from gathm"   # speak a phrase end to end
python3 lib/speech.py --check              # runtime / model / player report
audiocpp_cli --list-loaders   # should list: pocket_tts: tts (offline)
./install --check             # reports runtime, loader, voice model, playback
```

Speech options:

| Variable | Purpose |
|---|---|
| `GATHM_SPEAK` | `0` (also `off`/`false`/`no`) keeps Gathm silent |
| `GATHM_SPEAK_MAX_CHARS` | how much of a long reply to read (default 600) |
| `GATHM_SPEAK_TIMEOUT` | seconds allowed for one synthesis (default 180) |
| `GATHM_AUDIO_PLAYER` | force a playback command, e.g. `mpv --no-video` |

Build options:

| Variable | Purpose |
|---|---|
| `GATHM_INSTALL_AUDIO_CPP` | `0` skips the build (fast install, no voice) |
| `GATHM_AUDIOCPP_SKIP_MODEL` | `1` builds the runtime but skips the weights |
| `GATHM_AUDIOCPP_SRC` | clone/build location (default `~/.gathm/audio.cpp`) |
| `GATHM_AUDIOCPP_MODELS` | families to compile, e.g. `pocket_tts,qwen3_asr` |
| `GATHM_AUDIOCPP_MODEL_SET` | `custom` (default), `full`, or `core` |
| `GATHM_AUDIOCPP_JOBS` | compile parallelism (auto-capped by available RAM) |
| `GATHM_AUDIOCPP_FORCE` | `1` rebuilds even if `audiocpp_cli` already exists |

The resolved binary, model directory, family, and voice are written to
`~/.gathm/audiocpp_*` and to `.env` as `GATHM_AUDIOCPP_BIN`,
`GATHM_AUDIOCPP_MODEL`, `GATHM_AUDIOCPP_FAMILY`, and `GATHM_AUDIOCPP_VOICE`.

### LLM model on Termux

Termux uses its own two-step ladder instead of the desktop RAM tiers, because
on Android inference is pure CPU, thermal throttling starts within a minute, and
the low-memory killer reaps the Ollama server mid-response:

| Phone RAM (`MemTotal`) | Model |
|---|---|
| under 11 GB | `gemma3:1b` |
| 11 GB and above | `gemma3:4b` |

The floor is 11 GB rather than 12 because `MemTotal` reports what the kernel can
allocate, not the marketed capacity — a phone sold as 12 GB reads back around
11.0-11.7 GB. A strict 12 GB test would never match one. 11 GB still sits well
clear of an 8 GB phone (~7.2-7.6 GB) and a 10 GB one (~9.3 GB).

An install that previously selected a bigger model is migrated down, and models
already on disk that exceed the tier are ignored rather than reused. Unknown RAM
falls to `gemma3:1b`.

Override with `GATHM_OLLAMA_MODEL` for a specific model, or
`GATHM_TERMUX_LARGE_MIN_RAM_MB` to move the 4b threshold.

## Quick Start

```bash
gathm status
gathm list
gathm ask "weather in Tokyo"
gathm run dns -t MX gmail.com
gathm health all
```

## Main Interfaces

### 1) Agent Orchestrator (primary interface)

```bash
gathm --help
```

Core commands:

- `run <tool> [args]`
- `ask <query>`
- `plan <description>`
- `engineer <task>`
- `chain '<t1 | t2 | t3>'`
- `parallel '<t1, t2, t3>'`
- `new-tool <name> [opts]`
- `list [--json]`
- `health [tool|all]`
- `heal [tool|all]`
- `cache [stats|clear|purge|invalidate <tool>]`
- `status`
- `monitor [interval]`

### 2) Classic Launcher

```bash
gathm
```

Interactive menu launcher for tools (uses `dialog`).

### 3) Pilot TUI (local AI assistant)

```bash
python3 -m venv pilot/.venv
source pilot/.venv/bin/activate
pip install -r pilot/requirements.txt
python3 pilot/main.py
```

Notes:

- Uses Ollama-compatible model runtime.
- Model resolution order: `GATHM_OLLAMA_MODEL` -> `OLLAMA_MODEL` -> `~/.gathm/model` -> default.

### 4) REST API

```bash
python3 api/server.py --host 127.0.0.1 --port 8080
```

Examples:

```bash
curl http://127.0.0.1:8080/api/v1/tools
curl http://127.0.0.1:8080/api/v1/health
curl -X POST http://127.0.0.1:8080/api/v1/agent/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"weather in Paris"}'

# Speech (Termux): returns audio/wav for the client to play
curl http://127.0.0.1:8080/api/v1/speech/status
curl -X POST http://127.0.0.1:8080/api/v1/speech \
  -H "Content-Type: application/json" \
  -d '{"text":"hello from gathm"}' --output reply.wav
```

Enable API auth:

```bash
export GATHM_API_KEY="your-secret"
curl http://127.0.0.1:8080/api/v1/tools \
  -H "Authorization: Bearer your-secret"
```

### 5) GUI (chat-style web client)

Start API server first, then serve the GUI:

```bash
python3 api/server.py --port 8080
cd gui
python3 -m http.server 5173
```

Open `http://127.0.0.1:5173`.

## Tool Catalog

Tool categories from `tools/*/tool.yaml`:

- `security` (19): certinfo, cipher, crypt, cve, dnssec, headersaudit, katana, naabu, nuclei, pdchain, portscan, pwned, robotsaudit, shodan, siteciphers, tipcheck, uncover, urlscan, wafdetect
- `networking` (14): asn, dns, dnsx, geo, httpprobe, httpx, ipinfo, rdap, rdns, shorturl, subdomains, subfinder, transfer, whois
- `data` (8): cheat, covidinfo, cryptocurrency, define, googler, movie, news, weather
- `media` (4): gif, jukebox, lyrics, meme
- `utility` (4): imganalyze, maltego, qrify, strix
- `finance` (2): currency, stocks
- `science` (1): newton
- `productivity` (1): todo

## Security and Reliability Model

Gathm includes platform controls in the orchestrator path:

- Input sanitization for dangerous shell patterns and traversal
- Rate limiting (global + per-tool policies)
- Circuit breaker for repeated failures
- Retry with exponential backoff
- Fallback tool chaining from manifests
- Output cache with TTL
- Structured logs, audit logs, and metrics

State/log paths (default):

- `~/.gathm/logs`
- `~/.gathm/health`
- `~/.gathm/cache`
- `~/.gathm/agent`

## Configuration

Main config files:

- `config/agent.yaml`
- `config/policies.yaml`
- `config/tools.yaml`

Key environment variables:

- `GATHM_LOG_LEVEL` (`DEBUG|INFO|WARN|ERROR`)
- `GATHM_OUTPUT_MODE` (`text|json`)
- `GATHM_MAX_RETRIES`
- `GATHM_CACHE_ENABLED`
- `GATHM_CACHE_DEFAULT_TTL`
- `GATHM_API_KEY` (API bearer auth)
- `GATHM_OLLAMA_MODEL` / `OLLAMA_MODEL` (Pilot/Engineer model selection)
- `GATHM_AUDIOCPP_BIN` / `GATHM_AUDIOCPP_MODEL` / `GATHM_AUDIOCPP_FAMILY` / `GATHM_AUDIOCPP_VOICE`
  (audio.cpp speech runtime on Termux; see [Voice on Termux](#voice-on-termux-pilot-speech))
- `GATHM_SPEAK` / `GATHM_SPEAK_MAX_CHARS` / `GATHM_SPEAK_TIMEOUT` / `GATHM_AUDIO_PLAYER`
  (spoken replies)
- `OMDB_API_KEY` (movie tool)
- `VT_API_KEY` / `VIRUSTOTAL_API_KEY` (tipcheck VirusTotal)
- `ABUSEIPDB_API_KEY` (tipcheck AbuseIPDB)
- `SHODAN_API_KEY` (shodan CLI module usage)

## Docker

Build and run:

```bash
docker build -t gathm:local .
docker run --rm -p 8080:8080 gathm:local
```

Or compose:

```bash
docker compose up --build
```

## Development

### Run tests

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest tests -v
```

Fallback:

```bash
python3 -m unittest discover -s tests -v
```

### Scaffold a new tool

```bash
gathm new-tool mytool --category utility --description "My custom tool"
gathm run mytool --help
```

Generated files:

- `tools/mytool/mytool`
- `tools/mytool/tool.yaml`

## Notes and Compatibility

- Some tools depend on external services whose APIs can change over time.
- ProjectDiscovery wrappers require the corresponding binaries installed (`subfinder`, `dnsx`, `httpx`, `naabu`, `katana`, `nuclei`, `uncover`).
- `shodan` tool requires Python `shodan` module (`pip install shodan`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

GNU GPL v3.0. See [LICENSE](LICENSE).
