#!/usr/bin/env python3
"""Tests for the routing benchmark itself.

A benchmark that miscounts is worse than no benchmark, so the arithmetic is
checked here: accuracy, the conditional accuracy that excludes questions the
shortlist never reached, and the confidence buckets that decide whether a
hybrid is worth building.

The laya router is exercised against a stub, because no checkpoint can be
downloaded in CI. That proves the wiring — the questions dict we hand laya and
the keys we read back — matches the API in laya 0.3.11, which is the part most
likely to rot. It does not prove anything about laya's accuracy; only a real
run on real weights can do that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_bench  # noqa: E402

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s\n     got %r, wanted %r" % (name, got, want))


class StubLaya:
    """Shaped like laya.Router: predict(state, questions) -> answers/choice."""

    def __init__(self, choice, confidence=0.91):
        self.choice = choice
        self.confidence = confidence
        self.seen = None

    def predict(self, state, questions):
        self.seen = (state, questions)
        return {"answers": {"tool": {"choice": self.choice,
                                     "confidence": self.confidence,
                                     "probabilities": {self.choice: self.confidence}}}}


def main() -> int:
    print("== parsing what a model said ==")
    shortlist = ["dns", "dnssec", "whois"]
    check("a bare name", route_bench.parse_tool_reply("dns", shortlist), "dns")
    check("a labelled answer", route_bench.parse_tool_reply("Tool: whois", shortlist), "whois")
    check("backticks", route_bench.parse_tool_reply("`dnssec`", shortlist), "dnssec")
    check("trailing prose",
          route_bench.parse_tool_reply("whois\nbecause it asks about ownership", shortlist),
          "whois")
    # Formatting is not routing: a model that said the right name in an odd
    # shape has routed correctly, and scoring that as a miss would measure the
    # wrong thing. Nothing recognisable is still a miss.
    check("nothing recognisable", route_bench.parse_tool_reply("I am not sure", shortlist), "")
    check("an empty reply", route_bench.parse_tool_reply("", shortlist), "")
    check("punctuation keeps the full name",
          route_bench.parse_tool_reply("DNSSEC.", shortlist), "dnssec")
    check("related names cannot steal a punctuated answer",
          route_bench.parse_tool_reply('"cryptocurrency".', ["crypt", "cryptocurrency"]),
          "cryptocurrency")
    check("an unavailable longer name is not its prefix",
          route_bench.parse_tool_reply("dnssec.", ["dns", "whois"]), "")
    check("partial identifiers are not tool names",
          route_bench.parse_tool_reply("use-dns-helper", shortlist), "")
    check("ambiguous answers do not favor shortlist order",
          route_bench.parse_tool_reply("dns or dnssec", shortlist), "")

    print("== the laya wiring ==")
    stub = StubLaya("dns")
    router = route_bench.make_laya_router(stub)
    pick, confidence = router("MX records for gmail.com", shortlist,
                              {"dns": "DNS lookups", "dnssec": "DNSSEC checks",
                               "whois": "domain ownership"})
    check("the choice comes back", pick, "dns")
    check("so does the confidence", confidence, 0.91)
    state, questions = stub.seen
    check("the question is the state", state, {"question": "MX records for gmail.com"})
    check("one typed question", list(questions), ["tool"])
    check("of type choice", questions["tool"]["type"], "choice")
    check("criteria are the shortlisted tools", list(questions["tool"]["criteria"]), shortlist)
    check("described, not just named",
          questions["tool"]["criteria"]["dns"], "DNS lookups")

    print("== the arithmetic ==")
    questions_set = [
        {"q": "a", "tool": "dns", "kind": "plain"},       # in shortlist, right
        {"q": "b", "tool": "whois", "kind": "plain"},     # in shortlist, wrong
        {"q": "c", "tool": "newton", "kind": "oblique"},  # never shortlisted
    ]
    shortlists = {"a": ["dns", "whois"], "b": ["dns", "whois"], "c": ["dns", "whois"]}
    descriptions = {"dns": "dns", "whois": "whois", "newton": "maths"}

    def always_dns(_q, _s, _d):
        return "dns", 0.8

    args = argparse.Namespace(repeat=1, show=0)
    result = route_bench.measure("stub", "test", always_dns, questions_set,
                                 shortlists, descriptions, args)
    # 1 of 3 overall, but the third was unreachable — so the pick itself was
    # right 1 of the 2 it could have got. Reporting only the first number
    # blames the router for the shortlist's miss.
    check("overall accuracy", round(result["top1"], 4), round(1 / 3, 4))
    check("accuracy where the shortlist reached", result["top1_reachable"], 0.5)
    check("correct count", result["correct"], 1)
    check("failures are recorded", len(result["failures"]), 2)

    print("== the confidence buckets ==")
    def confident_when_right(q, _s, _d):
        return ("dns", 0.95) if q == "a" else ("dns", 0.40)

    result = route_bench.measure("stub", "test", confident_when_right, questions_set,
                                 shortlists, descriptions, args)
    buckets = result["confidence_thresholds"]
    # Above 0.9 only the right answer survives: a third of the traffic, all of
    # it correct. That is the number a hybrid design lives or dies on.
    check("a high floor keeps only the sure answers", buckets["0.90"]["accuracy"], 1.0)
    check("and only that share of traffic", round(buckets["0.90"]["share"], 4),
          round(1 / 3, 4))
    # 0.40 is below every floor, so a lower bar does not rescue those two —
    # the share is the same. Naming this "keeps everything" would describe a
    # behaviour the assertion does not check.
    check("a lower floor still drops the unsure ones",
          round(buckets["0.50"]["share"], 4), round(1 / 3, 4))

    print("== confidence coverage includes every question ==")
    def partially_available(q, _s, _d):
        if q == "b":
            raise RuntimeError("backend unavailable")
        return "dns", (0.99 if q == "a" else None)

    result = route_bench.measure("stub", "missing confidence and errors",
                                 partially_available, questions_set,
                                 shortlists, descriptions, args)
    check("errors still count", result["errors"], 1)
    check("errors and missing confidence are escalated",
          round(result["confidence_thresholds"]["0.90"]["share"], 4), round(1 / 3, 4))
    check("retained accuracy only measures retained answers",
          result["confidence_thresholds"]["0.90"]["accuracy"], 1.0)

    calls = {}
    def fails_on_repeat(q, _s, _d):
        calls[q] = calls.get(q, 0) + 1
        # The first question gets a warm-up call before its two measured calls.
        if (q == "a" and calls[q] == 3) or (q != "a" and calls[q] == 2):
            raise RuntimeError("retry failed")
        return "dns", 0.99

    result = route_bench.measure("stub", "later repeat fails", fails_on_repeat,
                                 questions_set, shortlists, descriptions,
                                 argparse.Namespace(repeat=2, show=0))
    check("failed repeats discard earlier confidence", result["confidence_thresholds"], {})

    print("== percentiles ==")
    check("p50 of a known list", route_bench.percentile([1, 2, 3, 4, 5], 0.5), 3)
    check("p95 of a known list", route_bench.percentile([1, 2, 3, 4, 5], 0.95), 5)
    check("an empty list", route_bench.percentile([], 0.5), 0.0)

    print("")
    print("passed=%d failed=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
