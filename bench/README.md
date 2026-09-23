# Routing benchmark

Pilot's slowest step is not writing the answer — it is deciding which tool the
question needs. The agent has to describe its tools before the model can choose
one, and on CPU that prefill is the wait. The repo's own figures put a vague
question at ~2 KB / ~500 tokens of tool text, every turn.

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

A router that is unavailable is skipped with the reason, so the harness runs
with no LLM, with no laya, or with neither.

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

Where a router reports calibrated confidence, the harness also prints what a
**hybrid** would buy: route locally when the model is sure, spend the LLM only
on the rest. That is the shape a real integration would take, so it is the
number worth optimising.

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
