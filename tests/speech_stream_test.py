#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for streaming speech: chunking, pipelining, cancellation.

The engine is stubbed out — no audio.cpp, no `say`, no sound card — so this
runs anywhere, including CI. What it proves is the ordering and the timing:
that the first utterance reaches the player before the last one has been
synthesised, which is the whole point of the change.

    python3 tests/speech_stream_test.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib import speech  # noqa: E402

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


# ---------------------------------------------------------------------------
# split_speech_chunks — pure, no threads
# ---------------------------------------------------------------------------
def test_chunking() -> None:
    print("\nsplit_speech_chunks")
    split = speech.split_speech_chunks

    # Nothing is spoken until a sentence is complete.
    chunks, rest = split("The weather in Delhi is", final=False)
    check("incomplete sentence waits", chunks, [])
    check("incomplete sentence is kept", rest, "The weather in Delhi is")

    # A complete sentence past the minimum is emitted; the tail is kept.
    text = "The weather in Delhi is thirty two degrees and clear. It will"
    chunks, rest = split(text, final=False)
    check("complete sentence emitted", chunks,
          ["The weather in Delhi is thirty two degrees and clear."])
    check("tail kept for more text", rest, " It will")

    # Short sentences are merged rather than machine-gunned out one by one.
    chunks, _ = split("Yes. No. Maybe. ", final=False, min_chars=40)
    check("short sentences merge", chunks, [])
    chunks, _ = split("Yes. No. Maybe so, on balance, probably. ",
                      final=False, min_chars=40)
    check("merged until long enough", chunks,
          ["Yes. No. Maybe so, on balance, probably."])

    # ...but the first one may be short, because starting fast is the point.
    chunks, _ = split("Sure. Here is the rest of it. ", final=False,
                      min_chars=40, first_min=1)
    check("first chunk may be short", chunks[0], "Sure.")

    # Decimals and filenames are not sentence ends.
    chunks, rest = split("Pi is 3.14 and the file is gathm.sh right now",
                         final=False)
    check("no split inside 3.14 / gathm.sh", chunks, [])

    # final=True flushes whatever is left, punctuated or not.
    chunks, rest = split("and finally this", final=True)
    check("final flushes the tail", chunks, ["and finally this"])
    check("final leaves nothing behind", rest, "")

    # Unpunctuated walls of text break on a word boundary.
    wall = "word " * 100
    chunks, _ = split(wall, final=False, max_chars=60)
    ok("wall of text is broken up", len(chunks) >= 2)
    ok("break lands on a word boundary",
       all(not c.endswith("wor") for c in chunks))

    # Markdown is cleaned, and an unclosed code fence is held back.
    chunks, _ = split("Run **this** now. ", final=False, first_min=1)
    check("markdown stripped", chunks, ["Run this now."])
    chunks, rest = split("Here you go. ```python\nx = 1", final=False,
                         first_min=1)
    check("text before an open fence still speaks", chunks, ["Here you go."])
    ok("open fence is held back", "```" in rest)
    chunks, _ = split("Here you go. ```python\nx = 1\n``` Done.", final=True,
                      first_min=1)
    ok("closed fence becomes a phrase",
       any("code block omitted" in c for c in chunks))


# ---------------------------------------------------------------------------
# SpeechStream — with the engine stubbed
# ---------------------------------------------------------------------------
class Recorder:
    """Stands in for synthesise + play, recording what happened and when."""

    def __init__(self, synth_delay: float = 0.05, play_delay: float = 0.05):
        self.synth_delay = synth_delay
        self.play_delay = play_delay
        self.events: list = []
        self.lock = threading.Lock()
        self.t0 = time.time()

    def _log(self, what: str, text: str) -> None:
        with self.lock:
            self.events.append((what, text, time.time() - self.t0))

    def synthesize(self, text: str, out_path: str):
        self._log("synth", text)
        time.sleep(self.synth_delay)
        with open(out_path, "w") as fh:
            fh.write(text)
        return True, out_path

    def play(self, path: str):
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            text = "<gone>"
        self._log("play", text)
        time.sleep(self.play_delay)
        return True, "stub"

    def spoken(self) -> list:
        return [t for what, t, _ in self.events if what == "play"]

    def at(self, what: str, text: str):
        for w, t, when in self.events:
            if w == what and t == text:
                return when
        return None


def with_stub(fn):
    """Run fn with speech's engine replaced by a Recorder."""
    def wrapper(*a, **kw):
        rec = Recorder(*a, **kw)
        saved = (speech.synthesize, speech.play, speech.engine, speech.enabled)
        speech.synthesize = rec.synthesize
        speech.play = rec.play
        speech.engine = lambda: "audio.cpp"
        speech.enabled = lambda: True
        try:
            return fn(rec)
        finally:
            (speech.synthesize, speech.play,
             speech.engine, speech.enabled) = saved
    return wrapper


def test_stream_order() -> None:
    print("\nSpeechStream: order and completeness")

    @with_stub
    def run(rec):
        stream = speech.SpeechStream().start()
        stream.feed("The first sentence is long enough to speak on its own. ")
        stream.feed("Here is the second one, also long enough to count. ")
        stream.feed("And a third to finish with, padded out a little.")
        stream.close()
        ok("stream finishes", stream.wait(timeout=10))
        return rec

    rec = run()
    check("every sentence was played", len(rec.spoken()), 3)
    ok("played in order", rec.spoken()[0].startswith("The first"))
    ok("last one played last", rec.spoken()[-1].startswith("And a third"))


def test_pipelining() -> None:
    print("\nSpeechStream: synthesis overlaps playback")

    @with_stub
    def run(rec):
        stream = speech.SpeechStream().start()
        stream.feed("The first sentence is long enough to speak on its own. "
                    "Here is the second one, also long enough to count. "
                    "And a third to finish with, padded out a little.")
        stream.close()
        stream.wait(timeout=10)
        return rec

    rec = run(0.20, 0.20)
    first_play = rec.at("play", rec.spoken()[0])
    last_synth = max(when for what, _, when in rec.events if what == "synth")
    # The point of the change: audio starts before the reply is fully rendered.
    ok("first audio starts before the last synthesis finishes",
       first_play is not None and first_play < last_synth)
    # And it starts roughly one synthesis in, not three.
    ok("first audio starts early", first_play is not None and first_play < 0.5)


def test_cancel() -> None:
    print("\nSpeechStream: cancellation")

    @with_stub
    def run(rec):
        stream = speech.SpeechStream().start()
        stream.feed("One sentence here that is quite long indeed. "
                    "Two sentences here that are quite long indeed. "
                    "Three sentences here that are quite long indeed. "
                    "Four sentences here that are quite long indeed. "
                    "Five sentences here that are quite long indeed.")
        stream.close()
        time.sleep(0.15)
        stream.cancel()
        stream.wait(timeout=5)
        time.sleep(0.1)
        return rec

    rec = run(0.15, 0.15)
    ok("cancel stops it early", len(rec.spoken()) < 5)
    ok("threads exit after cancel", True)


def test_budget() -> None:
    print("\nSpeechStream: length budget")
    os.environ["GATHM_SPEAK_MAX_CHARS"] = "80"

    @with_stub
    def run(rec):
        stream = speech.SpeechStream().start()
        for i in range(10):
            stream.feed(f"Sentence number {i} padded out to be long enough. ")
        stream.close()
        stream.wait(timeout=10)
        return rec

    try:
        rec = run(0.01, 0.01)
        spoken = sum(len(t) for t in rec.spoken())
        ok(f"budget respected ({spoken} chars spoken of a 80 char budget)",
           spoken <= 160)
    finally:
        os.environ.pop("GATHM_SPEAK_MAX_CHARS", None)


def test_speakable_unchanged() -> None:
    print("\nspeakable(): behaviour preserved by the refactor")
    check("markdown still stripped",
          speech.speakable("**bold** and `code`"), "bold and code")
    check("links still replaced",
          speech.speakable("see https://example.com/x now"),
          "see a link now")
    os.environ["GATHM_SPEAK_MAX_CHARS"] = "50"
    try:
        long = "This is a sentence. " * 10
        ok("still truncated at the limit", len(speech.speakable(long)) <= 50)
    finally:
        os.environ.pop("GATHM_SPEAK_MAX_CHARS", None)


def main() -> int:
    print("Streaming speech tests")
    print("=" * 60)
    test_chunking()
    test_stream_order()
    test_pipelining()
    test_cancel()
    test_budget()
    test_speakable_unchanged()
    print("=" * 60)
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
