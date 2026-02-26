"""
Gathm Pilot - Tricolor Terminal UI
Pure ANSI rendering — no external dependencies.
"""

import os
import shutil
import sys
import textwrap
from pathlib import Path

# ── Tricolor palette ────────────────────────────────────────────────
SAFFRON = "\033[38;5;208m"
WHITE_BOLD = "\033[1;37m"
WHITE = "\033[37m"
INDIAN_GREEN = "\033[38;5;28m"
ASHOKA_BLUE = "\033[38;5;20m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Box-drawing characters
TL = "╔"
TR = "╗"
BL = "╚"
BR = "╝"
H = "═"
V = "║"
RTL = "┌"
RTR = "┐"
RBL = "└"
RBR = "┘"
RH = "─"
RV = "│"

PILOT_VERSION = "2.0.0"

GATHM_ASCII = r"""   ___      _   _
  / _ \__ _| |_| |__  _ __ ___
 / /_\/ _` | __| '_ \| '_ ` _ \
/ /_\\ (_| | |_| | | | | | | | |
\____/\__,_|\__|_| |_|_| |_| |_|"""


def _term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def _strip_ansi(text: str) -> str:
    """Return visible length of text ignoring ANSI escape codes."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _pad_line(text: str, width: int) -> str:
    """Pad a line with spaces to reach *width* visible characters."""
    vlen = _visible_len(text)
    if vlen < width:
        return text + " " * (width - vlen)
    return text


# ── Welcome box ─────────────────────────────────────────────────────

def check_connectivity() -> str:
    """Check internet connectivity. Returns 'online' or 'offline'."""
    import socket
    try:
        socket.setdefaulttimeout(3)
        socket.create_connection(("github.com", 443))
        return "online"
    except OSError:
        return "offline"


def render_welcome(model_name: str, tool_count: int, platform: str,
                   connectivity: str = "") -> str:
    """Build the tricolor welcome box and return it as a string."""
    inner_w = min(_term_width() - 2, 62)  # inside the border
    lines: list[str] = []

    def box_line(content: str, color: str = WHITE) -> str:
        padded = _pad_line(f"  {color}{content}{RESET}", inner_w)
        return f"{SAFFRON}{V}{RESET}{padded}{INDIAN_GREEN}{V}{RESET}"

    def empty() -> str:
        return box_line("", "")

    # ── top border (saffron) ──
    lines.append(f"{SAFFRON}{TL}{H * inner_w}{INDIAN_GREEN}{TR}{RESET}")

    # ── title ──
    lines.append(empty())
    lines.append(box_line(f"\U0001F41A Gathm Pilot v{PILOT_VERSION}", WHITE_BOLD))
    lines.append(empty())

    # ── ASCII art (green) ──
    for art_line in GATHM_ASCII.splitlines():
        lines.append(box_line(art_line, INDIAN_GREEN))
    lines.append(empty())

    # ── detect connectivity if not passed ──
    if not connectivity:
        connectivity = check_connectivity()

    if connectivity == "online":
        net_color = INDIAN_GREEN
        net_label = "\u25cf Online"
    else:
        net_color = "\033[31m"  # red
        net_label = "\u25cf Offline"

    # ── info rows (tricolor rotation) ──
    info_rows = [
        (SAFFRON, "Model", model_name),
        (WHITE_BOLD, "Tools", f"{tool_count} available"),
        (INDIAN_GREEN, "Platform", platform),
        (net_color, "Network", net_label),
    ]
    for color, label, value in info_rows:
        lines.append(box_line(f"{color}{label:<10}{RESET}: {value}"))
    lines.append(empty())

    # ── tips ──
    lines.append(box_line("Tips:", DIM))
    tips = [
        '"weather Paris" \u2014 check the weather',
        '"pwned user@email.com" \u2014 breach lookup',
        '"crypto" \u2014 live cryptocurrency prices',
        "/tools to list all, /help for commands",
    ]
    for tip in tips:
        lines.append(box_line(f"  \u2022 {tip}", DIM))
    lines.append(empty())

    # ── bottom border (green) ──
    lines.append(f"{SAFFRON}{BL}{H * inner_w}{INDIAN_GREEN}{BR}{RESET}")

    return "\n".join(lines)


# ── Chat prompt ─────────────────────────────────────────────────────

def print_prompt() -> None:
    sys.stdout.write(f"\n{SAFFRON}\U0001F41A{RESET} {BOLD}\u203a{RESET} ")
    sys.stdout.flush()


# ── Response box ────────────────────────────────────────────────────

def _wrap_text(text: str, width: int) -> list[str]:
    """Word-wrap text respecting existing newlines."""
    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            result.append("")
            continue
        wrapped = textwrap.wrap(paragraph, width=width, break_long_words=False, break_on_hyphens=False)
        result.extend(wrapped if wrapped else [""])
    return result


def render_response(text: str) -> str:
    """Render a Pilot response inside a bordered box."""
    inner_w = min(_term_width() - 4, 72)
    content_w = inner_w - 4  # padding inside box
    lines: list[str] = []

    # ── header ──
    header = f" \U0001F41A Pilot "
    header_vis_len = _visible_len(header)
    remaining = inner_w - header_vis_len - 1
    lines.append(f"  {SAFFRON}{RTL}{RESET}{SAFFRON}{header}{RESET}{SAFFRON}{RH * max(remaining, 0)}{RTR}{RESET}")

    # ── empty line ──
    lines.append(f"  {SAFFRON}{RV}{RESET}{' ' * inner_w}{SAFFRON}{RV}{RESET}")

    # ── content ──
    wrapped = _wrap_text(text, content_w)
    for wline in wrapped:
        padded = _pad_line(f"  {WHITE}{wline}{RESET}", inner_w)
        lines.append(f"  {SAFFRON}{RV}{RESET}{padded}{SAFFRON}{RV}{RESET}")

    # ── empty line ──
    lines.append(f"  {SAFFRON}{RV}{RESET}{' ' * inner_w}{SAFFRON}{RV}{RESET}")

    # ── bottom border ──
    lines.append(f"  {INDIAN_GREEN}{RBL}{RH * inner_w}{RBR}{RESET}")

    return "\n".join(lines)


# ── Tool execution indicator ────────────────────────────────────────

def print_tool_exec(tool_command: str) -> None:
    print(f"  {DIM}{CYAN}\u25b6 Running:{RESET} {DIM}{tool_command}{RESET}")


# ── Status bar ──────────────────────────────────────────────────────

def print_status_bar() -> None:
    w = _term_width()
    hint = "? for help"
    pad = w - _visible_len(hint) - 1
    print(f"{DIM}{' ' * max(pad, 0)}{hint}{RESET}")


# ── Help screen ─────────────────────────────────────────────────────

def render_help() -> str:
    inner_w = min(_term_width() - 4, 62)
    lines: list[str] = []

    lines.append(f"\n  {SAFFRON}{BOLD}Gathm Pilot \u2014 Commands{RESET}")
    lines.append(f"  {DIM}{'─' * (inner_w - 2)}{RESET}")

    cmds = [
        ("/help", "Show this help screen"),
        ("/tools", "List all available Gathm tools"),
        ("/clear", "Clear screen and redraw welcome"),
        ("/model", "Show current model info"),
        ("/quit", "Exit Pilot"),
        ("?", "Show this help screen"),
    ]
    for cmd, desc in cmds:
        lines.append(f"  {INDIAN_GREEN}{cmd:<12}{RESET} {desc}")

    lines.append(f"  {DIM}{'─' * (inner_w - 2)}{RESET}")
    lines.append(f"  {DIM}Or just type naturally \u2014 Pilot will figure it out.{RESET}")
    return "\n".join(lines)


# ── Tools list ──────────────────────────────────────────────────────

def render_tools_list(tools: list[tuple[str, str]]) -> str:
    """Render (name, description) pairs in a formatted list."""
    inner_w = min(_term_width() - 4, 72)
    lines: list[str] = []

    lines.append(f"\n  {SAFFRON}{BOLD}\U0001F41A Available Tools ({len(tools)}){RESET}")
    lines.append(f"  {DIM}{'─' * (inner_w - 2)}{RESET}")

    # Tricolor rotation for rows
    colors = [SAFFRON, WHITE, INDIAN_GREEN]
    for i, (name, desc) in enumerate(tools):
        color = colors[i % 3]
        lines.append(f"  {color}{name:<16}{RESET} {DIM}{desc}{RESET}")

    lines.append(f"  {DIM}{'─' * (inner_w - 2)}{RESET}")
    return "\n".join(lines)


# ── Error display ───────────────────────────────────────────────────

def render_error(message: str) -> str:
    return (
        f"\n  {SAFFRON}{BOLD}[!] Issue Detected{RESET}\n"
        f"  {WHITE}{message}{RESET}\n"
        f"  {INDIAN_GREEN}The Engineer will take care of this shortly.{RESET}"
    )


# ── Goodbye ─────────────────────────────────────────────────────────

def render_goodbye() -> str:
    return f"\n{INDIAN_GREEN}\U0001F41A Jai Hind! Gathm Pilot signing off.{RESET}\n"
