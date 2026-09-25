# Branch consolidation review — 25 September 2026

Reviewed all 20 non-main remote branch tips against `main` at
`5f800005c3340ac4ed596875d46a4acff7d1a897`. Nine were already ancestors of main.
Eight newer branches share history and are integrated through a real merge of
`3de345a` and `f8bc87e`; their individual commits remain reachable.
Three older divergent branches are preserved under archive tags rather than
merged wholesale. Their unique experiments remain recoverable.

## Branch-by-branch decisions

The tip column records the exact reviewed commit, not a moving branch name.
Branches are eligible for deletion only after main or the listed archive tag
has been pushed and its preservation verified. Any changed remote tip requires
another review before deletion.

| Branch | Reviewed tip | Decision |
| --- | --- | --- |
| `audio-app-with-gemma3.1b` | `ae67e3d7c5d76f4148e2e3a5c1b3ef10f9d3a011` | Archive prototype; keep duplicate runtimes, snapshots and patch scripts out of main. |
| `claude/add-uninstall-script-K8TTc` | `848477e9176c75d044a91aafa8c046f40aff554c` | Already included in main; remove redundant branch reference. |
| `claude/agent-loop-guards-6bnsox` | `803122527d99f40adc33040ce7cf08f3f32d4f0f` | Merge bounded steps and duplicate prevention after correcting turn scope and diagnostics. |
| `claude/audio-cpp-termux-install-eipzyb` | `9fbec48b86e8519a0d8097cd4e5542d7579de6e5` | Already included in main; remove redundant branch reference. |
| `claude/busy-mayer-VmgFx` | `87c87fe6a621f92ecb9ce77b06e28080a38e564c` | Already included in main; remove redundant branch reference. |
| `claude/compact-tool-output-6bnsox` | `f8bc87e19dadf45a8369beb0c2982c0f64b908c9` | Merge compact observations and early finish after correcting graph edges and forecast handling. |
| `claude/engineer-ai-agent-UpiZv` | `e4d2cfdcf06d5f8ef1909dc0e38ca68b6a3665aa` | Already included in main; remove redundant branch reference. |
| `claude/enterprise-ai-agent-planning-vrN6Q` | `680f2b35b2902308d68acac3300c77781d036600` | Already included in main; remove redundant branch reference. |
| `claude/fix-engineer-production-bzjIf` | `b91b6d7dac2c9249eab143570be57bdeb46172c4` | Already included in main; remove redundant branch reference. |
| `claude/gathm-pilot-tui-UWykM` | `620d9c75636021d76d6bdde3f5ae0e5ee99f9d13` | Already included in main; remove redundant branch reference. |
| `claude/jolly-carson-j2c8zt` | `36c6e95b8ce751943861d8bf64ddb67e5e3a5383` | Archive alternative voice providers and obsolete HTTP API; current speech/API retained. |
| `claude/llama-cpp-integration-6bnsox` | `3de345a07ac40accc0acb4e1bf37ca2c86e56f25` | Merge llama.cpp, measured schema speedup and security/cleanup work. |
| `claude/net-check-latency-6bnsox` | `1280cfa393c7923b57363ec5be8861c2ce7db8fa` | Merge bounded header checks; repair fetch backend and timeout fixture. |
| `claude/prompt-cache-order-6bnsox` | `b3e72522728741187e271afd7ad8ad5235468bc8` | Merge stable instruction prefix and routing on the actual user question. |
| `claude/serene-turing-yrxky2` | `06e11f5551bfe0b4880821a4d86db0e8416021b3` | Port Android TTS fallback; archive the parallel engine manager and installer alternative. |
| `claude/shortlist-fallback-order-6bnsox` | `b000c7672280cf01cb859025b3ad072435850b95` | Merge operand filtering, multiline tags, routing vocabulary and fallback order. |
| `claude/speech-fixes-6bnsox` | `cf5e4882d87afd62f9ee1188953597ac01a6ada9` | Merge rendering retries and playback reuse after fixing the CSP-compatible primer. |
| `claude/tool-routing-benchmark-6bnsox` | `d60675b6c1c87903f6471c30af38b95daf59508a` | Merge benchmark after fixing identifier parsing and confidence coverage. |
| `extending-control` | `89e583aea8dfd24286f478c576e3f558d89ad78a` | Already included in main; remove redundant branch reference. |
| `lets-stream` | `b6b35ccef83e5045c4001226ee984a9aded2f305` | Already included in main; remove redundant branch reference. |

## Integration corrections

- Preserve the security commit's API authentication, same-origin/CSP checks,
  role-to-worker tool allowlists, job ownership and bounded inputs/output.
  Keep positional shell arguments and tool permission checks when resolving
  the Pilot conflict. System commands retain shell expansion and operators.
- Return from the graph's action node to the model after a tool result. The
  incoming edge pointed back to the action node. Repeated commands now end
  only the current turn and return that command's own result. Earlier turns
  do not suppress a new request. Bounded-loop fallback also retains CLI history.
- Make loop diagnostics reachable. Do not label every HTTP 400 as context
  overflow; report the original error unless there is context-size evidence.
- Use a nonempty silent WAV blob for the reusable browser audio element, keeping
  the existing CSP unchanged. Clean up URLs, allow a failed primer to retry,
  and prevent a late primer promise from pausing a real reply.
- Retain compact current weather, unit flags and moon output. `weather -f`
  requests the full forecast; Pilot adds it for future/forecast questions.
  Invalid flags fail before network work. The compact test works with macOS's
  BSD tools as well as GNU tools.
- Use FreeBSD fetch's metadata-only `-s` option. Retain sockets in the local
  timeout fixture so it exercises a real three-second timeout, not immediate
  connection closure. The native fetch backend is covered by its argument
  contract here, not execution on FreeBSD.
- Parse complete benchmark tool identifiers so `dnssec.` is not scored as
  `dns`. Reject ambiguous multi-tool answers, count errors in the confidence
  coverage denominator, and discard stale confidence after a failed repeat.
- Add graph, routing, speech lifecycle and Android TTS checks to CI coverage.
  Keep the benchmark outside the installed runtime bundle.

## Older divergent work

`audio-app-with-gemma3.1b` adds 14,209 lines including three installer snapshots,
seven Pilot snapshots and one-off patch programs. Its two dependency-light
Termux implementations differ in routing and result presentation, disable
Termux GUI/browser installation, and predate the consolidated permission and
loop controls. The PocketTTS helper duplicates the current speech library.
A minimal optional runtime and deterministic command shortcuts remain possible
future projects; these prototypes are preserved, not silently discarded.

`claude/jolly-carson-j2c8zt` adds faster-whisper, edge-tts and Piper to an obsolete
HTTP handler with wildcard CORS and unbounded request reads. Current recording,
VAD, streaming and API permissions are retained. Its Piper fallback returns raw
PCM labeled as MP3, and Whisper reloads per subprocess. Optional alternative
providers and audio visualization remain in the archive for future redesign.

`claude/serene-turing-yrxky2` contains a useful Android native TTS fallback. That
small capability is ported to the current speech library: actual Termux plus
`termux-tts-speak` on PATH are required, text is a single argv argument after
`--`, and the existing timeout applies. audio.cpp retains automatic priority;
explicit command overrides remain honored. Android playback is not advertised
as browser WAV synthesis, and installed espeak can still supply WAVs.
The old standalone engine manager, KittenTTS support, persistent voice preference
and untested Rust pydantic-core build fallback are retained only in the archive.
The current pinned Android-native wheel/package installation remains in place.

Archive tags (non-release names; they do not match the package publish trigger):

- `archive/2026-09-25/audio-app-with-gemma3.1b` → `ae67e3d7c5d76f4148e2e3a5c1b3ef10f9d3a011`
- `archive/2026-09-25/claude/jolly-carson-j2c8zt` → `36c6e95b8ce751943861d8bf64ddb67e5e3a5383`
- `archive/2026-09-25/claude/serene-turing-yrxky2` → `06e11f5551bfe0b4880821a4d86db0e8416021b3`

## Validation

All verification uses the combined source on macOS, with Python 3.14 and the
existing project dependencies. It does not deploy changes to the phone.

- Python discovery suite: 98 tests, including seven Android TTS cases and seven
  compiled-graph flow cases. Security boundary suite: 17 tests.
- Additional Python checks: system execution 344, packaging 98, macOS speech
  135, streaming speech 58, reply streaming 40, llama.cpp 63, routing benchmark 32.
- JavaScript checks: Markdown 104, chunking 40, VAD 32, speech lifecycle 6.
- Shell checks: launcher 61, llama.cpp installer 53, compact weather 14,
  connectivity 6; installer self-check passes.
- Real Chrome conversation suite: 22 checks. Actual API/GUI smoke: HTTP 200,
  local icons, successful gesture audio priming and playback, no page errors
  or CSP violations. Android browser autoplay/native TTS are not device-tested
  in this consolidation.
- Source distribution and wheel built; wheel installed in a fresh temporary
  environment without dependencies for launcher/bundle smoke checks. Runtime
  dependency behavior is covered by the existing populated test environment.
- Shell/Python syntax and diff whitespace checks pass. Bandit comparison adds
  only a low-severity ignored benchmark warm-up exception; no new medium/high
  finding. Existing findings and limitations remain documented in
  `../../security_best_practices_report.md`; this is not a claim of zero risk.
  Production dependencies are unchanged from the preceding audited commit.

The 79-question local routing benchmark improves shortlist recall from 71/79
(89.9%) to 75/79 (94.9%), and keyword top-1 from 58/79 (73.4%) to 62/79 (78.5%).
Shortlist evaluation is about 0.14 ms median on this Mac. This hand-labelled set
is not held-out evaluation; these figures are neither full-agent response time
nor a new phone benchmark. Existing measured phone startup results remain in
`../performance/termux-2026-09-24.md`.

GitHub-hosted CI could not run the preceding pushed commit: check annotations
report an account billing lock before any test steps. Local checks above are
explicit evidence; an unavailable hosted run is not treated as a passing check.
See https://github.com/hakxcore/gathm/actions/runs/36017845184.

## Preservation and cleanup procedure

1. Re-read remote main and every reviewed branch tip; stop if any changed.
2. Push the reviewed merge to main by normal fast-forward update.
3. Push and verify the three annotated archive tags at the exact original tips.
4. Verify every other tip is an ancestor of remote main.
5. Delete the reviewed remote branches with explicit expected-tip leases,
   preventing deletion of concurrently updated work.
6. Fast-forward the local main checkout and remove redundant local branches and
   the temporary integration worktree only after verifying they are clean.

Future work should start from main on a short-lived topic branch; keep backup
files and generated test outputs outside the source tree.
