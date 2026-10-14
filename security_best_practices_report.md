# Gathm installation, security and web fallback review

Review completed: 2026-10-14. Base commit: `677f051`. This review covers the
installation/security fixes and automatic web fallback on `codex/learning-harness`.
Separate local Whisper and macOS connector changes are excluded from this push.
The [2026-10-11 scan record](docs/security/scan-results-2026-10-11.json) remains
available for the previous review.

## Result and scope

The review fixed installer failure reporting and unsafe temporary downloads,
stale-PID service termination, raw arguments in logs/cache metadata, cache
isolation, sensitive learning capture, and cleartext geolocation. Uninstall now
keeps user data unless `--purge-data` is explicitly selected. The assistant also
uses real results for explicit searches and selected changing facts, and tries
one bounded web search for recognized uncertainty about public questions,
and handles missing internet without repeatedly retrying or inventing a result.

Reviewed first-party Python, shell, JavaScript, manifests, CI and Docker files.
Manual review covered API authentication and same-origin checks, role/tool
allowlists, job and approval ownership, subprocess boundaries, SQLite bindings,
model/learning state, installer/download ownership and browser text rendering.
Virtualenvs, downloaded third-party sources and ignored personal configuration
were excluded. No live installation or uninstallation was performed on this
machine, and no remote exploitation test was performed.

Machine-readable results: [scan-results-2026-10-14.json](docs/security/scan-results-2026-10-14.json).

## Fixed findings

### Dependency bootstrap — conditional dependency risk

The installed environment initially contained pip 24.0 and setuptools 79.0.1,
with seven distinct known advisories across those packages (14 scanner records
including advisory aliases). The environment now uses pip 26.2.1 and setuptools
84.0.0. Fresh installer environments and the Docker bootstrap require patched
minimum versions. Pilot and Engineer upgrade only their own virtualenvs, and a
failed Engineer setup cannot fall back into Pilot. An offline installation with
missing or vulnerable bootstrap tools fails with a useful error.

The final installed-environment audit found no known advisories in 90 audited
third-party packages (91 installed entries including the local Gathm package); a separate resolution of declared Pilot/API/optional Engineer
requirements found none across 73 packages. The editable Gathm package has no
published vulnerability metadata and is not certified by this dependency scan.
See [pip release notes](https://pip.pypa.io/en/stable/news/), the
[pip index advisory](https://github.com/advisories/GHSA-qwm4-qh6w-59xr), and the
[setuptools advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c).

### Installer and service lifecycle — medium hardening

Temporary download allocation now fails closed instead of using predictable
fallback paths. Audio checksum requests fail closed even when platform checksum
commands are missing. GitHub runtime assets with a supplied SHA-256 digest are
verified before extraction; see the [GitHub asset API](https://docs.github.com/en/rest/releases/assets?apiVersion=2022-11-28).
The Linux Ollama installer is downloaded completely into a private temporary
file before execution, with HTTPS-only redirects and bounded timeouts. A partial
script is never executed. Required dependency failures cannot report success.

Installer-written `.env` files use atomic private replacement, preserve unrelated
settings and reject symlink targets. API/model keys are not placed in installer
command arguments. GUI/Ollama/llama.cpp shutdown requires a matching PID, process
creation identity and checkout ownership. PID files alone no longer authorize
signalling a process. Old services without identity records remain running for
manual shutdown. Windows shell fixtures check the MSYS/native PID distinction;
macOS and Linux creation-identity paths have isolated regression coverage.

Uninstall defaults to retaining models, settings, history and lessons; explicit
`--purge-data` retains the existing foreign-install ownership safeguards. The
source checkout and its `.env` are retained. Real deletion runs only in disposable
fixture homes during tests. This change does not stop or uninstall independently
managed system services.

### Logs, cache and learning privacy — medium hardening

Retry, recovery and orchestration logs no longer record raw arguments, user
queries or command specifications. Log/cache directories and new files use
private permissions. Cache keys include argument boundaries and the API
principal, preventing accidental collisions between argument lists or callers.
Metadata stores argument counts instead of argument values. Numeric metadata is
validated before Bash arithmetic; malformed values cannot be evaluated as code.
Sensitive and mutating tools bypass caching.

Learning skips whole turns involving crypto/cipher, breach checks, media
analysis/transcription and file transfers, including recognized paths and run
aliases. Compound chain/parallel commands are skipped conservatively. Lesson
review remains required. New cache names avoid older entries; historical logs,
caches and learning records are not silently deleted or retroactively redacted.

### Transport and release hardening

Geolocation now uses verified HTTPS at ipwho.is while retaining the existing
field layout. IPv4/IPv6 input is checked before lookup; provider errors fail
clearly. The provider's [documentation](https://ipwhois.io/documentation) describes
its HTTPS endpoint and free quota. CI has read-only default repository permission,
release versions enter shell commands through quoted variables, Docker creates
private state paths without relying on Bash brace expansion, and additional CI checks were prepared for dependency advisories, high-severity
Python findings and changed shell code. The three workflow edits remain local:
GitHub rejected workflow updates because the available credential/integration
lacks workflow write permission. They are excluded from the application push.

### Automatic public knowledge fallback

Approved memory and model knowledge remain the first source. A recognized
uncertain factual reply can trigger one search without another planning model
call; a successful local answer does not invoke the network. Tool instructions
also request lookups for current facts and unseen external content. The fallback
uses only the current standalone public question and applies conservative
English privacy/offline heuristics. Model-added terms, recalled lessons and
conversation history are excluded from the search query. Tool permissions still
apply. Direct CLI websearch remains available separately.

Explicit searches bypass model guessing; successful searches return actual
snippets/URLs directly, with no model-written biography. Tool errors also end
the turn with the real error instead of generated repair commands. Failed or
offline searches do not assert an identity or pretend a lookup ran. Agent
searches use at most three snippets, a five-second network budget, a bounded
response size and an eight-second subprocess ceiling. Offline detection blocks
search; failures or empty results pause searches for 30 seconds in that worker.
Only one search runs per turn, within the graph's tool budget. Actual result URLs
are retained with the answer. Search content is untrusted evidence. Failed
lookups require a local-knowledge caveat, and uncertain answer openings are held
from streaming while the fallback is selected. No new service or database is
required. A live DuckDuckGo smoke test returned three public documentation links.

### Grounding regressions from reported conversations

Assistant instructions now use the system-message role, and the chat temperature
is 0.2 by default for consistent sampling (`GATHM_LLM_TEMPERATURE` remains an
explicit override). Lower temperature is not a factual correctness guarantee.
Exact-name search requests and common freshness questions route to actual search
results before model generation. Unsupported model-written action claims are
blocked when no current-turn tool evidence exists; the guard also holds recognized
claims from streaming. This remains a language heuristic, not a formal verifier.

A typed `files count` operation counts complete immediate directory entries,
separating files, folders, symlinks and other entries, including hidden names and
never following links. A short "you do and tell me" follow-up can reuse only the
immediately preceding read-only request; it cannot approve a mutation. macOS app
aliases resolve against the actual installed-app inventory and retain confirmation.
Opening Music chooses the app; an explicit Music folder remains a folder. Writing
inside Notes and controlling local playback return truthful unsupported-capability
messages instead of made-up paths, package names or successful actions.

See [grounded replies and recovery](docs/factual-grounding.md). Existing fabricated
chat history is retained; start a fresh chat after updating so old assistant claims
do not continue to prime the model.

## Static analysis and secret checks

- Syntax checked 61 Python files (including the extensionless websearch tool),
  83 shell files and 14 JavaScript files: no errors.
- Bandit: zero high findings or scan errors. Production/benchmark code has
  80 low and 12 medium heuristic findings. Medium triage: eight configured URL
  opens (B310), two SQL expressions with bounded identifiers/bound values (B608),
  wildcard client-address display normalization (B104), and prompt formatting's
  `shell=` keyword (B604), which does not execute a process.
- Ruff F401/F811/F821/F841: no production findings. Existing test-only unused
  imports/variables were not changed as part of this work.
- ShellCheck: zero errors, 54 pre-existing warnings across the full shell set.
  The targeted installer/shared-security files pass at warning severity.
- Common credential/private-key pattern scans found no matches in the reviewed
  current source or 1,085 Git-history blobs. One history blob larger than 2 MiB
  was skipped. This is not entropy-based secret detection or a guarantee that
  history contains no credentials.

## Validation and remaining limits

Passed: full Python unittest discovery; 19 web fallback tests and 15 transcript-based
grounding tests; 17 API/security
boundary tests; 344 shell-command checks; 40 reply-streaming checks; 104 packaging
checks; 63 llama.cpp lifecycle checks; 62 launcher checks; installer lifecycle,
reuse, download/checksum, virtualenv-path, cache and geolocation fixtures; and six
JavaScript suites. Install/security fixtures also pass macOS Bash 3.2. Wheel and
source archive build successfully, and the dirty-package fixture retains 15
required files while excluding nine local environment/credential files. See the
machine-readable record for exact suite counts.

No native Windows/Linux/Termux installation or Docker image build was executed
locally; CI covers platform fixtures. Native runtimes, optional dependencies
outside the resolved set and third-party binaries are not certified by pip-audit.
Some downloads still rely on upstream HTTPS without an independently pinned
checksum/signature. Same-user attackers can alter trusted files and race process
signals; creation checks prevent stale-record mistakes, not hostile-user isolation.

Permitted tools execute as the current account; approvals and API roles are not
an OS sandbox. Termux Chromium still uses `--no-sandbox`. Privacy/uncertainty
heuristics can miss secrets or confident model mistakes, and a search snippet or
user correction can be wrong. The fallback is not a factual accuracy guarantee.
Hosted models still need their provider connection; offline chat requires a
prepared local model. No claim of complete security is made.
