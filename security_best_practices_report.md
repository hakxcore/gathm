# Gathm cleanup and security review

Review date: 2026-10-11. Baseline: `1a4fbd6`, plus the pending installer and system-control regression work.

## Executive summary

The review fixed vulnerable dependency versions, a release-packaging bug that included local virtual environments, missing nested credential exclusions, and an HTTP news-feed default. Unused imports and a shell-based terminal-clear call were removed. Useful system-control tests, benchmarks, documentation, and the original logo were retained.

After upgrading, pip-audit reported **zero known advisories across 58 installed Python packages**. This result covers the audited environment, not every optional dependency, native runtime, or future dependency resolution. No new exploitable application-level high/critical issue was confirmed by the source review. The remaining trust and transport limitations below still apply.

Python **3.10+ is now required** because the patched dependency releases no longer support Python 3.9. The package metadata, installer, documentation, and minimum-version CI job agree.

## Scope and evidence

Reviewed the Bash launcher, installers, tools and shared libraries; Python FastAPI server, Pilot, model clients, system controls and optional engineer entry point; browser JavaScript; dependency manifests; release configuration; and regression tests. Manual attention focused on authentication, same-origin restrictions, permissions, command execution, confirmation ownership, filesystem targets, downloads, and rendering untrusted text.

Machine-readable results: [scan-results-2026-10-11.json](docs/security/scan-results-2026-10-11.json). Raw logs remain local under ignored `build/security-audit/`.

- Bandit scanned 22 Python source files outside `tests/`: 0 high, 9 medium, 72 low heuristic findings after cleanup.
- Ruff F401/F811/F821/F841 checks: no remaining findings in that source set.
- Vulture: two intentionally retained adapter parameters (`wait_until`, `full_page`) required for browser API compatibility.
- ShellCheck checked 80 Bash files: 0 errors, 53 existing warnings (27 SC2155, 8 SC2220, 18 SC2034). The modified installer, uninstaller, shared installer library, and lifecycle test have no warning-level findings.
- A redacted scan for common private-key, GitHub, AWS, and provider-token patterns found no matches in repository text files. This was a pattern scan of tracked and nonignored files, not Git history or ignored personal configuration.
- pip-audit: four advisories affecting two packages before upgrades; zero afterward. `pip check` also passed.

## Fixed findings

### SEC-2026-01 — High, conditional: vulnerable dependency releases

Evidence: `pyproject.toml:34`, `pyproject.toml:39`, and `pilot/requirements.txt:17`.

The installed `langgraph-sdk` 0.4.2 had an authorization-decorator advisory. Gathm does not use the affected LangGraph server authorization decorators, so this is an installed vulnerable dependency rather than a demonstrated bypass of Gathm's own API authorization. The minimum is now 0.4.4; the audited environment runs 0.4.6. See the [upstream authorization advisory](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fvww-7h3r-vfhp).

The installed `urllib3` 2.7.0 had three advisories concerning chunked-response buffering, deflate decompression, and HTTPS proxy TLS handling. Malicious upstream responses could affect availability; proxy impact depends on configuration. The declared minimum and installed version are now 2.8.0. See the upstream advisories for [chunked responses](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw), [decompression](https://github.com/urllib3/urllib3/security/advisories/GHSA-gh4c-6fx4-qh6g), and [proxy TLS](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77).

### SEC-2026-02 — Medium: wheels could include local environment files

Evidence: `pyproject.toml:91`, `pyproject.toml:102`, `pyproject.toml:133`; regression: `tests/wheel_contents_test.py:1`.

Directory `force-include` copied local virtual environments despite global exclusions. A direct build from this checkout produced a 58,902,357-byte wheel with 3,309 unwanted entries. That behavior also risked including credentials placed inside a bundled directory; no actual credential disclosure was demonstrated.

Runtime directories now use normal selection and source-path mapping. Forced inclusion remains only for the three known root scripts. Explicit exclusions cover environments, caches, node_modules, `.env` variants, and tool-documentation screenshots. The same checkout now produces a 448,394-byte wheel with 162 entries. Required runtime files remain present. A dirty-fixture regression checks 14 required files and nine unwanted files, including credentials and virtual environments. Both wheel and source archive were inspected. See [Hatch's file-selection and path-mapping documentation](https://hatch.pypa.io/latest/config/build/).

### SEC-2026-03 — Medium hardening: nested Docker credential files

Evidence: `.dockerignore:6`, `.gitignore:5`.

Nested `.env` and `.env.*` paths are explicitly excluded from Docker contexts, alongside dependency directories and caches. Git also ignores `.env` variants while allowing `.env.example`. Docker builds were not executed because the local Docker daemon was unavailable.

### SEC-2026-04 — Low: news feed used cleartext HTTP

Evidence: `tools/news/news:10`.

The default BBC RSS endpoint now uses HTTPS. The same provider's TLS endpoint was fetched and parsed successfully with 30 items. This removes the cleartext default without changing the service or output format.

### SEC-2026-05 — Cleanup and installer safety

Evidence: `pilot/tui.py:239`, `lib/install_common.bash:1`, `tests/install_lifecycle_test.sh:1`.

Removed unused Python imports, an unused API response fallback, and an unused packaging variable. Rich now clears the terminal directly instead of invoking a shell. The previous constant clear command was a scanner finding, not demonstrated input injection.

Included the pending installer/uninstaller fixes: shared platform and ownership handling, isolated virtualenv repair without global-pip fallback, safe shortcut publication, preservation of foreign commands and shared data, noninteractive error handling, and an explicit dry-run/keep-data uninstall flow. Destructive tests run in temporary fixture homes; the real installation was not uninstalled.

## Existing protections rechecked

- API authentication, loopback Host/client restrictions, same-origin browser checks, permissions, and job ownership: `api/server.py:816` and the security/API regression suites.
- System-control proposals require capability checks; confirmations consume an expiring, owner-bound, single-use random ticket: `api/server.py:1119`, `api/system_approvals.py:22`, `api/system_approvals.py:33`.
- Typed operations revalidate the proposed action and target before execution: `lib/system_control.py:522`. Filesystem changes use platform-specific non-overwriting rename operations: `lib/system_control.py:568`.
- Approval cards render values as text, keep tickets out of saved chat/model history, and cancel pending proposals when chat is cleared: `gui/system-controls.js:37`. Real-browser tests verified approval, cancellation, persistence, and streaming behavior without performing real system actions.

## Remaining limitations and hardening work

### SEC-2026-06 — Medium: download provenance

Evidence: `install:2135`, `install:2988`.

Some runtime downloads and the Linux Ollama installer still trust upstream HTTPS without independently pinned checksums or signatures. Compromise of an upstream release or delivery account could run code as the installing user. Custom audio downloads support `GATHM_AUDIOCPP_BIN_SHA256`. A broader signed/pinned release policy remains future work.

### SEC-2026-07 — Medium: permitted tools run with the user's privileges

Evidence: `lib/sysexec.py:431`, `pilot/browser.py:268`, and [SECURITY.md](SECURITY.md).

Tool permission checks and explicit approvals are not operating-system isolation. Trusted administrators can enable shell execution or use the engineer; approved code runs as the current account. The Termux Selenium path also uses Chromium's `--no-sandbox`. Gathm should not be presented as a sandbox for hostile code or an untrusted multiuser service.

### SEC-2026-08 — Low: geolocation still uses a cleartext provider endpoint

Evidence: `tools/geo/geo:79`, `tools/geo/tool.yaml:16`.

The existing `ip-api.com` endpoint uses HTTP, allowing an on-path observer to see the requested lookup and alter its public-data response. Provider migration or verified HTTPS support is still needed. It was not silently switched to a different service or an unverified paid endpoint.

### Static-analysis triage

The nine medium Bandit findings are seven `urlopen` calls using operator-configured model/benchmark endpoints (B310), wildcard-bind display-address normalization (B104), and the prompt-formatting keyword `shell=` (B604), which does not launch a process. They do not establish a new untrusted-input exploit under the documented local-operator trust model. Endpoint configuration and redirect restrictions could be tightened further if that model changes.

Low findings primarily concern subprocess use, deliberate error recovery, and assertions. The remaining ShellCheck warnings were recorded, not globally suppressed. No claim is made that scanner silence would prove security.

## Validation and practical limits

Passed locally on macOS:

- 220 Python unit tests, plus the dedicated 17-test security suite and 344 shell-execution checks.
- Packaging (104), llama.cpp runtime (63), launcher (61), and model-install fixture (55) checks.
- Installer lifecycle: 15 fixture groups under Bash 5 and macOS Bash 3.2; runtime reuse (18) and virtualenv-layout (14) checks.
- Remaining speech, routing, compact-output, connectivity, manifest, Markdown, VAD, and GUI unit suites.
- 17 real Chromium streaming/approval checks. Model and speech services were deterministic stubs; no shutdown, restart, or other real system mutation was performed.
- The real Mac `./install --check`, Bash syntax, targeted ShellCheck, and `git diff --check`.
- Source archive and wheel builds, dirty-fixture packaging regression, and a fresh dependency-free wheel install with working `gathm --version` and `gathm --where`.

Initial loopback-server tests were blocked by the execution sandbox; reruns with local-server access passed. Fresh native Windows, Linux, and Android installations were not performed in this review. The workflow includes Linux/macOS checks and Windows installer fixtures; their results must be read from GitHub after the push. Optional engineer/browser/model native dependencies and historical Git secrets were not exhaustively audited. The September review remains available in Git history and its dated evidence file; this report records the current checkout review.
