#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local voice pipeline for Gathm.

STT: faster-whisper  (local, offline)
TTS: edge-tts        (Microsoft neural voices via network) or
     piper-tts        (fully offline, if installed)

Protocol (used by api/server.py via subprocess):
  transcribe  stdin: raw audio bytes (webm/wav/ogg)
              stdout: {"text": "..."} or {"error": "..."}

  speak       stdin: {"text": "..."}
              stdout: raw mp3 bytes (Content-Type: audio/mpeg)
              OR on error: exits with code != 0 and writes {"error":"..."} to stdout
"""

import io
import json
import os
import sys
import tempfile


# ---------------------------------------------------------------------------
# STT — faster-whisper
# ---------------------------------------------------------------------------

def transcribe_audio(audio_bytes: bytes, language: str = None) -> dict:
    """Transcribe audio bytes using faster-whisper. Returns {"text":"..."}."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"error": "faster-whisper not installed. Run: pip install faster-whisper"}

    model_size = os.environ.get("WHISPER_MODEL", "base")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute = os.environ.get("WHISPER_COMPUTE", "int8")

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute)
    except Exception as exc:
        return {"error": f"Failed to load Whisper model '{model_size}': {exc}"}

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name

    try:
        kwargs = {"beam_size": 5}
        if language:
            kwargs["language"] = language
        segments, info = model.transcribe(tmp, **kwargs)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {"text": text, "language": info.language}
    except Exception as exc:
        return {"error": f"Transcription failed: {exc}"}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TTS — edge-tts (primary) with piper fallback
# ---------------------------------------------------------------------------

def synthesize_speech(text: str, voice: str = None) -> bytes:
    """Synthesize speech from text. Returns raw mp3 bytes or raises RuntimeError."""
    voice = voice or os.environ.get("TTS_VOICE", "en-US-AriaNeural")

    # Try edge-tts first
    try:
        import edge_tts
        import asyncio

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        audio = asyncio.run(_run())
        if audio:
            return audio
        raise RuntimeError("edge-tts produced no audio")

    except ImportError:
        pass  # fall through to piper
    except Exception as exc:
        raise RuntimeError(f"edge-tts failed: {exc}") from exc

    # Fallback: piper-tts (offline)
    try:
        import subprocess
        import shutil
        piper_bin = shutil.which("piper") or shutil.which("piper-tts")
        if not piper_bin:
            raise RuntimeError("neither edge-tts nor piper is installed")
        model = os.environ.get("PIPER_MODEL", "en_US-lessac-medium")
        result = subprocess.run(
            [piper_bin, "--model", model, "--output-raw"],
            input=text.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"piper exited {result.returncode}: {result.stderr.decode()}")
        return result.stdout  # raw PCM — caller must handle
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"piper failed: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI entry points (called by api/server.py via subprocess)
# ---------------------------------------------------------------------------

def _cmd_transcribe():
    audio = sys.stdin.buffer.read()
    if not audio:
        print(json.dumps({"error": "no audio data received"}))
        sys.exit(1)
    result = transcribe_audio(audio)
    print(json.dumps(result))
    sys.exit(0 if "text" in result else 1)


def _cmd_speak():
    raw = sys.stdin.read().strip()
    try:
        payload = json.loads(raw)
        text = payload.get("text", "")
    except (json.JSONDecodeError, AttributeError):
        text = raw
    if not text:
        sys.stdout.buffer.write(json.dumps({"error": "no text provided"}).encode())
        sys.exit(1)
    try:
        audio = synthesize_speech(text)
        sys.stdout.buffer.write(audio)
        sys.exit(0)
    except RuntimeError as exc:
        sys.stdout.buffer.write(json.dumps({"error": str(exc)}).encode())
        sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "transcribe":
        _cmd_transcribe()
    elif cmd == "speak":
        _cmd_speak()
    else:
        sys.stderr.write("Usage: voice.py transcribe | speak\n")
        sys.exit(2)
