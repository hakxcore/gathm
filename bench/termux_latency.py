#!/usr/bin/env python3
"""Measure startup and model latency on the device running Gathm (stdlib only)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid


def timed_command(command, root, timeout=60):
    start = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                timeout=timeout)
        return {"seconds": time.perf_counter() - start,
                "returncode": result.returncode,
                "stderr": result.stderr[-1000:] if result.returncode else ""}
    except subprocess.TimeoutExpired:
        return {"seconds": time.perf_counter() - start, "error": "timeout"}


def model_request(url, messages, max_tokens=32, cache_prompt=True):
    """First *content* token, wall time, and llama.cpp's own token timings."""
    payload = {"messages": messages, "stream": True, "temperature": 0,
               "max_tokens": max_tokens, "cache_prompt": cache_prompt}
    request = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    first = None
    reply = []
    timings = {}
    usage = {}
    with urllib.request.urlopen(request, timeout=180) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            event = json.loads(line[5:])
            timings = event.get("timings") or timings
            usage = event.get("usage") or usage
            for choice in event.get("choices", []):
                piece = choice.get("delta", {}).get("content") or ""
                if piece:
                    if first is None:
                        first = time.perf_counter() - start
                    reply.append(piece)
    return {"seconds": time.perf_counter() - start,
            "first_token_seconds": first, "reply": "".join(reply),
            "timings": timings, "usage": usage}


def pilot_prompt(root, query):
    """Capture the real Pilot prompt without executing tools or generating text."""
    code = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from pilot import main as p
from langchain_core.messages import HumanMessage, AIMessage
captured = []
p._build_llm = lambda: None
p.current_connectivity = lambda: "online"
def capture(llm, messages):
    captured.extend({"role": "assistant" if m.type == "ai" else "user",
                     "content": m.content} for m in messages)
    return AIMessage(content="Done.")
p._invoke_spoken = capture
p.call_model({"messages": [HumanMessage(content=sys.argv[2])]})
print(json.dumps(captured))
'''
    result = subprocess.run([sys.executable, "-c", code, str(root), query],
                            capture_output=True, text=True, cwd=root, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[-1000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model-url", help="Local OpenAI-compatible base URL, e.g. http://127.0.0.1:8081/v1")
    parser.add_argument("--api-url", help="Gathm API origin, e.g. http://127.0.0.1:8080")
    parser.add_argument("--prompt-cache", action="store_true",
                        help="Compare changing real Pilot prompts; generates one token, executes no tools")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.prompt_cache and not args.model_url:
        parser.error("--prompt-cache requires --model-url")
    root = args.root.resolve()
    report = {"root": str(root), "python": sys.version, "platform": platform.platform(),
              "cpu_count": os.cpu_count(), "samples": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save(label, record):
        report["samples"].setdefault(label, []).append(record)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(label, json.dumps(record), flush=True)

    for label, command in [
        ("cli_version", ["bash", str(root / "gathm"), "--version"]),
        ("tool_catalog", ["bash", "-c", 'source "$1/lib/schema.bash"; list_tools_json', "bench", str(root)]),
        ("pilot_import", [sys.executable, "-c", "from pilot import main"]),
    ]:
        for _ in range(args.repeat):
            save(label, timed_command(command, root))
    if args.model_url:
        messages = [{"role": "user", "content": "Count from one to twenty, using words."}]
        for _ in range(args.repeat):
            try:
                save("model_no_prompt_cache", model_request(args.model_url, messages, cache_prompt=False))
            except Exception as exc:
                save("model_no_prompt_cache", {"error": str(exc)})
        if args.prompt_cache:
            # A unique prefix prevents old interactive sessions from supplying
            # an already-cached prompt. Each question is also unique so exact
            # prompt replay cannot masquerade as shared-prefix reuse.
            cohort = uuid.uuid4().hex
            prompts = [(q, pilot_prompt(root, q)) for q in
                       ("weather in Mumbai", "MX records for example.com",
                        "convert 10 USD to EUR", "whois example.org")]
            for repeat in range(args.repeat):
                for query, messages in prompts:
                    messages = [dict(message) for message in messages]
                    messages[0]["content"] = f"Benchmark {cohort}.\n" + messages[0]["content"]
                    messages[-1]["content"] += f"\nBenchmark sample {repeat}."
                    try:
                        sample = model_request(args.model_url, messages, max_tokens=1)
                        sample.update(query=query, iteration=repeat,
                                      prompt_characters=sum(len(m["content"]) for m in messages))
                        save("pilot_prompt_cache", sample)
                    except Exception as exc:
                        save("pilot_prompt_cache", {"error": str(exc), "query": query})
    if args.api_url:
        for _ in range(args.repeat):
            start = time.perf_counter()
            try:
                request = urllib.request.Request(
                    args.api_url.rstrip("/") + "/api/v1/agent/chat",
                    data=json.dumps({"query": "hello", "history": []}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = json.load(response)
                sample = {"seconds": time.perf_counter() - start, "response": data}
                if data.get("error"):
                    sample["error"] = data["error"]
                save("api_greeting", sample)
            except Exception as exc:
                save("api_greeting", {"seconds": time.perf_counter() - start, "error": str(exc)})
    for label, samples in report["samples"].items():
        valid = [x["seconds"] for x in samples
                 if "seconds" in x and not x.get("error") and x.get("returncode", 0) == 0]
        if valid:
            print(f"{label}: median {statistics.median(valid):.3f}s ({len(valid)} successful samples)")
    return int(any(x.get("error") or x.get("returncode", 0)
                   for samples in report["samples"].values() for x in samples))


if __name__ == "__main__":
    raise SystemExit(main())
