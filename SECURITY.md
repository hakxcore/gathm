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

System command execution remains disabled unless explicitly enabled by the
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
