#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for speaking the model's answer while it is still being written.

The model is faked, so no Ollama and no audio are needed. What matters here is
the guard: a ReAct tool call ("Thought: ... Action: gathm ...") must never be
spoken, and it is only recognisable after the first line exists.

    python3 tests/reply_stream_test.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pilot"))

import main as pilot_main  # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")


def ok(name: str, cond) -> None:
    check(name, bool(cond), True)


class Chunk:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """A chat model that yields a canned reply one word at a time."""

    def __init__(self, text: str, fail_after: int | None = None):
        self.text = text
        self.fail_after = fail_after
        self.invoked = False

    def stream(self, messages):
        words = self.text.split(" ")
        for i, w in enumerate(words):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("connection dropped")
            yield Chunk(w + (" " if i < len(words) - 1 else ""))

    def invoke(self, messages):
        self.invoked = True
        return Chunk(self.text)


class NoStreamLLM:
    def __init__(self, text: str):
        self.text = text
        self.invoked = False

    def invoke(self, messages):
        self.invoked = True
        return Chunk(self.text)


class FakeStream:
    """Stands in for lib.speech.SpeechStream — records what it was fed."""

    def __init__(self):
        self.pieces: list = []
        self.closed = False

    def feed(self, text):
        self.pieces.append(text)

    def close(self):
        self.closed = True

    def spoken(self) -> str:
        return "".join(self.pieces)


def with_sink(fn):
    def wrapper(*a, **kw):
        fake = FakeStream()
        sink = pilot_main._ReplySpeech(fake)
        pilot_main._set_token_sink(sink)
        try:
            return fn(fake, sink, *a, **kw)
        finally:
            pilot_main._set_token_sink(None)
    return wrapper


ANSWER = ("It is thirty two degrees and clear in Delhi right now, with a light "
          "breeze from the north west and no rain expected before evening.")

TOOL_CALL = ("Thought: the user wants the weather, I should use the weather "
             "tool.\nAction: gathm\nAction Input: weather delhi")


def test_plain_answer() -> None:
    print("\na plain answer is spoken as it arrives")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM(ANSWER)
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        check("full text is returned", resp.content, ANSWER)
        check("the whole answer was spoken", fake.spoken(), ANSWER)
        ok("spoken in more than one piece", len(fake.pieces) > 1)
        ok("streamed, not invoked", not llm.invoked)
        ok("sink records that it fed", sink.fed)

    run()


def test_tool_call_is_not_spoken() -> None:
    print("\na ReAct tool call is never spoken")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM(TOOL_CALL)
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        check("full text still returned to the agent", resp.content, TOOL_CALL)
        check("nothing was spoken", fake.spoken(), "")
        ok("sink knows it fed nothing", not sink.fed)

    run()


def test_short_answer() -> None:
    print("\na short answer still gets spoken")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM("Sure, done.")
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        check("text returned", resp.content, "Sure, done.")
        check("short answer spoken once at the end",
              fake.spoken(), "Sure, done.")

    run()


def test_short_tool_call() -> None:
    print("\na short tool call is still caught")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM("Action: gathm")
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        check("text returned", resp.content, "Action: gathm")
        check("nothing spoken", fake.spoken(), "")

    run()


def test_no_sink_uses_invoke() -> None:
    print("\nno speech: falls back to invoke()")
    pilot_main._set_token_sink(None)
    llm = FakeLLM(ANSWER)
    resp = pilot_main._invoke_spoken(llm, ["msg"])
    ok("invoke() was used", llm.invoked)
    check("text returned", resp.content, ANSWER)


def test_llm_without_stream() -> None:
    print("\nmodel with no stream(): falls back to invoke()")

    @with_sink
    def run(fake, sink):
        llm = NoStreamLLM(ANSWER)
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        ok("invoke() was used", llm.invoked)
        check("text returned", resp.content, ANSWER)
        check("nothing spoken (batch path speaks it later)", fake.spoken(), "")

    run()


def test_partial_failure() -> None:
    print("\na stream that dies mid-answer keeps what it got")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM(ANSWER, fail_after=8)
        resp = pilot_main._invoke_spoken(llm, ["msg"])
        ok("partial text returned", 0 < len(resp.content) < len(ANSWER))
        ok("partial text was spoken", len(fake.spoken()) > 0)

    run()


def test_immediate_failure_raises() -> None:
    print("\na stream that dies immediately is a real error")

    @with_sink
    def run(fake, sink):
        llm = FakeLLM(ANSWER, fail_after=0)
        try:
            pilot_main._invoke_spoken(llm, ["msg"])
            check("raised", False, True)
        except RuntimeError as exc:
            check("the original error propagates", str(exc), "connection dropped")

    run()


def test_content_blocks() -> None:
    print("\ncontent blocks are turned into text, not repr")
    check("plain string passes through",
          pilot_main._token_text("hello"), "hello")
    check("block list is joined",
          pilot_main._token_text([{"type": "text", "text": "hel"},
                                  {"type": "text", "text": "lo"}]), "hello")
    check("string list is joined",
          pilot_main._token_text(["a", "b"]), "ab")
    check("None is empty", pilot_main._token_text(None), "")

    @with_sink
    def run(fake, sink):
        class BlockLLM:
            def stream(self, messages):
                for w in ANSWER.split(" "):
                    yield Chunk([{"type": "text", "text": w + " "}])
        resp = pilot_main._invoke_spoken(BlockLLM(), ["msg"])
        ok("no python repr leaked into the answer",
           "{" not in resp.content and "type" not in resp.content)
        ok("blocks were spoken", len(fake.spoken()) > 0)

    run()


def test_render_response_signature() -> None:
    print("\nrender_response can be told not to speak")
    import inspect
    try:
        from pilot import tui
    except ImportError:
        import tui  # type: ignore[no-redef]
    sig = inspect.signature(tui.render_response)
    ok("speak parameter exists", "speak" in sig.parameters)
    check("speaking is still the default",
          sig.parameters["speak"].default, True)
    ok("start_reply_stream exists", hasattr(tui, "start_reply_stream"))
    ok("start_reply_stream returns None when speech is off",
       (os.environ.update({"GATHM_SPEAK": "0"}) or tui.start_reply_stream()) is None)
    os.environ.pop("GATHM_SPEAK", None)


def main() -> int:
    print("Spoken-reply streaming tests")
    print("=" * 60)
    test_plain_answer()
    test_tool_call_is_not_spoken()
    test_short_answer()
    test_short_tool_call()
    test_no_sink_uses_invoke()
    test_llm_without_stream()
    test_partial_failure()
    test_immediate_failure_raises()
    test_content_blocks()
    test_render_response_signature()
    print("=" * 60)
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
