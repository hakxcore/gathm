#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


GATHM_ROOT = Path(__file__).resolve().parent.parent

AUDIO_CPP_BIN = Path(
    os.getenv(
        "GATHM_AUDIO_CPP_BIN",
        str(Path.home() / "audio.cpp" / "build" / "bin" / "audiocpp_cli"),
    )
)

POCKET_TTS_MODEL = Path(
    os.getenv(
        "GATHM_POCKET_TTS_MODEL",
        str(
            Path.home()
            / "audio.cpp"
            / "models"
            / "PocketTTS-GGUF"
            / "english"
        ),
    )
)


def tts_enabled() -> bool:
    return os.getenv("GATHM_TTS_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def speak(text: str) -> bool:
    """Generate speech with PocketTTS and play it on Android/Termux."""

    if not tts_enabled():
        return False

    if not AUDIO_CPP_BIN.is_file():
        print(f"[TTS] audio.cpp binary not found: {AUDIO_CPP_BIN}")
        return False

    if not POCKET_TTS_MODEL.is_dir():
        print(f"[TTS] PocketTTS model not found: {POCKET_TTS_MODEL}")
        return False

    voice = os.getenv("GATHM_TTS_VOICE", "alba")
    threads = os.getenv("GATHM_TTS_THREADS", "4")

    with tempfile.NamedTemporaryFile(
        suffix=".wav",
        prefix="gathm_tts_",
        delete=False,
    ) as tmp:
        wav_path = Path(tmp.name)

    try:
        cmd = [
            str(AUDIO_CPP_BIN),
            "--task",
            "tts",
            "--family",
            "pocket_tts",
            "--model",
            str(POCKET_TTS_MODEL),
            "--backend",
            "cpu",
            "--voice-id",
            voice,
            "--threads",
            threads,
            "--text",
            text,
            "--out",
            str(wav_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            print(f"[TTS] generation failed:\n{result.stderr or result.stdout}")
            return False

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            print("[TTS] no audio was generated")
            return False

        # Termux:API playback.
        player = Path(
            os.getenv(
                "GATHM_TTS_PLAYER",
                str(Path.home() / ".termux" / "tts-player"),
            )
        )

        # Prefer termux-media-player if installed.
        media_player = subprocess.run(
            ["sh", "-c", "command -v termux-media-player"],
            capture_output=True,
            text=True,
        )

        if media_player.returncode == 0:
            play = subprocess.run(
                ["termux-media-player", "play", str(wav_path)],
                capture_output=True,
                text=True,
            )
            if play.returncode == 0:
                return True

        # Fallback: open the WAV using Android.
        open_result = subprocess.run(
            ["termux-open", str(wav_path)],
            capture_output=True,
            text=True,
        )

        if open_result.returncode != 0:
            print(
                "[TTS] generated audio successfully, "
                "but couldn't start playback."
            )
            print(f"[TTS] WAV: {wav_path}")
            return False

        return True

    except subprocess.TimeoutExpired:
        print("[TTS] generation timed out")
        return False

    except Exception as exc:
        print(f"[TTS] error: {exc}")
        return False

    finally:
        # Give Android/termux-media-player time to open the file before cleanup.
        # If playback is asynchronous, the player may still need the file.
        # Keep it when explicitly requested for debugging.
        if os.getenv("GATHM_TTS_KEEP_WAV", "false").lower() not in (
            "1",
            "true",
            "yes",
        ):
            # Don't remove immediately when using Android fallback.
            # The media player may need the file after this function returns.
            if os.getenv("GATHM_TTS_PLAYER_MODE", "keep").lower() == "delete":
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
