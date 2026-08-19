"""
Gathm Pilot — TUI v3.0  (OpenClaw-inspired)

Visual design reverse-engineered from openclaw/openclaw:
  • Lobster-palette mapped to gathm tricolor
  • Shimmer waiting animation with whimsical phrases + elapsed timer
  • Rich-powered markdown / syntax-highlighted AI responses
  • Tool execution blocks with pending / success / error states
  • OSC8 clickable hyperlinks in responses
  • prompt_toolkit input with persistent history (optional dep)

Hard deps : rich >= 13
Soft deps : prompt_toolkit >= 3  (falls back to input() if absent)
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
# Gathm tricolor mapped to OpenClaw's lobster-inspired semantic palette:
#   accent      ← saffron orange  (openclaw accent/accentSoft)
#   success     ← indian green    (openclaw success / toolSuccessBg)
#   warn        ← gold            (openclaw accent)
#   error       ← red             (openclaw error)
#   muted       ← dim grey        (openclaw dim)
#   user_bg     ← dark blue-grey  (openclaw userBg)
# ══════════════════════════════════════════════════════════════════

# Rich color tokens (used in markup strings)
_C_ACCENT   = "color(208)"   # saffron orange
_C_SUCCESS  = "color(40)"    # indian green
_C_WARN     = "color(214)"   # gold
_C_ERROR    = "color(196)"   # red
_C_CYAN     = "color(80)"    # info cyan
_C_MUTED    = "color(244)"   # gray
_C_USER_BG  = "on color(236)"  # user message background

# Raw ANSI sequences (used in the waiting animation to avoid rich overhead)
_A_ACCENT  = "\033[38;5;208m"
_A_GREEN   = "\033[38;5;40m"
_A_BOLD    = "\033[1m"
_A_DIM     = "\033[2m"
_A_RESET   = "\033[0m"

PILOT_VERSION = "2.0.0"

GATHM_ASCII = r"""   ___      _   _
  / _ \__ _| |_| |__  _ __ ___
 / /_\/ _` | __| '_ \| '_ ` _ \
/ /_\\ (_| | |_| | | | | | | | |
\____/\__,_|\__|_| |_|_| |_| |_|"""

# ── Whimsical waiting phrases (openclaw-inspired, gathm-flavored) ─
_WAITING_PHRASES = [
    "flibbertigibbeting",
    "kerfuffling",
    "summoning the oracle",
    "weaving the threads",
    "consulting the archive",
    "scanning the horizon",
    "chasing the signal",
    "decoding the noise",
    "tracing the path",
    "connecting the dots",
]

_SPINNER = "◜◠◝◞◡◟"

# ── Console ──────────────────────────────────────────────────────
console = Console(highlight=False, markup=True)


# ══════════════════════════════════════════════════════════════════
# Waiting animation  (shimmer + elapsed, runs in background thread)
# Mirrors openclaw/src/tui/tui-waiting.ts  buildWaitingStatusMessage()
# ══════════════════════════════════════════════════════════════════

_wait_stop   = threading.Event()
_wait_thread: Optional[threading.Thread] = None


def _shimmer(text: str, tick: int) -> str:
    """Moving bright-band shimmer over plain text (openclaw shimmerText)."""
    width = 5
    pos = tick % (len(text) + width * 2) - width
    out = ""
    for i, ch in enumerate(text):
        if pos <= i < pos + width:
            out += _A_BOLD + ch + _A_RESET
        else:
            out += _A_DIM + ch + _A_RESET
    return out


def _waiting_loop() -> None:
    start = time.monotonic()
    tick  = 0
    while not _wait_stop.wait(timeout=0.1):
        elapsed = int(time.monotonic() - start)
        phrase  = _WAITING_PHRASES[(tick // 15) % len(_WAITING_PHRASES)]
        frame   = _SPINNER[tick % len(_SPINNER)]
        shimmer = _shimmer(phrase, tick)
        line = (
            f"  {_A_ACCENT}{frame}{_A_RESET}"
            f" {shimmer}"
            f"  {_A_DIM}{elapsed}s{_A_RESET}"
        )
        sys.stdout.write(f"\r{line}   ")
        sys.stdout.flush()
        tick += 1
    # Clear the animation line
    sys.stdout.write("\r" + " " * 64 + "\r")
    sys.stdout.flush()


def start_waiting() -> None:
    """Start the waiting shimmer in a background thread."""
    global _wait_thread
    _wait_stop.clear()
    _wait_thread = threading.Thread(target=_waiting_loop, daemon=True)
    _wait_thread.start()


def stop_waiting() -> None:
    """Stop the waiting shimmer and join the thread."""
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

def render_welcome(model_name: str, tool_count: int, platform: str,
                   connectivity: str = "") -> None:
    """Clear screen and render the welcome panel (openclaw-inspired header)."""
    if not connectivity:
        connectivity = check_connectivity()

    net_style = _C_SUCCESS if connectivity == "online" else _C_ERROR
    net_label = "● Online"  if connectivity == "online" else "● Offline"

    content = Text()
    content.append("\n")
    content.append("🐚  Gathm Pilot ", style="bold white")
    content.append(f"v{PILOT_VERSION}\n\n", style=f"bold {_C_ACCENT}")

    for art_line in GATHM_ASCII.splitlines():
        content.append(art_line + "\n", style=_C_SUCCESS)
    content.append("\n")

    for style, label, value in [
        (_C_ACCENT,     "Model",    model_name),
        ("bold white",  "Tools",    f"{tool_count} available"),
        (_C_SUCCESS,    "Platform", platform),
        (net_style,     "Network",  net_label),
    ]:
        content.append(f"  {label:<10}", style=style)
        content.append(": ")
        content.append(f"{value}\n", style="white")

    content.append("\n")
    content.append("  Tips:\n", style="dim")
    for tip in [
        '"weather Paris" — check the weather',
        '"pwned user@email.com" — breach lookup',
        '"crypto" — live cryptocurrency prices',
        "/tools to list all,  /help for commands",
    ]:
        content.append(f"    • {tip}\n", style="dim")
    content.append("\n")

    w = shutil.get_terminal_size((80, 24)).columns
    os.system("clear" if os.name != "nt" else "cls")
    console.print(
        Panel(
            content,
            border_style=_C_ACCENT,
            box=rich_box.ROUNDED,
            expand=False,
            width=min(w - 2, 70),
        )
    )


# ══════════════════════════════════════════════════════════════════
# Status bar
# ══════════════════════════════════════════════════════════════════

def print_status_bar() -> None:
    """Print a right-aligned dim hint line (openclaw footer concept)."""
    hint = "? for help  •  /model  •  /tools"
    w    = shutil.get_terminal_size((80, 24)).columns
    console.print(f"[{_C_MUTED}]{hint:>{w - 1}}[/{_C_MUTED}]")


# ══════════════════════════════════════════════════════════════════
# User message  (openclaw UserMessageComponent)
# ══════════════════════════════════════════════════════════════════

def print_user_message(text: str) -> None:
    """Render the user's query as a styled sent-message (dark bg, right-ish)."""
    msg = Text()
    msg.append("  🐚  ", style=f"bold {_C_ACCENT}")
    msg.append(f" {text} ", style=f"white {_C_USER_BG}")
    console.print()
    console.print(Padding(msg, (0, 2)))


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


def get_user_input() -> str:
    """Read user input (prompt_toolkit with history, or plain input fallback)."""
    session = _get_pt_session()
    if session is not None:
        # prompt_toolkit does not interpret raw ANSI escape sequences in prompt
        # strings — it displays them literally as ^[[...m.  Wrapping with ANSI()
        # tells it to parse and render the escape codes as intended colors/styles.
        prompt_str = f"\n{_A_ACCENT}🐚{_A_RESET} {_A_BOLD}›{_A_RESET} "
        text = session.prompt(_PT_ANSI(prompt_str)).strip()  # type: ignore[union-attr]
        # Erase the raw input line so only the styled bubble below remains.
        sys.stdout.write("\x1b[1A\x1b[2K\r")
        sys.stdout.flush()
        return text
    print_prompt()
    return input().strip()


def print_prompt() -> None:
    """Print the bare input prompt (fallback path without prompt_toolkit)."""
    sys.stdout.write(f"\n{_A_ACCENT}🐚{_A_RESET} {_A_BOLD}›{_A_RESET} ")
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════
# Tool execution  (openclaw ToolExecutionComponent)
# Pending ▶ blue-dim,  Success ✓ green,  Error ✗ red
# ══════════════════════════════════════════════════════════════════

def print_tool_exec(tool_command: str) -> None:
    """
    Display tool execution indicator.
    Automatically pauses the waiting animation while printing, then restarts it
    — mirrors openclaw's inline tool state blocks inside the chat log.
    """
    was_waiting = _wait_thread is not None and _wait_thread.is_alive()
    if was_waiting:
        stop_waiting()

    console.print(
        f"  [{_C_CYAN} dim]▶ running:[/{_C_CYAN} dim]  [dim]{tool_command}[/dim]"
    )

    if was_waiting:
        start_waiting()


# ══════════════════════════════════════════════════════════════════
# AI response   (openclaw AssistantMessageComponent + markdownTheme)
# ══════════════════════════════════════════════════════════════════

def render_response(text: str) -> None:
    """
    Render the assistant reply with full markdown support inside a panel.

    Mirrors openclaw's AssistantMessageComponent + HyperlinkMarkdown:
      • monokai code blocks (openclaw uses VS Dark-like theme)
      • headings in accent color
      • code in warn gold
      • OSC8 hyperlinks where the terminal supports them
    """
    w  = shutil.get_terminal_size((80, 24)).columns
    md = Markdown(text, code_theme="monokai", justify="left")
    console.print()
    console.print(
        Padding(
            Panel(
                md,
                title=f"[{_C_ACCENT}]🐚  Pilot[/{_C_ACCENT}]",
                title_align="left",
                border_style=_C_ACCENT,
                box=rich_box.ROUNDED,
                padding=(0, 1),
                width=min(w - 4, 78),
            ),
            (0, 2),
        )
    )

    # Say it out loud too. This is the single choke point for assistant replies
    # in the TUI, so hooking it here covers both the normal answer and the
    # safety refusal without duplicating the call at each site.
    speak_reply(text)


# ══════════════════════════════════════════════════════════════════
# Help screen
# ══════════════════════════════════════════════════════════════════

def render_help() -> None:
    """Render the slash-command help panel (openclaw /help output)."""
    w = shutil.get_terminal_size((80, 24)).columns

    content = Text("\n")
    for cmd, desc in [
        ("/help",   "Show this help screen"),
        ("/tools",  "List all available Gathm tools"),
        ("/clear",  "Clear screen and redraw welcome"),
        ("/model",  "Show current model / backend info"),
        ("/speak",  "Voice status; /speak on|off, or /speak <text> to test"),
        ("/quit",   "Exit Pilot"),
        ("?",       "Show this help screen"),
    ]:
        content.append(f"  {cmd:<14}", style=_C_SUCCESS)
        content.append(f"{desc}\n", style="white")

    content.append("\n  ", style="")
    content.append(
        "You can also type naturally — Pilot will route to the right tool.\n",
        style="dim",
    )
    content.append(
        "  Prefix with ! to run a raw bash command (e.g. !ls -la).\n",
        style="dim",
    )
    content.append("\n")

    console.print()
    console.print(
        Padding(
            Panel(
                content,
                title=f"[{_C_ACCENT}]Gathm Pilot — Commands[/{_C_ACCENT}]",
                title_align="left",
                border_style=f"{_C_ACCENT} dim",
                box=rich_box.ROUNDED,
                expand=False,
                width=min(w - 4, 66),
            ),
            (0, 2),
        )
    )


# ══════════════════════════════════════════════════════════════════
# Tools list
# ══════════════════════════════════════════════════════════════════

def render_tools_list(tools: List[Tuple[str, str]]) -> None:
    """Render tool catalog with tricolor row rotation."""
    w      = shutil.get_terminal_size((80, 24)).columns
    colors = [_C_ACCENT, "white", _C_SUCCESS]

    content = Text("\n")
    for i, (name, desc) in enumerate(tools):
        content.append(f"  {name:<18}", style=colors[i % 3])
        content.append(f"{desc}\n",      style="dim")
    content.append("\n")

    console.print()
    console.print(
        Padding(
            Panel(
                content,
                title=f"[{_C_ACCENT}]🐚  Available Tools ({len(tools)})[/{_C_ACCENT}]",
                title_align="left",
                border_style=f"{_C_ACCENT} dim",
                box=rich_box.ROUNDED,
                expand=False,
                width=min(w - 4, 82),
            ),
            (0, 2),
        )
    )


# ══════════════════════════════════════════════════════════════════
# Error display  (openclaw error state)
# ══════════════════════════════════════════════════════════════════

def render_error(message: str) -> None:
    """Render an error panel (red border, engineer hand-off note)."""
    content = Text()
    content.append("\n  ")
    content.append(message + "\n\n  ", style="white")
    content.append(
        "The Engineer will take care of this shortly.\n",
        style=_C_SUCCESS,
    )

    console.print()
    console.print(
        Padding(
            Panel(
                content,
                title=f"[{_C_ACCENT} bold][!] Issue Detected[/{_C_ACCENT} bold]",
                title_align="left",
                border_style=_C_ERROR,
                box=rich_box.ROUNDED,
                expand=False,
            ),
            (0, 2),
        )
    )


# ══════════════════════════════════════════════════════════════════
# Goodbye
# ══════════════════════════════════════════════════════════════════

def render_goodbye() -> None:
    console.print(
        f"\n[{_C_SUCCESS}]🐚  Jai Hind! Gathm Pilot signing off.[/{_C_SUCCESS}]\n"
    )
