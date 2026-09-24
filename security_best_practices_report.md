# Gathm cleanup and security review

Date: 24 September 2026. Base revision: `8634e3b`, branch
`claude/llama-cpp-integration-6bnsox`, including the local performance work.

## Executive summary

The review found and fixed high-severity weaknesses in shell approval,
unauthenticated API access, API permission enforcement, and Docker build
contents. It also fixed job access, unsafe browser dispatch, exposed terminal
sharing state, and several resource/transport problems. Changes are local;
nothing has been merged, pushed, or deployed to the phone during this pass.

The existing Termux speed improvements and benchmark evidence are retained.
This is a repository-wide static review plus targeted security testing, not a
penetration-test certification. Remaining work concerns download provenance,
browser/process isolation, and some legacy transport and lint warnings.

## Scope and evidence

Reviewed tracked source/configuration across `api`, `lib`, `agent`, `pilot`,
`engineer`, `gathmcli`, all tool directories, GUI JavaScript, the installer,
packaging, CI, and container configuration. Binary documentation assets and
local virtual-environment implementation code were excluded from source
analysis. Manual review focused on trust boundaries, command construction,
permissions, process execution, network/URL handling, secrets, and file writes.
Embedded Python in shell tools was inspected separately because Bandit does not
scan it as Python source.

- Bandit: 15 Python source files; 66 low, 8 medium, 1 high raw findings.
- ShellCheck: 74 shell files including the new access helper; no syntax errors
  or ShellCheck error-level findings. 55 warnings remain, in SC2155, SC2220 and
  SC2034 (assignment status masking, missing option cases, unused variables).
- pip-audit: 58 installed Python packages, zero known advisories returned.
  This does not cover absent optional Engineer dependencies, native model
  runtimes, Go tools, OS packages, or dependencies on the phone.
- Credential-pattern scan: no matching live-secret patterns in tracked text.
  Ignored `.env` contents and Git history were not scanned or disclosed.
- [Recorded scanner findings and package versions](docs/security/scan-results-2026-09-24.json).

The raw Bandit high finding is `os.system()` with a constant clear-screen
command in `pilot/tui.py:268`, not user input. Its medium bind warning is a
wildcard-to-loopback display conversion in `lib/llamacpp.py:418`; the shell
warning in Pilot is a string-format argument. URL-opening warnings concern
operator-configured model endpoints and the benchmark client. These are
triaged findings, not a claim that the scanner produced a clean report.

## High severity — fixed

### SEC-01: Local API accessible to hostile browser origins

Previously, wildcard CORS combined with the default unauthenticated admin role
allowed websites to reach powerful local API operations. Non-loopback binds
also lacked a requirement for authentication.

The API now rejects cross-origin/cross-site browser requests and requires both
a loopback peer and loopback Host when no key is configured. Host checks address
DNS rebinding. Configured-key remote callers must authenticate. Forwarded
client headers are disabled in the supplied launcher. Health checks now require
permission; `/api/v1/ping` remains the inexpensive public probe.

Evidence/fix: `api/server.py:177`, `api/server.py:767`.
Tests: origin, null-origin, cross-site, remote-peer, hostile Host, and
unauthenticated health requests are rejected; local same-origin and
key-authenticated requests work.

### SEC-02: “Read-only” shell commands could execute code or write files

The former allowlist accepted script files passed to Python/Node, writable
utilities, arbitrary executables with a familiar basename, and broad
PowerShell verb prefixes. This bypassed confirmation when system control was
enabled.

Approval now depends on conservative command/argument combinations. Script
interpreters, Git/package managers, programmable text utilities, arbitrary
paths, mutating flags, and PowerShell expressions require confirmation. Safe
POSIX execution preserves tilde expansion while bypassing shell functions and
startup files. The operator's installed executables/PATH remain trusted.

Evidence/fix: `lib/sysexec.py:79`, `lib/sysexec.py:96`, `lib/sysexec.py:370`,
`lib/sysexec.py:431`. Tests verify malicious classifications and that a denied
script never reaches process creation.

### SEC-03: API permissions could be bypassed through indirect execution

Tool approval/block checks applied only to direct endpoints. Chat, pipelines,
fallbacks, and jobs could select another tool. Job kinds accepted arbitrary
orchestrator commands. Engineer could execute generated host code with the
same permission as an ordinary tool.

Workers now receive a positive tool allowlist, enforced at Pilot dispatch,
orchestration/cache access, recovery/fallback execution, sibling tool startup,
and ProjectDiscovery pipeline stages. Built-in system/browser access requires
explicit capability, and explicit blocks/approval requirements override that capability. Engineer and heal require administrator permission.
Job kinds and timeouts are validated. API workers are non-interactive, cannot
auto-install through recovery, and do not inherit API authentication keys.
Default restricted roles require approval for terminal sharing and transfers.

Evidence/fix: `api/server.py:732`, `api/server.py:979`, `api/server.py:1018`,
`lib/access.bash:4`, `lib/utils.bash:3`, `pilot/main.py:487`,
`agent/orchestrator.sh:154`, `tools/pdchain/pdchain:58`, `config/policies.yaml:11`.
Tests cover inherited policy, denied shell/browser dispatch, blocked fallback,
invalid paths, invalid job kinds, and captured job permissions.

### SEC-04: Docker builds could include credentials and local environments

`COPY . .` had no `.dockerignore`; a local `.env` and virtual environments could
enter images and build contexts. Added exclusions. Docker now installs the
actual API requirements and fails if installation fails. Compose requires an
API key, publishes on host loopback, and mounts the non-root user's data path.

Evidence/fix: `.dockerignore:1`, `Dockerfile:33`, `docker-compose.yaml:7`.
Container configuration was reviewed statically; an image build was not run.

## Medium severity — fixed

### SEC-05: Jobs lacked ownership checks

Any token with discovery permission could read another token's job output;
execution permission could cancel another user's work. Job access now checks a
hash of the submitting bearer token, with an administrator override. List,
read, stream and cancellation are covered. Stored job files/directories are
private to the account running Gathm.

Evidence: `api/server.py:534`, `api/server.py:758`, `api/server.py:1039`.

### SEC-06: Unbounded inputs, job queue and process output

Requests could supply invalid timeouts/arbitrary job commands, uploads were
checked after allocation, and job/output storage could grow without limits.
Added body limits before parsing (including chunked requests), bounded input
fields/timeouts, a four-active-job/200-record queue limit, and output limits for
async workers. Counts use received bytes before stripping whitespace, with a
separate retained-line cap. POSIX cancellation kills the worker process group; readers are
cancelled and pipes drained. This is not a complete per-user resource sandbox:
chat still uses its own subprocess path and jobs/rate limits are per process.

Evidence: `api/server.py:299`, `api/server.py:324`, `api/server.py:655`,
`api/server.py:676`. Upload and output-overflow regression tests pass.

### SEC-07: GUI executed an unpinned third-party script

The local privileged GUI loaded `lucide@latest` from a CDN. Replaced that script
with a small local SVG renderer and added a GUI Content Security Policy,
frame protection, no-referrer and nosniff headers. Existing text/Markdown
rendering uses text nodes and checked link protocols; no new DOM XSS issue was
confirmed in that renderer.

Evidence: `gui/index.html:10`, `gui/icons.js:1`, `api/server.py:808`.

### SEC-08: Browser URLs reached OS command dispatch

Windows URL opening used `cmd /c start`, and browser helpers lacked consistent
scheme checks. They now accept HTTP/HTTPS only, use Windows native URL opening,
and restrict curl redirect protocols. WSL uses `wslview`/`xdg-open` rather than
re-parsing a URL through cmd.exe. Private-network URLs remain intentional.

Evidence: `pilot/browser.py:375`. Scheme-rejection tests cover open, fetch,
navigation and screenshot helpers. Windows/WSL execution was not tested on
native hosts.

### SEC-09: Shared terminal credentials used predictable global temporary files

Moved socket/PID/log state from shared `/tmp` paths to a private user directory
with restrictive permissions. Replaced interpolated shell execution with
argument arrays. Standard API roles cannot launch terminal sharing.

Evidence: `tools/shareterminal/shareterminal:127`, `config/policies.yaml:19`.

### SEC-10: Credential/file transport and unsafe download destinations

Movie API requests previously sent API keys over HTTP; QR decoding uploaded
files over HTTP. Those now use HTTPS, as does the TinyURL API. File transfers
reject traversal and refuse overwrites through atomic destination creation;
failed downloads return an error. Installer dependency recovery no longer
executes command text through `eval`.

Evidence: `tools/movie/movie:21`, `tools/qrify/qrify:48`,
`tools/transfer/transfer:38`, `lib/recovery.bash:253`.
HTTPS availability is documented by [OMDb](https://www.omdbapi.com/).

### SEC-11: Obsolete GIF updater could delete a user's checkout

The legacy GIF script carried duplicate platform/client code and an updater
that recursively deleted a home-directory `termux-snippets` checkout. Replaced
it with the shared utility layer and bounded HTTPS fetching. Updating now
points the operator to Gathm's installer.

Evidence: `tools/gif/gif:1`. Cache purge also now rejects an empty/root cache
path (`agent/orchestrator.sh:956`).

## Remaining hardening work

### SEC-12 — Medium: Download provenance is not consistently pinned

The llama.cpp installer downloads and runs HTTPS release assets without an
independently pinned digest (`install:1349`); other installer/tool binary paths
also trust upstream releases. Custom audio binary digests are optional
(`install:2831`). Add verified release checksums/signatures and version-pinned
artifacts. No malicious download was observed; this is a supply-chain trust
limit, and changing supported installers needs its own compatibility testing.

### SEC-13 — Medium: Tool/browser isolation remains limited

Permitted tools run as the Gathm account and may access that account's files or
network. The Engineer deliberately executes generated code; the Termux browser
starts Chromium with `--no-sandbox` (`pilot/browser.py:268`). API roles and shell
confirmation are not substitutes for OS isolation. Keep the service local or
restricted to trusted operators until isolated workers/browser execution and
per-user quotas are implemented. Native Windows descendant-process cleanup also
needs platform-specific verification.

### SEC-14 — Low: Legacy public-data HTTP fallbacks remain

`tools/geo/geo:79` uses the provider's HTTP geolocation endpoint;
`tools/news/news:10` defaults to an HTTP feed. These carry public lookup/feed
data, not the movie API key fixed above, but a network attacker can alter
results. Replace with verified HTTPS providers/endpoints and test output
compatibility in a follow-up. No insecure replacement provider was selected
without verifying its response contract.

## Cleanup and validation

Removed `tools/cheat/new.sh` (unreferenced COVID script),
`tools/meme/my_meme.jpg` (generated output), and the empty stray `=` file.
Removed the legacy no-op API handler, shadowed host/port defaults, unused MIME
mapping, duplicate imports and dead natural-language argument helper. Retained
README assets, benchmarks, and the prior performance patch.

Passing local checks:

| Suite | Result |
| --- | --- |
| Core/discovery/tool regression tests | 74 tests |
| New security regression suite | 17 tests |
| Shell command approval | 344 assertions |
| Packaging | 98 assertions |
| macOS speech / speech stream / reply stream | 135 / 49 / 40 assertions |
| llama.cpp runtime / installer | 63 / 53 assertions |
| Launcher | 61 assertions |
| Markdown / speech chunking / voice activity JS | 104 / 40 / 32 assertions |
| Browser conversation flow | 22 assertions |
| Actual API + GUI smoke | HTTP 200; 4 local icons; no JS/CSP errors |
| Shell syntax / JS syntax / patch whitespace | Passed |

Model/launcher tests used local stub servers. Browser tests used an isolated
headless Chrome profile. Several old tests were corrected to include built-in
tools and simulate missing dependencies independently of host-installed
llama.cpp or Linux package managers. No destructive scan or live target attack
was performed. Full dependency coverage on Termux, native Windows testing,
container image execution, and production multi-user isolation remain outside
this pass.

Guidance consulted: [Starlette middleware](https://www.starlette.io/middleware/),
[Bandit](https://bandit.readthedocs.io/en/latest/man/bandit.html),
[pip-audit](https://github.com/pypa/pip-audit), and the local
`security-best-practices` skill's Python/FastAPI and browser JavaScript guidance.
Bash was reviewed manually and with ShellCheck; that skill provides no Bash
framework-specific guide.
