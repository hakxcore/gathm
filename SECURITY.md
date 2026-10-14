# Security model

Gathm is a local operator tool. Its tools, model runtime, repository, executable
search path, and configuration are trusted. Tool permissions restrict which
Gathm tools an API client can invoke; they do **not** sandbox a permitted tool's
filesystem or network access. Administrator access includes the engineering
agent, which can execute generated code as the account running Gathm.

## API access

With no API key configured, only loopback clients using a loopback Host are
accepted. The local GUI can continue using `http://127.0.0.1:8080`. Browser
requests must come from the same origin. `/api/v1/ping` is the inexpensive public
liveness endpoint; `/api/v1/health` requires health-check permission.

For remote clients or a reverse proxy, configure `GATHM_API_KEY` (administrator)
or `GATHM_API_KEYS` (`token:role,token:role`). Generate long random tokens. Protect
remote traffic with HTTPS at a trusted reverse proxy or an SSH tunnel. Do not
place an unauthenticated local API behind a proxy. The supplied launcher ignores
forwarded client headers; custom uvicorn deployments should use
`--no-proxy-headers` unless they explicitly configure a trusted proxy.

The GUI currently has no API-key sign-in screen. It supports the local mode;
key-protected deployments require an HTTP client that sends the bearer token.
Arbitrary cross-origin GUI hosting is no longer enabled.

Roles are defined in `config/policies.yaml`. Restrictions follow API requests
through chat, asynchronous jobs, orchestration, fallbacks, and sibling tools.
Engineering and self-healing require administrator access. System commands and
browser control require `system:execute` and `browser:execute`, respectively
(administrators have both). Tools marked `requires_approval` are denied through
the API because there is no interactive approval channel. The standard user
and agent roles cannot start terminal sharing or file transfers. Jobs are
visible/cancellable only to their submitting token or an administrator.

Run one API worker: job ownership and rate limiting are currently in memory.
API worker subprocesses do not inherit the API authentication keys. Other
provider credentials remain available to the tools that need them.

## Shell commands and browsing

Typed system controls require the `system:control` capability through the API.
Changes such as moving files, stopping processes, restarting, or shutting down
require confirmation of the specific proposed action. API confirmations use
short-lived, one-use tickets bound to the requesting client; the action and its
target are checked again before execution. Model output cannot approve an action.

Arbitrary shell command execution remains disabled unless explicitly enabled by the
operator. Commands whose binary and arguments are not known inspection
operations require confirmation in the terminal. This includes interpreters,
Git, package managers, programmable text tools, and arbitrary executable paths.
Confirmed commands are powerful: approval is not an operating-system sandbox.

Browser commands accept HTTP/HTTPS URLs. Local/private network targets remain
available because network diagnostics are a core Gathm feature. Do not grant
browser or unrestricted tool access to untrusted remote users. Termux's
Selenium backend currently starts Chromium with `--no-sandbox`; use it only for
trusted browsing until browser isolation is improved.

## Installation and containers

The Docker build excludes `.env` files, Git history, local virtual environments,
and caches. Compose requires an API key and publishes only on host loopback.
Do not bake credentials into an image; inject them at runtime.

Some installer downloads still trust upstream HTTPS releases without an
independently pinned checksum. Audio binaries support
`GATHM_AUDIOCPP_BIN_SHA256`; use it for custom downloads. Review the remaining
hardening work in [the security review](security_best_practices_report.md)
before exposing Gathm as a service for multiple users.

When a GitHub runtime asset supplies a SHA-256 digest, installation verifies it
before extraction. Requested audio checksums fail closed if verification fails.
Downloads use private temporary paths; a partial Ollama installer is not executed.
Pilot and optional Engineer environments upgrade vulnerable pip/setuptools
bootstrap versions within their own environment. Offline installs cannot repair
missing or vulnerable dependencies; they report failure rather than success.

Uninstall retains models, settings, history and lessons by default. Removing
state requires `--purge-data`; the source checkout and its `.env` remain. Services
are stopped only when PID, process creation identity and checkout ownership
agree. Old services without records must be stopped manually. Creation identity
limits stale-PID mistakes but is not isolation against a malicious same-user
process racing a signal.

## Local records and automatic search

General orchestration/retry logs omit raw tool arguments and user queries.
Typed device-control audit records retain action targets for accountability
inside the private state directory. Cache
metadata stores argument counts; cache keys include argument boundaries and the
API principal. Sensitive and mutating tools bypass the cache. State, logs, cache
and installer-written `.env` files use private permissions. These changes do not
erase information already stored by older versions; review or remove historical
records if needed. Same-user access is outside this privacy boundary.

Automatic web fallback respects tool allowlists, offline instructions and a
conservative public-question check. It sends the current question, not memory or
history. Search providers receive that question and the network address. Privacy
and uncertainty checks are heuristics, not a proof of secrecy or correctness.
Search snippets remain untrusted and source links identify the returned evidence;
they do not certify that a claim is true. Failed searches back off, and the
assistant must state when online verification was unavailable.

Explicit public searches and common freshness requests route to real lookup
results before model generation. Search snippets and tool failures are returned
directly, avoiding fabricated biographies or repair commands in a second model
pass. Instructions use the system-message role. Recognized action claims without
current-turn evidence are blocked; this language guard cannot detect every
unsupported statement. File counts use a typed read-only operation and app aliases
resolve only to installed inventory entries. Read-only follow-ups never approve
a mutation, and app launches retain the normal confirmation requirement.
