# Gathm cleanup, latency and security review

Date: 2026-10-16. Baseline: `85b2efc`. Scope: the complete tracked/nonignored source tree and the changes in this review.

## Executive summary

Two confirmed medium-severity issues were fixed: cached permissions surviving removal of the policy file, and predictable temporary paths in installers/tools. No new confirmed exploitable high/critical application issue was found in the source review. Dependency audits returned zero known advisories for 58 installed packages and for an independently resolved 83-package graph including optional engineering, OpenAI-client and Selenium dependencies.

Permission setup previously parsed 55 manifests before every protected API request. Its measured warm median fell from 43.520 ms to 0.373 ms; catalog loading fell from 42.996 ms to 1.157 ms. These are API preparation measurements, not end-to-end model or voice latency. File changes still invalidate metadata immediately, and cached return values are isolated from callers.

Unused test imports and an unused module construction were removed. A stale terminal-clear test mock was corrected. The superseded October 11 scan artifact was replaced by [current machine-readable evidence](docs/security/scan-results-2026-10-16.json); historical evidence remains in Git history. Active runtime files, regression tests, benchmark baselines, configuration and the original logo remain useful and were retained.

## Fixed findings

### SEC-16-01 — Medium: missing policies left cached authority active

- Rule: FASTAPI-AUTHZ-001, fail-closed authorization.
- Location: `api/server.py:184` and `api/server.py:188` (`_file_stamp`, `_get_policies`).
- Evidence before the fix: the `except OSError` branch returned the previous `_policies` dictionary. Only floating-point modification time controlled reloads.
- Impact: after an operator removed or made the policy file inaccessible, new requests could continue using the last loaded grants. Replacements preserving modification time could also leave obsolete grants active. This requires a local configuration change; it is not a demonstrated unauthenticated remote takeover.
- Fix: clear grants when policy metadata or contents cannot be read. Check file identity, size, nanosecond modification/change times and policy path for reloads. Removal now denies permission checks and recreation recovers using the new grants.
- Verification: `tests/test_api_metadata.py` covers missing/unreadable policies, recovery and replacement with preserved modification time. The existing API authorization and system-approval suites also passed.

### SEC-16-02 — Medium: predictable temporary outputs could follow planted paths

- Rule: secure temporary-file creation / filesystem integrity.
- Locations: `install:1424`, `install:3302`, `tools/subdomains/subdomains:56`, `tools/pdchain/pdchain:200`.
- Evidence before the fix: failed allocations fell back to PID-based or fixed filenames; the default chain directory used a domain and second-resolution timestamp under `/tmp`.
- Impact: another local user able to plant a guessed path could influence output destinations or read output. The installer/subdomain fallbacks required `mktemp` failure; the default chain directory was predictable during normal execution. This is a local shared-directory attack surface.
- Fix: fail when private allocation fails, use an exclusive randomized patch file in its destination directory, and create the default chain directory with `mktemp -d`. The subdomain response runs in a subshell so its EXIT cleanup retains access to the local filename and no longer needs duplicated cleanup code. Explicit `pdchain -o` destinations remain operator-selected output paths.
- Verification: four isolated tool tests cover successful cleanup, distinct private directories and allocation failures. Installer lifecycle fixtures additionally verify that failed allocation starts no download and leaves source files untouched; all 16 fixture groups pass under modern Bash and macOS Bash 3.2.

## Latency change and cache safety

Evidence: `api/server.py:306`, `api/server.py:319`, `api/server.py:324`, `api/server.py:817`.

Authorization now uses live executable names without parsing tool manifests. Catalog parsing uses a bounded 256-entry cache keyed by file metadata. Each read checks the current file stamp; no time-based authorization or metadata TTL was introduced. Returned manifest dictionaries are deep copies, preventing one caller from modifying another caller's cached nested values. Explicit policy denies and confirmation requirements retain priority.

Ten metadata regressions verify isolation, edited/replaced/removed manifests, live tool additions/removals, no parsing during permission setup, path validation, and policy revocation. The 50-sample before/after comparison, method and limitations are in [the latency guide](docs/testing/latency.md#api-metadata-overhead--2026-10-16) and [raw measurements](docs/testing/api-overhead-2026-10-16.json).

## Scan coverage and triage

- Manual review focused on API authentication/origin checks, role propagation, job and approval ownership, shell/system execution, uploads/static serving, model-client requests, browser rendering/storage, installers, tool outputs, containers and package selection.
- Bandit 1.9.4 checked **23 runtime Python files**, including the extensionless web-search entry point: **0 high, 10 medium, 72 low** findings.
- The medium findings are eight B310 URL-opening heuristics for operator-selected model/benchmark endpoints or the fixed web-search provider; B104 on loopback display-address normalization; and B604 on a prompt-formatting `shell=` keyword, which does not launch a process. They were reviewed under the documented trusted-operator model; they are not ten confirmed exploitable vulnerabilities.
- Ruff 0.17.1 checked **51 Python files** for F401/F811/F821/F841: **0 findings** after removing unused imports and the unused construction.
- Vulture 2.16 flags two intentionally retained Selenium adapter parameters, `wait_until` and `full_page`, used for interface compatibility with callers.
- ShellCheck 0.11.0 checked **80 Bash files**: **0 errors, 53 pre-existing warnings** (SC2155: 27, SC2220: 8, SC2034: 18). The changed installer/tool scripts and lifecycle test are clean at warning severity. Warnings were recorded, not broadly suppressed.
- A redacted scan of repository text found **0 common credential-pattern matches**. It covers private-key headers, GitHub tokens, AWS access IDs and provider-token patterns, reporting locations only. Ignored personal configuration and historical Git objects were outside this scan.
- pip-audit 2.10.1 returned **0 advisories and 0 skipped packages** for both the 58-package installed environment and the 83-package resolved optional graph. The latter combines Pilot, API, engineer, `langchain-openai` and Selenium requirements for this Mac/Python environment. No optional runtime was installed into Gathm for the audit. `pip check` passed.

Sources/methods: [Bandit](https://bandit.readthedocs.io/), [pip-audit](https://github.com/pypa/pip-audit), [Ruff](https://docs.astral.sh/ruff/), [ShellCheck](https://www.shellcheck.net/). Scanner counts and dependency versions are recorded in the linked evidence file.

## Remaining security limitations

### SEC-16-03 — Medium: upstream download provenance

Evidence: `install:2135`, `install:2988`. Some runtime downloads and the Linux Ollama installer trust upstream HTTPS without independently pinned checksums/signatures. A compromised upstream account/release could execute code as the installing user. Custom audio downloads support `GATHM_AUDIOCPP_BIN_SHA256`; a complete signed/pinned release policy remains future work.

### SEC-16-04 — Medium: permitted tools are not OS-isolated

Evidence: `lib/sysexec.py:431`, `pilot/browser.py:268`, [SECURITY.md](SECURITY.md). Approved commands, optional engineering code and permitted tools execute as the account running Gathm. Termux Selenium uses Chromium's `--no-sandbox`. Role controls and human approvals restrict dispatch but do not provide an operating-system sandbox for hostile code or untrusted multiuser operation.

### SEC-16-05 — Low: cleartext geolocation lookup

Evidence: `tools/geo/geo:79` and `tools/geo/tool.yaml:16`. The existing free `ip-api.com` provider uses HTTP. An on-path observer could see a lookup target or alter the public-data response. A provider change or confirmed HTTPS service is still needed; this review did not silently substitute an unverified endpoint.

## Validation and limits

Passed locally on macOS:

- **234 Python unit tests**, including 14 new metadata/temporary-file tests.
- Dedicated security (17), system execution (344), packaging (104), llama.cpp runtime (63), model installer (55), routing (95), manifest (55), speech and reply-stream suites.
- Launcher (61), virtualenv layouts (14), network checks (6), compact output (14), runtime reuse (18), and installer lifecycle (16 groups under Bash 5 and macOS Bash 3.2).
- All six JavaScript unit suites and **17 real Chromium streaming/approval checks** using deterministic local model/speech stubs.
- Real Mac installer self-check, targeted ShellCheck, Bash syntax and whitespace checks.
- Dirty-wheel fixture regression and inspected source/wheel builds, with no local virtualenvs, caches, dependency directories or `.env` variants included and all required runtime paths present.

Initial full-suite testing exposed an obsolete `tui.os.system` mock left by earlier cleanup; it now patches Rich's actual `console.clear` call, and the full suite was rerun successfully. No real system shutdown, restart or uninstall was performed. Native Android/Linux/Windows installs and real-device voice/model latency were not retested. Optional native dependencies were resolved for this host, not every supported platform. No penetration-test or zero-vulnerability guarantee is implied by the automated scans; the remaining limitations above still apply.
