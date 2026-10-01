# Routing benchmark

This harness measures the cost and accuracy of selecting a tool. Describing
tools to a generative model adds prompt processing time, especially on a CPU.
The benchmark isolates that decision; it does not measure a full conversation.

**Laya is experimental and benchmark-only.** Neither the GUI nor the TUI calls
it. Both use Pilot's keyword shortlist and configured LLM. Installing Laya
enables this benchmark, not a new chat routing mode. Laya needs its own Python
runtime dependencies and checkpoints; it does not reuse a llama.cpp GGUF.

Tool selection is a classification problem being solved by a generative model.
This harness measures whether something cheaper does it as well.

```bash
pip install -r pilot/requirements.txt     # the harness imports pilot/main.py
python3 bench/route_bench.py              # every router that is available
python3 bench/route_bench.py --routers shortlist    # no model needed
python3 bench/route_bench.py --repeat 3 --json out.json
python3 bench/route_bench_test.py         # the harness's own arithmetic
```

## What it compares

| router | what it is | cost |
|---|---|---|
| `shortlist` | the keyword scorer already in `pilot/main.py`, top-1 of its own ranking | free |
| `llm` | what Gathm does today: shortlist, then ask the configured backend to pick | one completion |
| `laya` | shortlist, then one non-autoregressive `choice` pass (`pip install laya`) | one forward pass |

Every router sees the same questions and the same shortlist. Only the final
pick differs — that is the only way the comparison means anything.

A router that is unavailable is skipped with the reason, so the harness can run
the available alternatives. It exits unsuccessfully if none ran or if inference
raised an error. Requested JSON output includes skipped reasons even when no
router ran. Failed calls are excluded from latency statistics.

## Reading the result

**The ceiling comes first.** If the right tool is not in the shortlist, no
downstream router can recover it. The report opens with `recall@k` of the
shortlist stage, and every router is then reported twice:

- `top-1` — accuracy over all questions. What a user experiences.
- `of those the shortlist reached` — accuracy over only the questions where the
  gold tool was actually offered. This is the number that compares the *pick*;
  the first one blames the router for the shortlist's misses.

Questions are tagged `plain` (names the domain outright), `confusable` (a
neighbouring tool is a plausible answer — `dns`/`dnssec`/`rdns`,
`whois`/`rdap`, `crypt`/`cipher`, `currency`/`cryptocurrency`) and `oblique`
(no keyword overlap at all: "should I take an umbrella to work"). The split
matters more than the total, because the three classes fail for different
reasons and only one of them is fixed by a better model.

Where a router reports answer confidence, the harness also prints what a
**hybrid** would buy: route locally when the model is sure, spend the LLM only
on the rest. That is the shape a real integration would take, so it is the
number worth optimising.

For Laya choices, the harness uses `answer_confidence`, or the selected label's
probability for older releases. Its legacy `confidence` field measures
normalized entropy and is not interchangeable with answer probability. See
[Laya's answer implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py).
These scores still need calibration checks on Gathm requests before deployment.

## On a phone (Termux)

The keyword baseline can run on Termux. Laya inference also requires a compatible
PyTorch installation and downloaded checkpoints; native Android inference has
not been verified. Test on the intended phone before drawing latency conclusions.

```bash
pkg install python
pip install rich                      # the minimum pilot/main.py needs
python3 bench/route_bench.py --routers shortlist     # free, instant
```

Laya loads lazily by default. To keep only one checkpoint resident:

```bash
GATHM_BENCH_LAYA_PRELOAD=0 GATHM_BENCH_LAYA_MAX_LOADED=1     python3 bench/route_bench.py --routers laya
```

`GATHM_BENCH_LAYA_PRELOAD=1` explicitly opts into loading all configured
checkpoints up front. Current Laya loads three and raises its resident limit to
fit them, so `MAX_LOADED=1` does not constrain explicit preloading. Keep
preloading off on memory-constrained devices. The English question set normally
needs only the English checkpoint.

For the `llm` router, start the configured backend first (`gathm llm start` for
llama.cpp, or `ollama serve` for an Ollama setup). The harness makes one test
call and skips with the reason if nothing answers.

Run the routers one at a time on a phone. Holding laya's checkpoint and the LLM
in memory at once is the configuration most likely to get something killed, and
the numbers are per-router anyway.

## Honest limits

- The `llm` router isolates the routing decision — one completion asking for a
  tool name. It is not the full ReAct loop Pilot runs, so its latency is a
  lower bound on what routing costs in the real agent, not an equal.
- Latency is wall clock on the machine you run it on. A GPU box says nothing
  about the phone in your pocket, which is where routing cost hurts most.
  Run it on the hardware you care about.
- ~80 hand-labelled questions is enough to catch a router that confuses `dns`
  with `dnssec`. It is not enough to publish, and it is not a held-out set —
  it was written by reading the tool catalogue.
- `questions.jsonl` encodes one person's opinion of the right answer. Some are
  genuinely arguable ("is this IP malicious" could be `tipcheck` or `shodan`).
  Disagree by editing the file; that is the point of it being data.

## Adding questions

One JSON object per line:

```json
{"q": "what nameservers does example.org use", "tool": "dns", "kind": "plain"}
```

`kind` is free-form; the report groups by whatever values it finds.
