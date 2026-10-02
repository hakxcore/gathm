#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Non-interactive entry point to the Pilot LLM agent.

Used by the API server (POST /api/v1/agent/chat) so the GUI can talk to the
real LangGraph agent — on whichever backend lib/llm.py resolves (llama.cpp,
Ollama, Gemini, Anthropic) — instead of the bash keyword router.

Protocol:
  stdin  : JSON {"query": "...", "history": [{"role": "user"|"assistant",
                                              "content": "..."}]}
  stdout : JSON {"reply": "...", "backend": "...", "model": "..."}
           or  {"error": "..."} on failure (exit code != 0)

With --worker, each stdin line is an independent request. stdout emits JSON
lines with event="token" when streaming was requested, then event="result"
and the same reply/error object in data. History must be supplied every turn.

All of the agent's human/TUI output is redirected to stderr so stdout stays
clean JSON for the caller to parse.
"""

import json
import os
import sys

# The agent (pilot/main.py) and its TUI print to stdout during reasoning.
# Keep stdout redirected for the whole run. Protocol writes use the saved
# stream directly, so later tool output cannot corrupt a worker response.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

# Make `import main` work regardless of the caller's CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _result(obj: dict, code: int = 0):
    return obj, code


def _write(obj: dict) -> None:
    _REAL_STDOUT.write(json.dumps(obj) + "\n")
    _REAL_STDOUT.flush()


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
    """Delegate to the agent's describer so both paths say the same thing."""
    try:
        from main import describe_agent_failure  # type: ignore[import]
    except ImportError:
        try:
            from pilot.main import describe_agent_failure  # type: ignore[import]
        except ImportError:
            return "agent error: %s" % exc
    return describe_agent_failure(exc)

def run_turn(req: dict, on_token=None):
    query = (req.get("query") or "").strip()
    if not query:
        return _result({"error": "empty query"}, 1)

    # Import the agent (langchain/langgraph must be present — i.e. run me with
    # the pilot venv's python). main.py may raise SystemExit on missing deps,
    # so catch BaseException to always return clean JSON.
    try:
        import main as pilot  # pilot/main.py
    except SystemExit as exc:
        return _result({"error": f"agent dependencies missing (code {exc.code})"}, 2)
    except BaseException as exc:  # noqa: BLE001 — report any import failure cleanly
        return _result({"error": f"agent unavailable: {exc}"}, 2)

    if not getattr(pilot, "LANGCHAIN_AVAILABLE", False) or getattr(pilot, "app", None) is None:
        return _result({"error": "LLM runtime not available (langchain/langgraph not installed)"}, 2)

    from langchain_core.messages import HumanMessage, AIMessage

    if on_token is not None:
        class TokenSink:
            def feed(self, text):
                on_token(text)
        pilot._set_token_sink(TokenSink())

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
        last_tool_output = ""
        for output in pilot.app.stream(state, config={"recursion_limit": pilot.AGENT_MAX_STEPS}):
            for key, value in output.items():
                messages = value.get("messages") or []
                if messages:
                    seen = str(getattr(messages[-1], "content", "") or "")
                    if seen.startswith("Observation:"):
                        last_tool_output = seen[len("Observation:"):].strip()
                # The tool node ends the turn too, not just the agent —
                # checking only "agent" silently dropped its answer.
                if value.get("next_step") == "end" and messages:
                    reply = messages[-1].content
    except Exception as exc:  # noqa: BLE001
        # A loop that will not converge is not an error to hand the GUI. The
        # tool ran and its output is in hand; that is a better answer than a
        # LangGraph stack trace with a documentation URL.
        if pilot._is_recursion_limit(exc) and last_tool_output:
            return _result({
                "reply": last_tool_output,
                "backend": getattr(pilot, "LLM_BACKEND", "unknown"),
                "model": getattr(pilot, "OLLAMA_MODEL", "unknown"),
            })
        return _result({"error": _describe_failure(exc)}, 3)

    return _result({
        "reply": reply or "(no response)",
        "backend": getattr(pilot, "LLM_BACKEND", "unknown"),
        "model": getattr(pilot, "OLLAMA_MODEL", "unknown"),
    })


def _cleanup_turn():
    pilot = sys.modules.get("main")
    if pilot is not None:
        pilot._set_token_sink(None)
    # Browser sessions held cookies and page state only within the old
    # one-shot process. Preserve that boundary across reusable-worker turns.
    for name in ("pilot.browser", "browser"):
        browser = sys.modules.get(name)
        if browser is not None:
            try:
                browser.close_session()
            except Exception:
                pass


def main() -> int:
    if "--worker" not in sys.argv[1:]:
        result, code = run_turn(_read_request())
        _write(result)
        return code

    # JSON lines keep the child alive without retaining conversation history.
    # Never prompt from this protocol stream, even when launched by hand.
    os.environ["GATHM_NON_INTERACTIVE"] = "1"
    for raw in sys.stdin:
        try:
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise ValueError("request must be an object")
            callback = (lambda text: _write({"event": "token", "text": text})) if req.get("stream") else None
            result, _code = run_turn(req, callback)
        except Exception as exc:
            result = {"error": _describe_failure(exc)}
        finally:
            _cleanup_turn()
        _write({"event": "result", "data": result})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
