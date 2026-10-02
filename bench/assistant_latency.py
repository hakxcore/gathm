#!/usr/bin/env python3
"""Compare full replies and first visible text against two local Gathm builds.

Run the baseline and changed API on separate loopback ports using the same
model and temperature. Requests alternate order to reduce shared model-cache
bias. This measures assistant overhead, not independent model throughput.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import statistics
import time
import urllib.request


CASES = [
    ("greeting", {"query": "hello", "history": []}),
    ("explanation", {"query": "Explain the difference between RAM and storage in two short sentences.", "history": []}),
    ("writing", {"query": "Write a two-sentence birthday greeting for a friend who loves gardening.", "history": []}),
    ("context", {"query": "What is my name? Answer briefly.", "history": [
        {"role": "user", "content": "My name is Sam."},
        {"role": "assistant", "content": "Nice to meet you, Sam."}]}),
]


def request_turn(url, payload, stream=False):
    start = time.perf_counter()
    headers = {"Content-Type": "application/json"}
    if stream:
        headers["Accept"] = "text/event-stream"
    request = urllib.request.Request(url.rstrip("/") + "/api/v1/agent/chat",
                                     data=json.dumps(payload).encode(), headers=headers)
    first = None
    result = None
    with urllib.request.urlopen(request, timeout=180) as response:
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            for line in response:
                if not line.startswith(b"data:"):
                    continue
                event = json.loads(line[5:])
                if event.get("event") == "token" and event.get("text") and first is None:
                    first = time.perf_counter() - start
                if event.get("event") == "result":
                    result = event.get("data")
                    break
        else:
            result = json.load(response)
    elapsed = time.perf_counter() - start
    sample = {"seconds": elapsed, "first_text_seconds": first if first is not None else elapsed,
              "response": result}
    if not isinstance(result, dict) or not result.get("reply") or result.get("error"):
        sample["error"] = "no successful final reply"
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-url", required=True)
    parser.add_argument("--after-url", required=True)
    parser.add_argument("--repeat", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("use at least two repetitions to alternate request order")
    report = {"platform": platform.platform(), "repeat": args.repeat,
              "before_url": args.before_url, "after_url": args.after_url,
              "warmup": [], "samples": [], "summary": {}}
    targets = [("before", args.before_url, False), ("after", args.after_url, True)]
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    def measure(label, url, stream, payload):
        try:
            return request_turn(url, payload, stream)
        except Exception as exc:
            return {"error": str(exc)}

    # Record startup separately; headline medians describe an ongoing session.
    for label, url, stream in targets:
        report["warmup"].append({"build": label, **measure(label, url, stream, CASES[0][1])})
        save()
    for iteration in range(args.repeat):
        for name, payload in CASES:
            order = targets if iteration % 2 == 0 else list(reversed(targets))
            for label, url, stream in order:
                sample = {"build": label, "case": name, "iteration": iteration,
                          **measure(label, url, stream, payload)}
                report["samples"].append(sample)
                save()
                print(json.dumps(sample), flush=True)
    for name, _payload in CASES:
        comparison = {}
        for label, _url, _stream in targets:
            samples = [r for r in report["samples"]
                       if r["build"] == label and r["case"] == name and not r.get("error")]
            comparison[label] = {"successful": len(samples)}
            if samples:
                for metric in ("seconds", "first_text_seconds"):
                    comparison[label][metric] = statistics.median(r[metric] for r in samples)
        report["summary"][name] = comparison
    save()
    print(json.dumps(report["summary"], indent=2))
    return int(any(r.get("error") for r in report["samples"] + report["warmup"]))


if __name__ == "__main__":
    raise SystemExit(main())
