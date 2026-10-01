# Laya integration check

Checked on 2026-10-01 in the macOS checkout using `pilot/venv/bin/python`.

## Current integration

Laya is an optional routing benchmark in `bench/route_bench.py`. It is not used
by live GUI or TUI conversations and is not an installation dependency.
The GUI starts `pilot/chat_once.py`, which uses the same agent graph as the TUI.
That graph calls `_shortlist_tools()` and then the configured LLM in
`pilot/main.py:call_model()`.

The active Python environment has no `laya`, `torch`, `transformers`, or
`huggingface_hub` installation. Actual Laya checkpoint inference, routing
accuracy, memory use, and latency could not be verified. No model downloads or
changes to the assistant's installed dependencies were made for this check.

## Checks and corrections

- The benchmark's original 32 checks passed, but its Laya adapter uses a stub.
  These checks do not establish that real Laya inference works.
- Running `--routers shortlist,laya` on all 79 questions found 57 tools. The
  keyword shortlist contained the expected tool on 74/79 questions (93.7%);
  selecting its first entry was correct on 62/79 (78.5%). Laya was skipped
  because it was not installed. These numbers are not Laya results.
- The adapter now reads `answer_confidence`, falling back to the selected
  label's probability in older responses. Laya's legacy choice `confidence`
  represents normalized entropy, not answer probability.
- Laya loading defaults to lazy. Explicit preloading loads all configured
  checkpoints and can override the requested resident-model limit.
- An entirely skipped benchmark or inference failure now exits unsuccessfully.
  Requested JSON records skipped routers, and failed calls do not enter latency
  statistics.
- After these corrections, all 95 benchmark regression checks passed. A real
  `--routers laya --limit 1` invocation exited with status 1 and wrote JSON
  containing no router results and the explicit `not installed` skip reason.

The API field and preload behavior were checked against Laya's published
[answer implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py)
and [router implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/router.py).
Adapter regressions use fixtures; no real checkpoint was loaded.

## Before enabling live routing

Run the benchmark with real checkpoints on each target device, comparing
accuracy and latency with the existing LLM path. The current shortlist already
misses five expected tools, which a downstream selector cannot recover.
Include everyday conversation that needs no tool, multi-step requests, and
conversation context in the evaluation; the current dataset selects a single
tool per question.

Live integration needs a persistent router or model service: starting a new
Laya instance inside the GUI's per-turn `chat_once.py` process would repeatedly
load weights. Preserve direct conversation, tool argument validation, approvals,
and fallback to the configured LLM when routing is unavailable or uncertain.
