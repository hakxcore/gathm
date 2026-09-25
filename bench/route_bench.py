#!/usr/bin/env python3
"""
Gathm — tool-routing benchmark

The question this answers
-------------------------
Pilot's slowest step is not generating the answer, it is deciding which tool
the question needs. The agent has to describe its tools before the model can
choose one, and on CPU that prefill is the wait — README's own numbers put a
vague question at ~2 KB / ~500 tokens of tool text, every turn.

Tool selection is a classification problem being solved by a generative model.
This harness measures whether something cheaper does it as well:

  shortlist   the keyword scorer already in pilot/main.py, top-1 of its own
              ranking. Free, zero model calls, and nobody has ever measured it.
  llm         what Gathm does today: shortlist, then ask the configured
              backend (llama.cpp, Ollama, Gemini, Anthropic) to pick one.
  laya        shortlist, then a single non-autoregressive `choice` forward
              pass (pip install laya).

Every router sees the SAME questions and the SAME shortlist. Only the final
pick differs, which is the only way the comparison means anything.

The ceiling matters more than the winner
----------------------------------------
If the right tool is not in the shortlist, no downstream router can recover it.
So the report leads with recall@k of the shortlist stage, and reports each
router twice: raw accuracy over all questions, and accuracy over only the
questions the shortlist got right. The second number is the one that compares
the PICK; the first is what the user actually experiences.

Honesty notes
-------------
* The `llm` router isolates the routing decision — one completion asking for a
  tool name. It is not the full ReAct loop Pilot runs, so its latency is a
  lower bound on what routing costs in the real agent, not an equal.
* Latency is wall clock on THIS machine. A number measured on a GPU box says
  nothing about the phone in your pocket, which is where routing cost hurts
  most. Run it on the hardware you care about.
* The question set is hand-labelled and small (~80). It is enough to catch a
  router that confuses `dns` with `dnssec`; it is not enough to publish.

Usage
-----
    python3 bench/route_bench.py                       # every available router
    python3 bench/route_bench.py --routers shortlist   # no model needed
    python3 bench/route_bench.py --repeat 3 --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

BENCH_DIR = Path(__file__).resolve().parent
GATHM_ROOT = BENCH_DIR.parent
DEFAULT_QUESTIONS = BENCH_DIR / "questions.jsonl"

# How many tools the shortlist is allowed to keep. Mirrors Pilot's default so
# the benchmark measures the shipping configuration unless told otherwise.
SHORTLIST_K = int(os.environ.get("GATHM_TOOL_SHORTLIST", "10"))


# ---------------------------------------------------------------------------
# Loading the real thing
# ---------------------------------------------------------------------------

def load_pilot() -> Any:
    """Import pilot/main.py, because a copy of the scorer would drift.

    The whole point is to measure what ships. pilot/main.py exits at import
    without `rich`, so say that plainly rather than falling back to a
    reimplementation that would quietly benchmark different code.
    """
    sys.path.insert(0, str(GATHM_ROOT))
    sys.path.insert(0, str(GATHM_ROOT / "pilot"))
    try:
        import main as pilot_main  # type: ignore[import]
        return pilot_main
    except SystemExit:
        raise SystemExit(
            "pilot/main.py could not start — it needs `rich` at minimum.\n"
            "    pip install -r pilot/requirements.txt\n"
        )
    except ImportError as exc:
        raise SystemExit("could not import pilot/main.py: %s" % exc)


def load_questions(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise SystemExit("%s line %d is not JSON: %s" % (path, number, exc))
        if "q" not in row or "tool" not in row:
            raise SystemExit("%s line %d needs both 'q' and 'tool'" % (path, number))
        row.setdefault("kind", "plain")
        rows.append(row)
    if not rows:
        raise SystemExit("no questions in %s" % path)
    return rows


def tool_descriptions(pilot: Any, names: list[str]) -> dict[str, str]:
    """One line per tool, as the model (or laya) would see it."""
    out = {}
    for name in names:
        try:
            text = pilot.get_tool_description(name) or ""
        except Exception:
            text = ""
        out[name] = " ".join(text.split())[:160] or name
    return out


# ---------------------------------------------------------------------------
# Routers. Each takes (question, shortlist, descriptions) and returns a name.
# ---------------------------------------------------------------------------

# Every router returns (pick, confidence). Confidence is None where the router
# has no honest number to give — a generative model asked for a tool name has
# no calibrated probability behind it, and inventing one would be worse than
# admitting the gap.

def make_shortlist_router() -> Callable:
    """The zero-cost baseline: whatever the keyword scorer ranked first.

    Worth measuring on its own because if it is already right most of the time,
    every model call underneath it is being spent to confirm a decision that
    was free.
    """
    def route(_question: str, shortlist: list[str], _descriptions: dict):
        return (shortlist[0] if shortlist else ""), None
    return route


LLM_INSTRUCTIONS = (
    "You route a user's question to exactly one tool. "
    "Answer with the tool name alone — no punctuation, no explanation. "
    "If no tool fits, answer: none"
)


def make_llm_router(provider: Any) -> Callable:
    def route(question: str, shortlist: list[str], descriptions: dict) -> str:
        catalogue = "\n".join("%s: %s" % (n, descriptions[n]) for n in shortlist)
        messages = [
            {"role": "system", "content": LLM_INSTRUCTIONS},
            {"role": "user",
             "content": "Tools:\n%s\n\nQuestion: %s\nTool:" % (catalogue, question)},
        ]
        reply = provider.complete(messages, max_tokens=16)
        return parse_tool_reply(reply, shortlist), None
    return route


def parse_tool_reply(reply: str, shortlist: list[str]) -> str:
    """Pull a tool name out of whatever the model said.

    Generous on purpose: a model that answers "Tool: dns" or "`dns`" has
    routed correctly, and scoring that as a miss would measure formatting
    rather than routing. Anything with no recognisable name is a real miss.
    """
    # Match complete identifiers: punctuation after "dnssec" must not turn
    # it into "dns" merely because dns occurs first in the shortlist.
    tokens = set(re.findall(r"[a-z0-9_-]+", (reply or "").lower()))
    matches = [name for name in shortlist if name.lower() in tokens]
    # Multiple distinct names are not a single routing decision. Choosing in
    # shortlist order would bias the measured accuracy toward the baseline.
    return matches[0] if len(matches) == 1 else ""


def make_laya_router(model: Any) -> Callable:
    def route(question: str, shortlist: list[str], descriptions: dict) -> str:
        questions = {
            "tool": {
                "type": "choice",
                "instructions": "Which tool should answer this question?",
                "criteria": {name: descriptions[name] for name in shortlist},
            }
        }
        result = model.predict({"question": question}, questions)
        answer = result["answers"]["tool"]
        # laya returns a calibrated confidence, which is the thing that makes a
        # hybrid possible: route locally when it is sure, spend the LLM only on
        # the rest. measure() reports what that trade would buy.
        return answer["choice"], answer.get("confidence")
    return route


# ---------------------------------------------------------------------------
# Building the optional routers, each skipping cleanly when unavailable
# ---------------------------------------------------------------------------

def build_llm_router() -> tuple[Optional[Callable], str]:
    try:
        sys.path.insert(0, str(GATHM_ROOT))
        from lib.llm import LLMConfig, LLMProvider  # type: ignore[import]
    except Exception as exc:
        return None, "lib/llm.py did not import (%s)" % exc
    try:
        config = LLMConfig.from_env()
        provider = LLMProvider(config)
        # One real call, so an unreachable backend is reported here rather than
        # as 80 identical failures inside the run.
        provider.complete([{"role": "user", "content": "reply with: ok"}],
                          max_tokens=8)
    except Exception as exc:
        return None, "backend not answering (%s)" % str(exc)[:120]
    return make_llm_router(provider), "%s / %s" % (config.backend, config.model)


def build_laya_router() -> tuple[Optional[Callable], str]:
    try:
        import laya  # type: ignore[import]
    except ImportError:
        return None, "not installed (pip install laya)"

    # Defaults are laya's own recommendation and right on a laptop: preload
    # both checkpoints so nothing reloads mid-run. On a phone they are wrong.
    # Two resident checkpoints is several hundred MB on top of whatever is
    # already holding the LLM, and Android's low-memory killer reaps the
    # biggest process without ceremony — which would look like a crashed
    # benchmark rather than what it is.
    #
    #   GATHM_BENCH_LAYA_PRELOAD=0   load on first use instead
    #   GATHM_BENCH_LAYA_MAX_LOADED=1   keep one checkpoint, not two
    #
    # An English-only question set never needs the multilingual checkpoint, so
    # max_loaded=1 costs nothing here beyond a reload if you mix languages in.
    preload = (os.environ.get("GATHM_BENCH_LAYA_PRELOAD", "1").strip()
               not in ("0", "false", "no"))
    try:
        max_loaded = int(os.environ.get("GATHM_BENCH_LAYA_MAX_LOADED", "") or 2)
    except ValueError:
        max_loaded = 2

    try:
        model = laya.Router(preload=preload, max_loaded=max(1, max_loaded))
    except MemoryError:
        return None, ("out of memory loading a checkpoint — retry with "
                      "GATHM_BENCH_LAYA_PRELOAD=0 GATHM_BENCH_LAYA_MAX_LOADED=1")
    except Exception as exc:
        return None, "could not load a checkpoint (%s)" % str(exc)[:120]

    note = "laya %s" % getattr(laya, "__version__", "?")
    if not preload:
        note += ", lazy"
    if max_loaded != 2:
        note += ", max_loaded=%d" % max_loaded
    return make_laya_router(model), note


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def run(args: argparse.Namespace) -> int:
    pilot = load_pilot()
    questions = load_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]

    all_tools = pilot.discover_tools()
    print("catalogue : %d tools" % len(all_tools))
    print("questions : %d" % len(questions))
    print("shortlist : top %d (GATHM_TOOL_SHORTLIST)" % SHORTLIST_K)
    print("")

    # --- Stage 1, shared by every router -------------------------------
    shortlists: dict[str, list[str]] = {}
    shortlist_ms: list[float] = []
    in_shortlist = 0
    missing: list[tuple[str, str]] = []
    for row in questions:
        start = time.perf_counter()
        picked = pilot._shortlist_tools(row["q"], all_tools)
        shortlist_ms.append((time.perf_counter() - start) * 1000)
        shortlists[row["q"]] = picked
        if row["tool"] in picked:
            in_shortlist += 1
        else:
            missing.append((row["q"], row["tool"]))

    recall = in_shortlist / len(questions)
    print("Stage 1 — the keyword shortlist (the ceiling for everything below)")
    print("  recall@%-2d : %.1f%% (%d/%d)   median %.2f ms"
          % (SHORTLIST_K, recall * 100, in_shortlist, len(questions),
             statistics.median(shortlist_ms)))
    if missing:
        print("  the shortlist never offered the right tool for:")
        for question, tool in missing[: args.show]:
            print("    %-52s wanted %s" % (question[:52], tool))
        if len(missing) > args.show:
            print("    ... and %d more" % (len(missing) - args.show))
    print("")

    descriptions = tool_descriptions(pilot, all_tools)

    # --- Stage 2 -------------------------------------------------------
    wanted = [name.strip() for name in args.routers.split(",") if name.strip()]
    builders = {
        "shortlist": lambda: (make_shortlist_router(), "keyword scorer, no model"),
        "llm": build_llm_router,
        "laya": build_laya_router,
    }

    results: dict[str, dict] = {}
    for name in wanted:
        if name not in builders:
            print("unknown router %r — choose from %s" % (name, ", ".join(builders)))
            return 2
        router, note = builders[name]()
        if router is None:
            print("Router %-10s SKIPPED — %s" % (name, note))
            print("")
            continue
        results[name] = measure(name, note, router, questions, shortlists,
                                descriptions, args)

    if args.json and results:
        payload = {
            "catalogue": len(all_tools),
            "questions": len(questions),
            "shortlist_k": SHORTLIST_K,
            "shortlist_recall": recall,
            "shortlist_median_ms": statistics.median(shortlist_ms),
            "routers": results,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("wrote %s" % args.json)

    return 0


def measure(name: str, note: str, router: Callable, questions: list[dict],
            shortlists: dict, descriptions: dict, args: argparse.Namespace) -> dict:
    print("Router %s  (%s)" % (name, note))

    # A warm-up that is thrown away: the first call pays lazy imports, a cold
    # KV cache, or a checkpoint being paged in, and folding that into the
    # median would libel whichever router happened to go first. This is doing
    # real work when GATHM_BENCH_LAYA_PRELOAD=0 — the checkpoint loads here.
    try:
        first = questions[0]
        router(first["q"], shortlists[first["q"]], descriptions)
    except Exception:
        pass

    latencies: list[float] = []
    correct = 0
    correct_reachable = 0
    reachable = 0
    by_kind: dict[str, list[int]] = {}
    failures: list[tuple[str, str, str]] = []
    confident: list[tuple[float, int]] = []      # (confidence, was it right)
    errors = 0

    for row in questions:
        question, gold = row["q"], row["tool"]
        shortlist = shortlists[question]
        best, confidence = "", None
        for _ in range(max(1, args.repeat)):
            start = time.perf_counter()
            try:
                best, confidence = router(question, shortlist, descriptions)
            except Exception as exc:               # a router that throws is a miss
                errors += 1
                best = "error: %s" % str(exc)[:60]
                confidence = None                 # discard a previous repeat's score
                latencies.append((time.perf_counter() - start) * 1000)
                break
            latencies.append((time.perf_counter() - start) * 1000)

        hit = int(best == gold)
        if confidence is not None:
            confident.append((float(confidence), hit))
        correct += hit
        by_kind.setdefault(row["kind"], []).append(hit)
        if gold in shortlist:
            reachable += 1
            correct_reachable += hit
        if not hit:
            failures.append((question, gold, best or "(nothing)"))

    total = len(questions)
    accuracy = correct / total
    conditional = (correct_reachable / reachable) if reachable else 0.0

    print("  top-1     : %.1f%% (%d/%d) overall" % (accuracy * 100, correct, total))
    print("  of those the shortlist reached: %.1f%% (%d/%d)"
          % (conditional * 100, correct_reachable, reachable))
    for kind in sorted(by_kind):
        hits = by_kind[kind]
        print("      %-11s %.1f%% (%d/%d)"
              % (kind, 100 * sum(hits) / len(hits), sum(hits), len(hits)))
    print("  latency   : p50 %.1f ms   p95 %.1f ms   total %.1f s"
          % (percentile(latencies, 0.50), percentile(latencies, 0.95),
             sum(latencies) / 1000))
    if errors:
        print("  errors    : %d calls raised" % errors)

    # The hybrid question: if this router escalated its unsure answers to the
    # LLM, how much traffic would it keep and how right would it be on that?
    thresholds: dict[str, dict] = {}
    if confident:
        print("  if unsure answers were escalated to the LLM instead:")
        for floor in (0.9, 0.75, 0.5):
            kept = [hit for score, hit in confident if score >= floor]
            if not kept:
                continue
            # Errors and answers without confidence require escalation too.
            share = len(kept) / total
            precision = sum(kept) / len(kept)
            thresholds["%.2f" % floor] = {"share": share, "accuracy": precision}
            print("      confidence >= %.2f : keeps %.0f%% of questions, %.1f%% right"
                  % (floor, share * 100, precision * 100))
    if failures and args.show:
        print("  wrong picks:")
        for question, gold, got in failures[: args.show]:
            print("    %-46s wanted %-14s got %s" % (question[:46], gold, got))
        if len(failures) > args.show:
            print("    ... and %d more" % (len(failures) - args.show))
    print("")

    return {
        "note": note,
        "top1": accuracy,
        "top1_reachable": conditional,
        "correct": correct,
        "total": total,
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "errors": errors,
        "by_kind": {k: sum(v) / len(v) for k, v in by_kind.items()},
        "confidence_thresholds": thresholds,
        "failures": [{"q": q, "wanted": g, "got": b} for q, g, b in failures],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Gathm's tool routing: keyword vs LLM vs laya.")
    parser.add_argument("--routers", default="shortlist,llm,laya",
                        help="comma-separated: shortlist, llm, laya")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--repeat", type=int, default=1,
                        help="calls per question; more is a steadier latency figure")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    parser.add_argument("--show", type=int, default=8, help="how many failures to print")
    parser.add_argument("--json", default="", help="write the full result here")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
