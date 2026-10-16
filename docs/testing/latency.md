# Assistant latency

Measured on 2026-10-02 using native macOS 26.7 arm64, llama.cpp, and the existing
`Meta-Llama-3.1-8B-Instruct-Q4_K_M` model. The baseline is commit `e0c614d`.
Both API builds used the same resident model server, temperature `0`, and
`GATHM_SPEAK=0`. No model, quantization, context, or output-token limit was
reduced for this comparison.

## Paired results

Four repetitions per task per build, with request order alternating each
iteration: **32 of 32 successful replies**. The table gives medians in seconds.
The baseline returned a complete JSON response, so its first-text and full-reply
times were the same. The changed build requested streaming responses.

| Task | Before: first text / full reply | After: first text | After: full reply | Full-reply reduction |
|---|---:|---:|---:|---:|
| Greeting | 1.432 | 0.979 | 0.979 | 31.6% |
| Short explanation | 3.195 | 0.802 | 2.529 | 20.8% |
| Birthday message | 3.986 | 0.990 | 3.241 | 18.7% |
| Recall a name from history | 1.016 | 0.381 | 0.382 | 62.4% |

First-text time decreased by **74.9% for explanation** and **75.2% for writing**.
The initial greeting, recorded separately before measured repetitions, took
2.258 seconds before and 1.300 seconds after. These initial requests include
assistant startup but **not cold model loading**.

[Raw measurements and replies](latency-macos-2026-10-02.json) include warmups,
all samples, and medians. Prompts are synthetic and contain no personal data.

## Changes behind the result

- Reuse a serialized assistant subprocess and its imported dependencies.
  History is supplied explicitly for every request; browser sessions are
  closed after each turn. Caller, permission, environment, and watched
  configuration/code changes invalidate the process.
- Create the model client only when needed, then reuse it. llama.cpp readiness
  is still checked so a stopped server can recover on the next turn.
- Refresh connectivity asynchronously with a short-lived cache. The terminal
  welcome screen does not wait for the probe, and unknown status stays unknown.
  The socket probe now closes its connection without changing global timeouts.
- Stream reply text to the GUI and feed completed sentences into speech with
  bounded synthesis lookahead. Mute and interruption cancel outstanding speech
  and stop later tokens from restarting it.

The worker queue, protocol messages, accumulated token output, and SSE buffers
are bounded. Timeouts and streaming disconnects stop the active worker without
retrying a request that may already have executed a tool. The normal tool
permissions and confirmation rules still apply. The reusable worker remains
resident between turns; set `GATHM_CHAT_WORKER=0` if one-shot processes are
preferred for memory or troubleshooting.

## Reproduce

Run the baseline and changed checkout on separate loopback ports with the same
model configuration. For this run, the baseline used port 8767 and the changed
build used 8768. Both were launched with:

```bash
GATHM_LLM_BACKEND=llamacpp GATHM_LLM_TEMPERATURE=0 GATHM_SPEAK=0 \
  /path/to/pilot/venv/bin/python api/server.py --host 127.0.0.1 --port PORT
```

Then run from the changed checkout:

```bash
pilot/venv/bin/python bench/assistant_latency.py \
  --before-url http://127.0.0.1:8767 \
  --after-url http://127.0.0.1:8768 \
  --repeat 4 --output build/latency-results/paired.json
```

The harness checks for a successful final reply and measures first displayable
text and full-reply time. It does not execute tools. Alternating order reduces
shared prompt-cache bias but does not isolate inference throughput; reply
wording and token counts can differ. Four synthetic tasks are a small sample,
and these medians are not tail-latency guarantees or independent cold starts.

## Verification and limits

The final Python discovery run passed all 157 tests, including 16 latency tests
and 4 TUI tests. The worker/API subset passed 22 tests covering history separation,
configuration and permission invalidation, shutdown, oversized messages,
blocked writes, slow consumers, timeout, cancellation, and no replay. Existing
security, packaging, reply-stream, markdown, and voice-endpointing suites passed.

JavaScript checks passed: 56 chunker checks, 10 speech/parser tests, 6 voice
lifecycle tests, and 12 actual Chromium 154 checks. The Chromium suite uses a
local deterministic API and real WAV playback to verify text and audio before
the final reply, sentence order, mute/barge-in, whitespace normalization,
legacy JSON replies, and truncated-stream cleanup. It requires no microphone,
model download, or cloud service and runs in the browser CI job.

The latency numbers cover HTTP conversation on this Mac. They do not measure
microphone-to-speaker latency, cold model loading, external tool services,
Android/Termux, Linux, or Windows performance. Physical-device and other OS
measurements remain necessary before claiming the same improvements there.

## API metadata overhead — 2026-10-16

A follow-up profile at `85b2efc` found that every protected API request parsed
all 55 tool manifests to construct its permitted-tool names. That also delayed
speech status and synthesis requests. Permission setup now discovers the live
executable names directly. Catalog responses reuse parsed manifests, with file
identity, size, modification time and change time checked on every lookup;
edits and atomic replacements invalidate cached metadata immediately.

On this Mac, using Python 3.14.7, 50 warm samples per function per version:

| Operation | Before median | After median | Reduction |
|---|---:|---:|---:|
| Request permission setup (`_role_environment('admin')`) | 43.520 ms | 0.373 ms | 99.14% |
| Tool catalog (`list_tools()`) | 42.996 ms | 1.157 ms | 97.31% |

[Raw samples and measurement scope](api-overhead-2026-10-16.json) retain the
before/after data. These numbers measure Python API preparation overhead;
model generation, microphone capture, synthesis, networking, and cold imports
are outside this comparison. No new end-to-end conversation or phone latency
claim is inferred from them.

To repeat, run the same snippet with each checkout's Pilot Python from its
repository root:

```python
import statistics, time
from api import server
for name, operation in [('catalog', server.list_tools),
                        ('permissions', lambda: server._role_environment('admin'))]:
    operation()  # warm the cache
    samples = []
    for _ in range(50):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1e6)
    print(name, statistics.median(samples), 'ms')
```

Regression tests cover cache isolation, edits, replacements with preserved
modification time, removal/recreation, tool discovery, and permission revocation.
