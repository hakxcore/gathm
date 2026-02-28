"""pilot/browser.py — Cross-platform browser control for Gathm Pilot.

Commands (dispatched by run_browser_action):
  open <url>              — open URL in the system's default browser (all platforms)
  fetch <url>             — HTTP-fetch page text via requests+bs4 / curl fallback
  navigate <url>          — load URL in the controlled headless session
  click <selector|text>   — click an element (CSS selector or visible text)
  type <selector> <text>  — type text into a field
  read                    — return text content of the current page
  scroll <up|down>        — scroll the current page
  fill <selector> <value> — alias for type (form-fill ergonomics)
  screenshot [url]        — capture screenshot of current page (or navigate first)
  close                   — close the controlled session
  search <query>          — navigate to a DuckDuckGo search for <query>

Platform support matrix:
  ┌──────────────┬────────┬───────┬──────────────┬─────────┐
  │ Action       │ Linux  │ macOS │ Win / WSL    │ Termux  │
  ├──────────────┼────────┼───────┼──────────────┼─────────┤
  │ open         │   ✓    │   ✓   │      ✓       │    ✓    │
  │ fetch        │   ✓    │   ✓   │      ✓       │    ✓    │
  │ navigate     │   ✓    │   ✓   │      ✓       │    ✗*   │
  │ click        │   ✓    │   ✓   │      ✓       │    ✗*   │
  │ type / fill  │   ✓    │   ✓   │      ✓       │    ✗*   │
  │ read         │   ✓    │   ✓   │      ✓       │    ✗*   │
  │ scroll       │   ✓    │   ✓   │      ✓       │    ✗*   │
  │ screenshot   │   ✓    │   ✓   │      ✓       │    ✗*   │
  └──────────────┴────────┴───────┴──────────────┴─────────┘
  * Termux: playwright / headless Chromium unavailable; fetch is used instead.

Playwright is an optional dependency.  If it is not installed (or Chromium
is not downloaded) the controlled-session actions degrade gracefully with a
clear install hint.  Run `playwright install chromium` to enable them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _platform() -> str:
    import platform as _plat
    if _plat.system().lower() == "darwin":
        return "macos"
    if _plat.system().lower() == "windows":
        return "windows"
    if shutil.which("termux-setup-storage"):
        return "termux"
    try:
        if "microsoft" in Path("/proc/version").read_text().lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


_PLAYWRIGHT_HINT = (
    "playwright is not installed or Chromium is missing.\n"
    "Fix: pip install playwright && playwright install chromium"
)

# ---------------------------------------------------------------------------
# Stateful Playwright session
# Kept as module-level state so multiple Pilot commands share one browser.
# ---------------------------------------------------------------------------

class _Session:
    """Holds a single long-running Playwright browser + page."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pw: Any = None      # playwright context manager (sync_playwright)
        self._browser: Any = None
        self._page: Any = None

    def _start(self) -> Optional[str]:
        """Launch headless Chromium. Returns error string or None on success."""
        if not _playwright_available():
            return _PLAYWRIGHT_HINT
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
            self._pw = sync_playwright().__enter__()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            return None
        except Exception as exc:
            self._pw = None
            self._browser = None
            self._page = None
            return f"Could not start browser session: {exc}\n{_PLAYWRIGHT_HINT}"

    def ensure(self) -> Optional[str]:
        """Return an error string if the session can't be started, else None."""
        with self._lock:
            if self._page is None:
                return self._start()
        return None

    def page(self) -> Any:
        return self._page

    def close(self) -> None:
        with self._lock:
            try:
                if self._browser:
                    self._browser.close()
                if self._pw:
                    self._pw.__exit__(None, None, None)
            except Exception:
                pass
            finally:
                self._pw = None
                self._browser = None
                self._page = None


_session = _Session()


def _page_text(page: Any, max_lines: int = 200) -> str:
    """Extract visible text from the current page via beautifulsoup4."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:max_lines])
    except ImportError:
        # Fall back to Playwright's innerText
        return page.inner_text("body")[:8000]
    except Exception as exc:
        return f"(could not extract page text: {exc})"


# ---------------------------------------------------------------------------
# Action: open URL in system browser
# ---------------------------------------------------------------------------

def open_url(url: str) -> str:
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
# Action: HTTP fetch (no rendering — available on all platforms)
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = 15) -> str:
    try:
        import requests  # type: ignore[import]
        from bs4 import BeautifulSoup  # type: ignore[import]
        headers = {"User-Agent": (
            "Mozilla/5.0 (compatible; Gathm/3.0; "
            "+https://github.com/hakxcore/gathm)"
        )}
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:200])
    except ImportError:
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", str(timeout),
                 "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            return result.stdout.strip()[:6000] or f"No content returned for {url}"
        except Exception as exc:
            return (f"Cannot fetch {url}: {exc}. "
                    "Install 'requests' and 'beautifulsoup4' for web support.")
    except Exception as exc:
        return f"Error fetching {url}: {exc}"


# ---------------------------------------------------------------------------
# Playwright session-based actions (desktop only)
# ---------------------------------------------------------------------------

def _termux_only_open(action: str) -> str:
    return (
        f"'{action}' requires a headless browser which is not available on Termux. "
        "Use 'browser open <url>' to open in Android's browser, "
        "or 'browser fetch <url>' to read page content."
    )


def navigate(url: str) -> str:
    if _platform() == "termux":
        return _termux_only_open("navigate")
    err = _session.ensure()
    if err:
        return err
    try:
        _session.page().goto(url, wait_until="domcontentloaded", timeout=30_000)
        title = _session.page().title()
        return f"Navigated to: {url}\nPage title: {title}"
    except Exception as exc:
        return f"Navigation failed: {exc}"


def click_element(selector_or_text: str) -> str:
    if _platform() == "termux":
        return _termux_only_open("click")
    err = _session.ensure()
    if err:
        return err
    page = _session.page()
    try:
        # Try CSS/XPath selector first; fall back to visible text match
        try:
            page.click(selector_or_text, timeout=5_000)
        except Exception:
            page.get_by_text(selector_or_text).first.click(timeout=5_000)
        return f"Clicked: {selector_or_text}"
    except Exception as exc:
        return f"Click failed for '{selector_or_text}': {exc}"


def type_into(selector: str, text: str) -> str:
    if _platform() == "termux":
        return _termux_only_open("type")
    err = _session.ensure()
    if err:
        return err
    try:
        _session.page().fill(selector, text)
        return f"Typed into '{selector}': {text!r}"
    except Exception as exc:
        return f"Type failed for '{selector}': {exc}"


def read_current_page() -> str:
    if _platform() == "termux":
        return _termux_only_open("read")
    err = _session.ensure()
    if err:
        return err
    try:
        url = _session.page().url
        text = _page_text(_session.page())
        return f"[Current page: {url}]\n\n{text}"
    except Exception as exc:
        return f"Could not read page: {exc}"


def scroll_page(direction: str) -> str:
    if _platform() == "termux":
        return _termux_only_open("scroll")
    err = _session.ensure()
    if err:
        return err
    try:
        if direction.lower() in ("down", "d"):
            _session.page().keyboard.press("PageDown")
        elif direction.lower() in ("up", "u"):
            _session.page().keyboard.press("PageUp")
        else:
            return f"Unknown direction '{direction}'. Use 'up' or 'down'."
        return f"Scrolled {direction}"
    except Exception as exc:
        return f"Scroll failed: {exc}"


def screenshot(url: Optional[str] = None) -> str:
    if _platform() == "termux":
        return (
            "Screenshots are not supported on Termux. "
            "Use 'browser fetch <url>' to read page content instead."
        )
    err = _session.ensure()
    if err:
        return err
    try:
        page = _session.page()
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        import tempfile
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False, prefix="gathm_browser_"
        ) as fh:
            path = fh.name
        page.screenshot(path=path, full_page=False)
        return f"Screenshot saved → {path}"
    except Exception as exc:
        return f"Screenshot failed: {exc}"


def close_session() -> str:
    _session.close()
    return "Browser session closed."


def search_web(query: str) -> str:
    """Navigate to a DuckDuckGo search result page."""
    import urllib.parse
    search_url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
    if _platform() == "termux":
        return open_url(search_url)
    err = _session.ensure()
    if err:
        # Fall back to opening in system browser
        return open_url(search_url)
    try:
        _session.page().goto(search_url, wait_until="domcontentloaded", timeout=20_000)
        text = _page_text(_session.page(), max_lines=100)
        return f"[Search: {query}]\n{search_url}\n\n{text}"
    except Exception as exc:
        return f"Search navigation failed ({exc}) — opening in system browser instead.\n{open_url(search_url)}"


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_browser_action(command: str) -> str:
    """Dispatch a browser command issued by Pilot.

    Syntax:
        open <url>
        fetch <url>
        navigate <url>
        click <selector_or_text>
        type <selector> <text>
        fill <selector> <value>
        read
        scroll up|down
        screenshot [url]
        close
        search <query>
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return (
            "Usage: browser <action> [args]\n"
            "Actions: open, fetch, navigate, click, type, fill, read, "
            "scroll, screenshot, close, search"
        )

    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Ensure scheme for URL actions
    def _with_scheme(u: str) -> str:
        return u if u.startswith(("http://", "https://")) else "https://" + u

    if action == "open":
        if not rest:
            return "Usage: browser open <url>"
        return open_url(_with_scheme(rest))

    elif action == "fetch":
        if not rest:
            return "Usage: browser fetch <url>"
        content = fetch_page(_with_scheme(rest))
        return f"[Content from {rest}]\n\n{content}"

    elif action == "navigate":
        if not rest:
            return "Usage: browser navigate <url>"
        return navigate(_with_scheme(rest))

    elif action == "click":
        if not rest:
            return "Usage: browser click <css_selector_or_visible_text>"
        return click_element(rest)

    elif action in ("type", "fill"):
        # "type input#q hello world" → selector=input#q, text=hello world
        type_parts = rest.split(None, 1)
        if len(type_parts) < 2:
            return f"Usage: browser {action} <selector> <text>"
        return type_into(type_parts[0], type_parts[1])

    elif action == "read":
        return read_current_page()

    elif action == "scroll":
        direction = rest or "down"
        return scroll_page(direction)

    elif action in ("screenshot", "capture"):
        return screenshot(_with_scheme(rest) if rest else None)

    elif action == "close":
        return close_session()

    elif action == "search":
        if not rest:
            return "Usage: browser search <query>"
        return search_web(rest)

    else:
        return (
            f"Unknown browser action: '{action}'.\n"
            "Valid actions: open, fetch, navigate, click, type, fill, "
            "read, scroll, screenshot, close, search"
        )
