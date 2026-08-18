#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single-shot, non-interactive entry point to the Pilot LLM agent.

Used by the API server (POST /api/v1/agent/chat) so the GUI can talk to the
real LangGraph + Ollama/Gemini agent instead of the bash keyword router.

Protocol:
  stdin  : JSON {"query": "...", "history": [{"role": "user"|"assistant",
                                              "content": "..."}]}
  stdout : JSON {"reply": "...", "backend": "...", "model": "..."}
           or  {"error": "..."} on failure (exit code != 0)

All of the agent's human/TUI output is redirected to stderr so stdout stays
clean JSON for the caller to parse.
"""

import json
import os
import sys

# The agent (pilot/main.py) and its TUI print to stdout during reasoning.
# Redirect stdout to stderr for the whole run; we restore the real stdout
# only to emit the final JSON line.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

# Make `import main` work regardless of the caller's CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _emit(obj: dict, code: int = 0) -> int:
    """Write a single JSON line to the real stdout and return an exit code."""
    sys.stdout = _REAL_STDOUT
    print(json.dumps(obj))
    sys.stdout.flush()
    return code


def _read_request() -> dict:
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:
        raw = ""
    raw = (raw or "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"query": raw}
    # Fallback: query as command-line args
    if len(sys.argv) > 1:
        return {"query": " ".join(sys.argv[1:])}
    return {}


def _describe_failure(exc: BaseException) -> str:
    """Turn a raw agent exception into something the user can act on.

    A dead Ollama server surfaced as bare "agent error: [Errno 111]
    Connection refused", which says nothing about what to start. Walk the
    exception chain looking for a refused/unreachable connection and name the
    server and the fix instead.
    """
    seen = []
    cur: BaseException | None = exc
    while cur is not None and len(seen) < 10:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__

    text = " | ".join("%s: %s" % (type(e).__name__, e) for e in seen).lower()
    refused = (
        "errno 111" in text
        or "connection refused" in text
        or "connectionerror" in text
        or "failed to establish a new connection" in text
        or "max retries exceeded" in text
        or "all connection attempts failed" in text
        or "cannot connect to host" in text
    )
    if not refused:
        return "agent error: %s" % exc

    backend = os.environ.get("GATHM_LLM_BACKEND", "ollama")
    if backend == "ollama":
        url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        host = url.split("/v1")[0]
        return (
            "cannot reach the Ollama server at %s — it is not running. "
            "Start it with:  ollama serve  (then retry)" % host
        )
    return (
        "cannot reach the %s LLM backend — the connection was refused. "
        "Check that it is running and reachable." % backend
    )


def main() -> int:
    req = _read_request()
    query = (req.get("query") or "").strip()
    if not query:
        return _emit({"error": "empty query"}, 1)

    # Import the agent (langchain/langgraph must be present — i.e. run me with
    # the pilot venv's python). main.py may raise SystemExit on missing deps,
    # so catch BaseException to always return clean JSON.
    try:
        import main as pilot  # pilot/main.py
    except SystemExit as exc:
        return _emit({"error": f"agent dependencies missing (code {exc.code})"}, 2)
    except BaseException as exc:  # noqa: BLE001 — report any import failure cleanly
        return _emit({"error": f"agent unavailable: {exc}"}, 2)

    if not getattr(pilot, "LANGCHAIN_AVAILABLE", False) or getattr(pilot, "app", None) is None:
        return _emit({"error": "LLM runtime not available (langchain/langgraph not installed)"}, 2)

    from langchain_core.messages import HumanMessage, AIMessage

    # Rebuild prior conversation turns for multi-turn context.
    history = []
    for turn in req.get("history", []) or []:
        role = (turn.get("role") or "").lower()
        content = turn.get("content", "")
        if not content:
            continue
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role in ("assistant", "ai", "bot"):
            history.append(AIMessage(content=content))

    state = {"messages": history + [HumanMessage(content=query)]}

    reply = None
    try:
        for output in pilot.app.stream(state, config={"recursion_limit": 25}):
            for key, value in output.items():
                if key == "agent" and value.get("next_step") == "end":
                    reply = value["messages"][-1].content
    except Exception as exc:  # noqa: BLE001
        return _emit({"error": _describe_failure(exc)}, 3)

    return _emit({
        "reply": reply or "(no response)",
        "backend": getattr(pilot, "LLM_BACKEND", "unknown"),
        "model": getattr(pilot, "OLLAMA_MODEL", "unknown"),
    })


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
