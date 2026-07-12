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
