"""Gathm's terminal conversation, with optional voice and tool activity.

Rich renders replies; prompt_toolkit adds input history when available.
The terminal remains usable without speech or prompt_toolkit installed.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

# ── rich ─────────────────────────────────────────────────────────
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich import box as rich_box
from rich.padding import Padding

# ── prompt_toolkit (optional) ────────────────────────────────────
_PT = False
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
    _PT = True
except ImportError:
    pass

# ── speech (optional) ────────────────────────────────────────────
# audio.cpp is installed by ./install on Termux; lib/speech.py drives it.
# Absent or unbuilt, every call below is a no-op, so the TUI is unaffected.
_GATHM_ROOT = Path(__file__).resolve().parent.parent
if str(_GATHM_ROOT) not in sys.path:
    sys.path.insert(0, str(_GATHM_ROOT))
try:
    from lib import speech as _speech
except Exception:  # noqa: BLE001 - never let speech break the UI
    _speech = None


def speak_reply(text: str) -> None:
    """Read a reply aloud in the background. Silent when speech is off."""
    if _speech is None:
        return
    try:
        _speech.speak_async(text)
    except Exception:  # noqa: BLE001
        pass


def start_reply_stream():
    """A speech stream to feed the reply into as it is generated, or None.

    Returns None whenever speech is unavailable or switched off, which is the
    signal to the caller that it should speak the finished reply the old way.
    """
    if _speech is None:
        return None
    try:
        if not _speech.enabled():
            return None
        _speech.stop()                      # cut off the previous answer
        return _speech.SpeechStream().start()
    except Exception:  # noqa: BLE001 - speech never breaks a reply
        return None


def stop_speaking() -> None:
    """Cut off whatever is being said — called when the user speaks up next."""
    if _speech is None:
        return
    try:
        _speech.stop()
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════
# Palette
# Warm saffron for Gathm, neutral text, and color only where it adds meaning.
# ══════════════════════════════════════════════════════════════════

# Rich color tokens (used in markup strings)
_C_ACCENT   = "color(208)"   # saffron orange
_C_LOGO     = "color(40)"    # original Gathm green
_C_SUCCESS  = "color(114)"   # soft green
_C_ERROR    = "color(203)"   # red
_C_MUTED    = "color(246)"   # readable gray

# Raw ANSI sequences (used in the waiting animation to avoid rich overhead)
_A_ACCENT  = "\033[38;5;208m"
_A_BOLD    = "\033[1m"
_A_DIM     = "\033[2m"
_A_RESET   = "\033[0m"

_SPINNER = "◜◝◞◟"

GATHM_ASCII = r"""   ___      _   _
  / _ \__ _| |_| |__  _ __ ___
 / /_\/ _` | __| '_ \| '_ ` _ \
/ /_\\ (_| | |_| | | | | | | | |
\____/\__,_|\__|_| |_|_| |_| |_|"""

# ── Console ──────────────────────────────────────────────────────
console = Console(highlight=False, markup=True)


# ══════════════════════════════════════════════════════════════════
# Waiting indicator (elapsed time, runs in background thread)
# ══════════════════════════════════════════════════════════════════

_wait_stop   = threading.Event()
_wait_thread: Optional[threading.Thread] = None


def _waiting_loop() -> None:
    start = time.monotonic()
    tick  = 0
    while not _wait_stop.wait(timeout=0.2):
        elapsed = int(time.monotonic() - start)
        frame   = _SPINNER[tick % len(_SPINNER)]
        line = (
            f" {_A_ACCENT}{frame}{_A_RESET} Working"
            f" {_A_DIM}· {elapsed}s{_A_RESET}"
        )
        sys.stdout.write(f"\r\033[2K{line}")
        sys.stdout.flush()
        tick += 1
    # Clear the animation line
    sys.stdout.write("\r\033[2K")
    sys.stdout.flush()


def start_waiting() -> None:
    """Start the waiting indicator in a background thread."""
    global _wait_thread
    _wait_stop.clear()
    _wait_thread = threading.Thread(target=_waiting_loop, daemon=True)
    _wait_thread.start()


def stop_waiting() -> None:
    """Stop the waiting indicator and join the thread."""
    global _wait_thread
    _wait_stop.set()
    if _wait_thread is not None:
        _wait_thread.join(timeout=0.5)
        _wait_thread = None


# ══════════════════════════════════════════════════════════════════
# Connectivity
# ══════════════════════════════════════════════════════════════════

def check_connectivity() -> str:
    """Return 'online' or 'offline'."""
    try:
        socket.setdefaulttimeout(3)
        socket.create_connection(("github.com", 443))
        return "online"
    except OSError:
        return "offline"


# ══════════════════════════════════════════════════════════════════
# Welcome screen
# ══════════════════════════════════════════════════════════════════

def _terminal_width() -> int:
    """Respect both the terminal and an explicitly sized Rich console."""
    return min(console.width, shutil.get_terminal_size((80, 24)).columns)


def _print_panel(content, *, title: str = "", width: int = 76,
                 border_style: str = _C_MUTED) -> None:
    """Use one column of breathing room, including on narrow Termux screens."""
    console.print(
        Padding(
            Panel(
                content,
                title=title or None,
                title_align="left",
                border_style=border_style,
                box=rich_box.ROUNDED,
                padding=(1, 2),
                width=max(1, min(_terminal_width() - 2, width)),
            ),
            (0, 1),
        )
    )


def render_welcome(model_name: str, tool_count: int, platform: str,
                   connectivity: str = "") -> None:
    """Introduce the conversation before model and tool details."""
    if not connectivity:
        connectivity = check_connectivity()

    content = Text()
    # Account for the panel border, padding, and outer margin so the original
    # wordmark stays intact on phone terminals instead of wrapping its lines.
    content_width = min(_terminal_width() - 2, 76) - 6
    if content_width >= max(map(len, GATHM_ASCII.splitlines())):
        content.append(GATHM_ASCII + "\n\n", style=_C_LOGO)
    else:
        content.append("Gathm\n", style=f"bold {_C_LOGO}")
    content.append("Your personal AI assistant\n\n", style="bold default")
    content.append(
        "Plan, write, learn, or talk things through.\n\n", style="default"
    )
    content.append("Start anywhere\n", style="bold default")
    for example in [
        "Help me plan my day",
        "Help me write a kind reply",
        "Explain something new to me",
    ]:
        content.append(f"  {example}\n", style="default")

    content.append("\nTry /listen for voice, or type a message.\n", style="default")
    content.append("Voice settings: /speak\n\n", style=_C_MUTED)
    content.append("Tools when useful\n", style="bold default")
    content.append(
        f"{tool_count} available for current information and actions. /tools to explore.\n\n",
        style=_C_MUTED,
    )
    content.append(f"{model_name}\n{platform} · ", style=_C_MUTED)
    content.append(
        "Online" if connectivity == "online" else "Offline",
        style=_C_SUCCESS if connectivity == "online" else _C_MUTED,
    )
    os.system("clear" if os.name != "nt" else "cls")
    _print_panel(content, border_style=_C_ACCENT)


# ══════════════════════════════════════════════════════════════════
# Status bar
# ══════════════════════════════════════════════════════════════════

def print_status_bar() -> None:
    """Keep everyday conversation and voice controls easy to discover."""
    console.print(Text(" /speak · /listen · /help", style=_C_MUTED))


# ══════════════════════════════════════════════════════════════════
# User message
# ══════════════════════════════════════════════════════════════════

def print_user_message(text: str) -> None:
    """Keep the user's words readable and separate from Gathm's reply."""
    msg = Text()
    msg.append("You\n", style=f"bold {_C_MUTED}")
    msg.append(text, style="default")
    console.print()
    console.print(Padding(msg, (0, 1)))


# ══════════════════════════════════════════════════════════════════
# Input prompt  (with optional prompt_toolkit history)
# ══════════════════════════════════════════════════════════════════

_pt_session: Optional[object] = None


def _get_pt_session() -> Optional[object]:
    global _pt_session
    if _pt_session is None and _PT:
        history_path = Path.home() / ".gathm" / "pilot_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        _pt_session = PromptSession(  # type: ignore[assignment]
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
        )
    return _pt_session


def _event_loop_running() -> bool:
    """Whether an asyncio loop is running on this thread.

    Playwright's SYNC api runs one, and Pilot keeps the browser session open
    between commands on purpose — so after `browser screenshot` a loop is live
    for the rest of the session. prompt_toolkit's synchronous prompt() calls
    asyncio.run() internally, which refuses to start inside a running loop:
    "asyncio.run() cannot be called from a running event loop". Taking a
    screenshot therefore broke the input prompt for good.
    """
    try:
        import asyncio
        asyncio.get_running_loop()
        return True
    except Exception:  # noqa: BLE001 - RuntimeError when there is none
        return False


def get_user_input() -> str:
    """Read user input (prompt_toolkit with history, or plain input fallback)."""
    session = _get_pt_session()
    # Plain input() needs no event loop of its own, so it still works when
    # something else owns one. History is lost for these turns; a working
    # prompt matters more.
    if session is not None and _event_loop_running():
        session = None
    if session is not None:
        # prompt_toolkit does not interpret raw ANSI escape sequences in prompt
        # strings — it displays them literally as ^[[...m.  Wrapping with ANSI()
        # tells it to parse and render the escape codes as intended colors/styles.
        prompt_str = f"\n{_A_ACCENT}You{_A_RESET} {_A_BOLD}›{_A_RESET} "
        try:
            text = session.prompt(_PT_ANSI(prompt_str)).strip()  # type: ignore[union-attr]
        except RuntimeError:
            # Belt and braces: a loop that appeared between the check above and
            # here must not cost the user their prompt.
            print_prompt()
            return input().strip()
        # Erase the raw input line so only the styled bubble below remains.
        sys.stdout.write("\x1b[1A\x1b[2K\r")
        sys.stdout.flush()
        return text
    print_prompt()
    return input().strip()


def print_prompt() -> None:
    """Print the bare input prompt (fallback path without prompt_toolkit)."""
    sys.stdout.write(f"\n{_A_ACCENT}You{_A_RESET} {_A_BOLD}›{_A_RESET} ")
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════
# Tool activity
# ══════════════════════════════════════════════════════════════════

def print_tool_exec(tool_command: str) -> None:
    """Show tool activity without interrupting the conversation layout."""
    was_waiting = _wait_thread is not None and _wait_thread.is_alive()
    if was_waiting:
        stop_waiting()

    activity = Text(" Using tool · ", style=_C_MUTED)
    activity.append(tool_command)
    console.print(activity)

    if was_waiting:
        start_waiting()


# ══════════════════════════════════════════════════════════════════
# Assistant reply
# ══════════════════════════════════════════════════════════════════

def render_response(text: str, speak: bool = True) -> None:
    """Render markdown and optionally speak the reply."""
    md = Markdown(text, code_theme="monokai", justify="left")
    console.print()
    _print_panel(
        md,
        title=f"[{_C_ACCENT}]Gathm[/{_C_ACCENT}]",
        border_style=_C_ACCENT,
    )

    # Say it out loud too. This is the single choke point for assistant replies
    # in the TUI, so hooking it here covers both the normal answer and the
    # safety refusal without duplicating the call at each site.
    #
    # speak=False means the reply was already spoken while it was being
    # generated (see start_reply_stream); saying it again would be a rerun.
    if speak:
        speak_reply(text)


# ══════════════════════════════════════════════════════════════════
# Help screen
# ══════════════════════════════════════════════════════════════════

def render_help() -> None:
    """Present conversation controls before optional tool controls."""
    content = Text("Ask naturally. Gathm can help you think, write, and learn, "
                   "and use tools when useful.\n\n")
    for cmd, desc in [
        ("/speak", "Voice status; /speak on or off for spoken replies"),
        ("/listen", "Record a voice message if supported; /listen 5 records 5 seconds"),
        ("/clear", "Redraw the welcome screen; keeps this conversation"),
        ("/model", "Show the model in use"),
        ("/tools", "Explore tools for information and actions"),
        ("/help or ?", "Show this guide"),
        ("/quit", "Leave Gathm"),
    ]:
        content.append(f"{cmd}\n", style=f"bold {_C_ACCENT}")
        content.append(f"{desc}\n\n", style="default")

    content.append("Advanced\n", style="bold default")
    content.append("Use ! before a shell command, for example !ls -la. "
                   "Shell access must be enabled.", style=_C_MUTED)

    console.print()
    _print_panel(content, title="Gathm · Help")


# ══════════════════════════════════════════════════════════════════
# Tools list
# ══════════════════════════════════════════════════════════════════

def render_tools_list(tools: List[Tuple[str, str]]) -> None:
    """Keep tools available as optional capabilities, with readable wrapping."""
    content = Text("Ask for what you need in your own words. "
                   "Gathm can use these tools to help.\n")
    if not tools:
        content.append("\nNo tools are available in this installation.", style=_C_MUTED)
    for name, desc in tools:
        content.append(f"\n{name}\n", style=f"bold {_C_ACCENT}")
        content.append(desc + "\n", style="default")

    console.print()
    _print_panel(content, title=f"Tools · {len(tools)} available")


# ══════════════════════════════════════════════════════════════════
# Error display
# ══════════════════════════════════════════════════════════════════

def render_error(message: str) -> None:
    """Show the actual error without promising a background repair."""
    content = Text(message)

    console.print()
    _print_panel(content, title="Something went wrong", border_style=_C_ERROR)


# ══════════════════════════════════════════════════════════════════
# Goodbye
# ══════════════════════════════════════════════════════════════════

def render_goodbye() -> None:
    console.print(Text("\n Take care. — Gathm\n", style=_C_ACCENT))
