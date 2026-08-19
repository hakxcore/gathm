# Gathm Framework

Formerly **Termux-Snippets**.

Gathm is a modular, local-first command intelligence framework for security, networking, and operator workflows. It combines:

- A large tool catalog (55 tools in this branch)
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
├── tools/                     # 55 tool wrappers/scripts + tool.yaml manifests
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

This step is **Termux-only**, because Android is the platform with no built-in
speech at all. Elsewhere Gathm speaks through the OS instead of building
anything: `say` on macOS, `spd-say` or `espeak-ng` on Linux. audio.cpp wins when
it is installed; otherwise the system voice is used, so `python3 lib/speech.py
"hello"` and spoken Pilot replies work on macOS with no setup. Override the
command with `GATHM_SPEAK_COMMAND`. Voice *input* remains audio.cpp-only, so it
is Termux-only for now.

`python3 lib/speech.py --check` reports which engine is in use.

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

Expect 20-60 minutes for the build on a phone — **once**. After that the
installer avoids compiling entirely:

1. If `GATHM_AUDIOCPP_BIN_URL` is set, it downloads that binary (verifying
   `GATHM_AUDIOCPP_BIN_SHA256` when given).
2. Otherwise it reuses the cached build in `~/.cache/gathm/audiocpp/`, kept
   outside `~/.gathm` so `--uninstall` does not throw it away.
3. Only if neither applies does it configure and compile.

Both shortcuts are verified by actually running `--list-loaders` before being
installed, so a stale, truncated, or wrong-architecture file falls through to a
source build instead of leaving you with a binary that cannot speak.

The cache key is `audiocpp_cli-<arch>-<families>`, so a build made for
`pocket_tts` alone is not reused for a config that also wants `sense_asr`.

**Reusing one build across devices.** Build once, then publish the binary and
point every other install at it:

```bash
# on the device that already built it
cp ~/.local/bin/audiocpp_cli audiocpp_cli-aarch64
sha256sum audiocpp_cli-aarch64          # note the hash
# attach the file to a GitHub release, then on every other device:
GATHM_AUDIOCPP_BIN_URL=https://github.com/<you>/<repo>/releases/download/v1/audiocpp_cli-aarch64 \
GATHM_AUDIOCPP_BIN_SHA256=<hash> ./install
```

What this cannot do is give you one binary for everything. `audiocpp_cli` is a
native executable linked against Termux's `libc++_shared.so`, so a build is
specific to the OS *and* the CPU architecture: a Termux aarch64 binary will not
run on glibc Linux, macOS, or a 32-bit phone, and each target needs its own
build and its own URL. Check audio.cpp's own license before redistributing a
binary built from it.

`GATHM_AUDIOCPP_FORCE_BUILD=1` skips both shortcuts and compiles from source.

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
  the machine running the server. This works with either engine: audio.cpp on
  Termux, `say` or `espeak-ng` elsewhere. `spd-say` can speak but cannot write a
  file, so on a machine where it is the only voice the GUI stays text-only while
  Pilot still talks. The speaker button appears only when
  `GET /api/v1/speech/status` reports a working runtime, and the on/off choice
  is remembered. The browser may need one tap on the page before it allows the
  first playback.

Synthesis is only half of speaking — Android ships no command-line audio player,
so `./install` also installs `mpv`. `termux-media-player`, `ffplay`, `play`,
`paplay` and `aplay` are used if already present, or force one with
`GATHM_AUDIO_PLAYER`.

#### How Gathm listens

The same binary transcribes (`--task asr`), so voice input uses the same
install. The compiled ASR family is **`sense_asr`** — SenseVoice-Small, ~250 MB
at q8_0, 23 languages with auto-detection, offline. The Qwen3 ASR models are the
obvious alternative and are 3-7x larger (0.6B ≈ 700 MB, 1.7B ≈ 1.8 GB) for
nothing Gathm needs, which is a bad trade on a phone already hosting the LLM.

Listening is deliberately **not** a tool the model can call: the agent needs the
words before it can decide anything, so transcription happens at the input edge,
ahead of the reasoning graph.

- **Pilot:** `/listen` records from the microphone and asks what you said.
  `/listen 5` sets the length (default 8s, or `GATHM_LISTEN_SECONDS`).
- **GUI:** the mic button now records as well as animating. Tap it, speak, tap
  again — the transcript lands in the input box and sends. The browser encodes
  16 kHz mono WAV itself, so no server-side conversion is involved.
- **Anywhere:** `gathm run transcribe recording.wav` turns a file into text, and
  is the one speech capability the agent *can* invoke, because "transcribe this
  file" is a genuine request with a file argument.

Recordings are written under `~/.gathm/tmp`, not `$TMPDIR`: on Termux the
capture is performed by the separate Termux:API app, which cannot reliably write
into Termux's private `usr/tmp` — the file simply never appears.

Voice input needs two things beyond the runtime, both installed by `./install`:
`termux-api` for `termux-microphone-record` (**plus the Termux:API app from
F-Droid**, which an installer cannot do for you) and `ffmpeg`, because the
recorder encodes AAC and the models want 16 kHz WAV.

`audiocpp_cli` resolves its model contract specs (`model_specs/<family>.json`)
relative to the working directory, so Gathm runs it from the audio.cpp checkout.
Run it by hand from elsewhere and a family whose spec is not embedded in its
GGUF fails with `model contract spec not found for family 'sense_asr'` — either
`cd` into the checkout first, or rebuild, since new builds pass
`-DAUDIOCPP_DEPLOYMENT_BUILD=ON` to compile the specs into the binary.

Upgrading an install built before `sense_asr` was compiled in:

```bash
GATHM_AUDIOCPP_MODELS=pocket_tts,sense_asr GATHM_AUDIOCPP_FORCE=1 ./install
```

That reuses the existing build directory, so it compiles the new family rather
than starting over. `./install --check` says plainly whether the loader, the
weights, the recorder and the converter are each present.

In Pilot:

```text
/speak              runtime, voice, model, player, and whether speech is on
/speak off          silence replies for this session
/speak on           turn them back on
/speak hello there  say a phrase, printing the reason if nothing comes out
/listen             record, transcribe, and ask what you said
/listen 5           the same, for 5 seconds
```

Useful checks:

```bash
python3 lib/speech.py "hello from gathm"    # speak a phrase end to end
python3 lib/speech.py --listen 5            # record, then print what you said
python3 lib/speech.py --transcribe clip.wav # transcribe a file
python3 lib/speech.py --check              # runtime / model / player / mic report
audiocpp_cli --list-loaders   # should list pocket_tts: tts and sense_asr: asr
./install --check             # reports every piece of both directions
```

Speech options:

| Variable | Purpose |
|---|---|
| `GATHM_SPEAK` | `0` (also `off`/`false`/`no`) keeps Gathm silent |
| `GATHM_SPEAK_MAX_CHARS` | how much of a long reply to read (default 600) |
| `GATHM_SPEAK_TIMEOUT` | seconds allowed for one synthesis (default 180) |
| `GATHM_AUDIO_PLAYER` | force a playback command, e.g. `mpv --no-video` |
| `GATHM_SPEAK_COMMAND` | force the OS speech command, e.g. `say -v Daniel` |
| `GATHM_LISTEN_SECONDS` | default recording length (default 8) |
| `GATHM_ASR_TIMEOUT` | seconds allowed for one transcription (default 300) |
| `GATHM_AUDIO_RECORDER` | force a recording command |
| `GATHM_OBS_MAX_CHARS` | cap on tool output shown to the model (default 1500, `0` = no cap) |
| `GATHM_AUDIOCPP_ASR_PACKAGE` | ASR weights to install (default `sensevoice_small_q8`) |
| `GATHM_AUDIOCPP_ASR_FAMILY` | ASR family to use (default `sense_asr`) |
| `GATHM_AUDIOCPP_SKIP_ASR_MODEL` | `1` builds the loader but skips the weights |

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
| `GATHM_AUDIOCPP_BIN_URL` | download a prebuilt `audiocpp_cli` instead of compiling |
| `GATHM_AUDIOCPP_BIN_SHA256` | expected sha256 of that download |
| `GATHM_AUDIOCPP_CACHE` | where built binaries are cached (default `~/.cache/gathm/audiocpp`) |
| `GATHM_AUDIOCPP_FORCE_BUILD` | `1` ignores the cache and any URL, and compiles |

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

## Response Speed

Gathm is slower per answer than talking to the model directly with
`ollama run gemma3:1b`, and most of the gap is prompt size rather than the model:
the agent has to describe its tools before the model can choose one.

| | tool text sent to the model |
|---|---|
| `ollama run gemma3:1b` | none |
| Gathm, before narrowing | ~5.3 KB / ~1,300 tokens, every turn |
| Gathm, a routed question ("weather in Delhi") | ~85 bytes / ~21 tokens |
| Gathm, a vague question | ~2 KB / ~500 tokens |
| Gathm, "what can you do" | the full catalogue, deliberately |

On CPU-only hardware that prefill is the wait. Three things reduce it:

- **Per-query tool narrowing.** Only tools scoring against the question are
  listed, ranked by name, description, and manifest tags, with prefix matching so
  "derivative" still finds `newton` and "registered" still finds `whois`. Asking
  what Gathm can do still lists everything. `GATHM_TOOL_SHORTLIST=0` restores the
  old behaviour; the default is 10 tools.
- **The model stays loaded.** Ollama unloads an idle model after 5 minutes by
  default, so the next question paid a full reload. Gathm now asks for
  `keep_alive=30m` — tune with `GATHM_OLLAMA_KEEP_ALIVE` (`-1` never unloads,
  `0` unloads immediately, at the cost of RAM).
- **Small talk skips tools entirely.** A greeting is answered from a short
  prompt with no tool list at all.
- **Tool output is stripped before the model reads it.** `weather` alone returns
  ~3.5 KB of box-drawing and ANSI escapes; the model now gets the text without
  the art, capped by `GATHM_OBS_MAX_CHARS`.

What remains, and is not yet optimized: the API server starts a fresh Python
process per turn (`pilot/chat_once.py`), so the GUI pays LangChain's import cost
on every message — noticeable on a phone, and worth a persistent worker later.

| Variable | Purpose |
|---|---|
| `GATHM_TOOL_SHORTLIST` | tools listed per question (default 10, `0` = all) |
| `GATHM_OLLAMA_KEEP_ALIVE` | how long Ollama keeps the model resident (default `30m`) |
| `GATHM_OLLAMA_NUM_PREDICT` | cap on generated tokens (unset by default) |
| `GATHM_OLLAMA_NUM_CTX` | context window override (unset by default) |
| `GATHM_CHAT_TIMEOUT` | seconds the API waits for one agent turn (default 600) |

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

# Transcription: raw audio bytes in, {"text": ...} out
curl http://127.0.0.1:8080/api/v1/transcribe/status
curl -X POST http://127.0.0.1:8080/api/v1/transcribe \
  -H "Content-Type: audio/wav" --data-binary @recording.wav
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

The chat is kept in `localStorage`, so closing the browser or letting Android
reclaim the tab no longer loses the conversation or the model's context; type
`/clear` to start fresh. The input box grows with a long prompt (Enter sends,
Shift+Enter adds a newline), and the mic button shows a recording indicator with
an elapsed timer while it captures.

If the server refuses to start with `address already in use`, an older Gathm
API server is still on that port — and it is the one the browser talks to, so
pulling new code appears to change nothing. Stop it with
`lsof -ti:8080 | xargs kill` (the server now says this instead of a bare
`[Errno 48]`).

**Voice input needs a secure origin.** Browsers only expose a microphone to
`https://` pages or to `localhost`/`127.0.0.1`. Opening the GUI on the phone's
LAN address gives no microphone at all — the page says so rather than appearing
to record. Use `http://127.0.0.1:8080` on the device itself.

## Tool Catalog

Tool categories from `tools/*/tool.yaml`:

- `security` (19): certinfo, cipher, crypt, cve, dnssec, headersaudit, katana, naabu, nuclei, pdchain, portscan, pwned, robotsaudit, shodan, siteciphers, tipcheck, uncover, urlscan, wafdetect
- `networking` (15): asn, dns, dnsx, geo, httpprobe, httpx, ipinfo, rdap, rdns, shareterminal, shorturl, subdomains, subfinder, transfer, whois
- `data` (8): cheat, covidinfo, cryptocurrency, define, googler, movie, news, weather
- `media` (5): gif, jukebox, lyrics, meme, transcribe
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
- `GATHM_LISTEN_SECONDS` / `GATHM_ASR_TIMEOUT` / `GATHM_AUDIO_RECORDER`
  / `GATHM_AUDIOCPP_ASR_MODEL` / `GATHM_AUDIOCPP_ASR_FAMILY` (voice input)
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
