"""pilot/browser.py — Cross-platform browser control for Gathm Pilot.

Commands exposed to Pilot (via run_browser_action):
  open <url>        — opens URL in the system's default browser
  fetch <url>       — returns the readable text content of a web page
  screenshot <url>  — saves a screenshot (desktop only; requires playwright)

Platform support:
  macOS     → open
  Windows   → cmd /c start
  WSL       → cmd.exe /c start (falls back to xdg-open)
  Termux    → termux-open-url (open) / requests+bs4 (fetch) — no playwright
  Linux     → xdg-open / sensible-browser
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Platform detection (kept local so this module is self-contained)
# ---------------------------------------------------------------------------

def _platform() -> str:
    import platform as _plat
    sys_name = _plat.system().lower()
    if sys_name == "darwin":
        return "macos"
    if sys_name == "windows":
        return "windows"
    if shutil.which("termux-setup-storage"):
        return "termux"
    try:
        if "microsoft" in Path("/proc/version").read_text().lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


# ---------------------------------------------------------------------------
# open_url
# ---------------------------------------------------------------------------

def open_url(url: str) -> str:
    """Open *url* in the system's default browser. Returns a status string."""
    plat = _platform()
    try:
        if plat == "macos":
            subprocess.run(["open", url], check=True, timeout=5)
        elif plat == "windows":
            subprocess.run(["cmd", "/c", "start", "", url], check=True, timeout=5)
        elif plat == "termux":
            subprocess.run(["termux-open-url", url], check=True, timeout=5)
        elif plat == "wsl":
            try:
                subprocess.run(["cmd.exe", "/c", "start", "", url],
                               check=True, timeout=5)
            except Exception:
                subprocess.run(["xdg-open", url], check=False, timeout=5)
        else:
            # Linux: walk the opener list
            for opener in ("xdg-open", "sensible-browser", "x-www-browser"):
                if shutil.which(opener):
                    subprocess.run([opener, url], check=False, timeout=5)
                    break
        return f"Opened in browser: {url}"
    except FileNotFoundError as exc:
        return f"Browser opener not found ({exc}). Visit manually: {url}"
    except Exception as exc:
        return f"Could not open browser ({exc}). Visit manually: {url}"


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = 15) -> str:
    """Fetch *url* and return its readable text (up to ~200 lines).

    Uses requests + beautifulsoup4 when available; falls back to curl.
    """
    try:
        import requests  # type: ignore[import]
        from bs4 import BeautifulSoup  # type: ignore[import]

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; Gathm/3.0; "
                "+https://github.com/hakxcore/gathm)"
            )
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:200])

    except ImportError:
        # Fall back to curl
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", str(timeout),
                 "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            raw = result.stdout.strip()
            return raw[:6000] if raw else f"curl returned no output for {url}"
        except Exception as exc:
            return (
                f"Could not fetch {url} ({exc}). "
                "Install 'requests' and 'beautifulsoup4' for full web support."
            )

    except Exception as exc:
        return f"Error fetching {url}: {exc}"


# ---------------------------------------------------------------------------
# screenshot_page  (desktop only — requires playwright)
# ---------------------------------------------------------------------------

def screenshot_page(url: str) -> str:
    """Capture a full-page screenshot. Requires playwright + Chromium."""
    if _platform() == "termux":
        return (
            "Screenshots are not supported on Termux. "
            "Use 'browser fetch <url>' to read page content instead."
        )

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError:
        return (
            "playwright is not installed. "
            "Run: pip install playwright && playwright install chromium"
        )

    import tempfile
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="gathm_screenshot_"
            ) as fh:
                path = fh.name
            page.screenshot(path=path, full_page=False)
            browser.close()
        return f"Screenshot saved → {path}"
    except Exception as exc:
        return f"Screenshot failed: {exc}"


# ---------------------------------------------------------------------------
# Public entry-point used by pilot/main.py
# ---------------------------------------------------------------------------

def run_browser_action(command: str) -> str:
    """Dispatch a browser command from Pilot.

    Syntax:
        open <url>
        fetch <url>
        screenshot <url>
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return "Usage: browser open|fetch|screenshot <url>"

    action = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if not target:
        return f"Please provide a URL after '{action}'."

    # Ensure URL has a scheme
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    if action == "open":
        return open_url(target)
    elif action == "fetch":
        content = fetch_page(target)
        return f"[Content from {target}]\n\n{content}"
    elif action in ("screenshot", "capture"):
        return screenshot_page(target)
    else:
        return (
            f"Unknown browser action: '{action}'. "
            "Valid actions: open, fetch, screenshot"
        )
