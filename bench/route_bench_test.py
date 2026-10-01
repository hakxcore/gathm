#!/usr/bin/env python3
"""Tests for the routing benchmark itself.

A benchmark that miscounts is worse than no benchmark, so the arithmetic is
checked here: accuracy, the conditional accuracy that excludes questions the
shortlist never reached, and the confidence buckets that decide whether a
hybrid is worth building.

The laya router is exercised against a stub, because no checkpoint can be
downloaded in CI. That proves the wiring — the questions dict we hand laya and
the keys we read back — matches the current choice-answer contract. Unlike
the old 0.3.11 assumption, the selected answer probability comes from
answer_confidence or probabilities[choice], not the entropy-based confidence.
These tests do not prove laya's accuracy; only real weights can do that.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

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


class AnswerLaya:
    def __init__(self, answer):
        self.answer = answer

    def predict(self, _state, _questions):
        return {"answers": {"tool": self.answer}}


def rejects_answer(answer, shortlist):
    try:
        route_bench.make_laya_router(AnswerLaya(answer))(
            "test question", shortlist, {name: name for name in shortlist})
    except (TypeError, ValueError):
        return True
    return False


def exercise_run(routers, laya_builder, llm_builder=None):
    """Run the real reporting path with no pilot import or model download."""
    pilot = types.SimpleNamespace(
        discover_tools=lambda: ["dns"],
        _shortlist_tools=lambda _q, _tools: ["dns"],
        get_tool_description=lambda _name: "DNS lookup",
    )
    questions = [{"q": "lookup example.org", "tool": "dns", "kind": "plain"}]
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "result.json"
        args = argparse.Namespace(questions="unused", limit=0, show=0,
                                  routers=routers, repeat=1, json=str(output))
        with patch.object(route_bench, "load_pilot", return_value=pilot), \
                patch.object(route_bench, "load_questions", return_value=questions), \
                patch.object(route_bench, "build_laya_router", return_value=laya_builder), \
                patch.object(route_bench, "build_llm_router",
                             return_value=llm_builder or (None, "backend unavailable")), \
                contextlib.redirect_stdout(io.StringIO()):
            status = route_bench.run(args)
        payload = json.loads(output.read_text()) if output.exists() else None
    return status, payload


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

    print("== answer probability is distinct from entropy confidence ==")
    def answer_score(answer):
        return route_bench.make_laya_router(AnswerLaya(answer))(
            "test question", shortlist, {name: name for name in shortlist})[1]

    check("selected-answer confidence takes precedence",
          answer_score({"choice": "dns", "answer_confidence": 0.82,
                        "confidence": 0.99, "probabilities": {"dns": 0.61}}), 0.82)
    check("selected-choice probability is the fallback",
          answer_score({"choice": "dns", "confidence": 0.99,
                        "probabilities": {"dns": 0.61, "whois": 0.39}}), 0.61)
    check("null answer confidence still falls back",
          answer_score({"choice": "dns", "answer_confidence": None,
                        "probabilities": {"dns": 0.61}}), 0.61)
    check("legacy entropy confidence alone is not an answer probability",
          answer_score({"choice": "dns", "confidence": 0.99}), None)
    check("another choice's probability is not borrowed",
          answer_score({"choice": "dns", "confidence": 0.99,
                        "probabilities": {"whois": 0.99}}), None)
    for boundary in (0.0, 1.0):
        check("probability boundary %s" % boundary,
              answer_score({"choice": "dns", "answer_confidence": boundary}), boundary)
    for invalid in (True, False, float("nan"), float("inf"), -float("inf"),
                    -0.01, 1.01, "not a probability"):
        check("invalid selected-answer confidence %r is rejected" % invalid,
              rejects_answer({"choice": "dns", "answer_confidence": invalid,
                              "probabilities": {"dns": 0.8}}, shortlist), True)
        check("invalid fallback probability %r is rejected" % invalid,
              rejects_answer({"choice": "dns", "probabilities": {"dns": invalid}},
                             shortlist), True)
    check("a choice outside the shortlist is rejected",
          rejects_answer({"choice": "shell", "answer_confidence": 0.99}, shortlist), True)

    print("== optional laya loading stays lazy unless requested ==")
    constructor_calls = []
    def fake_constructor(**kwargs):
        constructor_calls.append(kwargs)
        return StubLaya("dns")

    fake_module = types.SimpleNamespace(Router=fake_constructor, __version__="test")
    with patch.dict(sys.modules, {"laya": fake_module}):
        for value, expected in ((None, False), ("0", False), ("FALSE", False),
                                (" No ", False), ("1", True), ("TrUe", True)):
            environment = {} if value is None else {"GATHM_BENCH_LAYA_PRELOAD": value}
            with patch.dict(route_bench.os.environ, environment, clear=True):
                built, _note = route_bench.build_laya_router()
            check("preload setting %r" % value, constructor_calls[-1]["preload"], expected)
            check("stub router is usable with preload %r" % value,
                  built("q", shortlist, {name: name for name in shortlist}), ("dns", 0.91))

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

    check("successful repeats are counted", result["successful_calls"], 3)
    check("failed repeats are counted", result["failed_calls"], 3)

    print("== failed calls cannot look like fast model inference ==")
    clock = [0.0]
    def timed_router(q, _s, _d):
        clock[0] += {"a": 0.020, "b": 0.001, "c": 0.080}[q]
        if q == "b":
            raise RuntimeError("failed before inference")
        return "dns", 0.8

    with patch.object(route_bench.time, "perf_counter", side_effect=lambda: clock[0]):
        result = route_bench.measure("stub", "timed errors", timed_router,
                                     questions_set, shortlists, descriptions, args)
    check("failed duration excluded from p50", round(result["p50_ms"], 3), 20.0)
    check("successful duration retained in p95", round(result["p95_ms"], 3), 80.0)
    check("successful call count excludes warm-up", result["successful_calls"], 2)
    check("failed call count", result["failed_calls"], 1)

    def always_fails(_q, _s, _d):
        raise RuntimeError("offline")

    result = route_bench.measure("stub", "unavailable", always_fails,
                                 questions_set, shortlists, descriptions, args)
    check("all failed calls have no median", result["p50_ms"], None)
    check("all failed calls have no p95", result["p95_ms"], None)
    check("no successful inference is reported", result["successful_calls"], 0)
    check("each failed question is counted", result["failed_calls"], 3)

    for invalid in (True, float("nan"), float("inf"), -0.01, 1.01, "bad"):
        def malformed_router(_q, _s, _d):
            return "dns", invalid
        with contextlib.redirect_stdout(io.StringIO()):
            result = route_bench.measure("stub", "malformed confidence", malformed_router,
                                         questions_set, shortlists, descriptions, args)
        check("measure handles invalid confidence %r as per-call errors" % invalid,
              (result["errors"], result["successful_calls"], result["failed_calls"],
               result["confidence_thresholds"], result["p50_ms"]),
              (3, 0, 3, {}, None))

    print("== command status and JSON record unavailable routers ==")
    status, payload = exercise_run("laya", (None, "laya is unavailable"))
    check("an entirely skipped run fails", status != 0, True)
    check("an entirely skipped run still writes JSON", payload is not None, True)
    if payload is not None:
        check("skipped run has no router results", payload["routers"], {})
        check("skip reason is preserved", payload["skipped"], {"laya": "laya is unavailable"})

    status, payload = exercise_run("shortlist,laya", (None, "laya is unavailable"))
    check("a working router alongside a skip succeeds", status, 0)
    check("working and skipped routers are both retained",
          (sorted(payload["routers"]), payload["skipped"]),
          (["shortlist"], {"laya": "laya is unavailable"}))

    status, payload = exercise_run("laya", (always_fails, "model failure"))
    check("router errors fail the command", status != 0, True)
    check("router errors remain available in JSON", payload["routers"]["laya"]["errors"], 1)
    check("an attempted router is not a skipped router", payload["skipped"], {})

    status, payload = exercise_run("shortlist,laya", (always_fails, "model failure"))
    check("a successful router cannot hide another router's errors", status != 0, True)
    check("mixed results remain available for diagnosis", sorted(payload["routers"]),
          ["laya", "shortlist"])

    print("== percentiles ==")
    check("p50 of a known list", route_bench.percentile([1, 2, 3, 4, 5], 0.5), 3)
    check("p95 of a known list", route_bench.percentile([1, 2, 3, 4, 5], 0.95), 5)
    check("an empty list", route_bench.percentile([], 0.5), 0.0)

    print("")
    print("passed=%d failed=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
