"""pilot/browser.py — Cross-platform browser control for Gathm Pilot.

Commands (dispatched by run_browser_action):
  open <url>              — open URL in the system's default browser (all platforms)
  fetch <url>             — HTTP-fetch page text via requests+bs4 / curl fallback
  navigate <url>          — load URL in the controlled headless session
  click <selector|text>   — click an element (CSS selector or visible text)
  type <selector> <text>  — type text into a field
  fill <selector> <value> — alias for type (form-fill ergonomics)
  read                    — return text content of the current page
  scroll <up|down>        — scroll the current page
  screenshot [url]        — capture screenshot of current page (or navigate first)
  search <query>          — DuckDuckGo search + show results
  close                   — close the controlled session

Platform support matrix (all actions available everywhere):
  ┌──────────────┬────────┬───────┬──────────────┬────────────────────────────────┐
  │ Action       │ Linux  │ macOS │ Win / WSL    │ Termux                         │
  ├──────────────┼────────┼───────┼──────────────┼────────────────────────────────┤
  │ open         │   ✓    │   ✓   │      ✓       │ ✓ termux-open-url              │
  │ fetch        │   ✓    │   ✓   │      ✓       │ ✓ requests/curl                │
  │ navigate     │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ click        │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ type / fill  │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ read         │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ scroll       │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ screenshot   │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  │ search       │   ✓    │   ✓   │      ✓       │ ✓ Selenium + pkg Chromium      │
  └──────────────┴────────┴───────┴──────────────┴────────────────────────────────┘

Backend selection:
  Desktop  — Playwright (playwright Python package + playwright install chromium)
             Falls back to any system Chrome/Chromium on PATH if bundled binary absent.
  Termux   — Selenium (pip install selenium) + system Chromium from pkg:
               pkg install tur-repo x11-repo && pkg install chromium
             The playwright Python package has no PyPI wheel for Android/aarch64.
             Selenium is pure Python and works everywhere pip works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Platform + binary helpers
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


def _find_system_chromium() -> Optional[str]:
    """Locate an installed system Chromium/Chrome binary."""
    prefix = os.environ.get("PREFIX", "")  # Termux sets this
    candidates = [
        os.path.join(prefix, "bin", "chromium-browser") if prefix else "",
        os.path.join(prefix, "bin", "chromium") if prefix else "",
        "/data/data/com.termux/files/usr/bin/chromium-browser",
        "/data/data/com.termux/files/usr/bin/chromium",
        shutil.which("chromium-browser") or "",
        shutil.which("chromium") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("google-chrome") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _find_chromedriver() -> Optional[str]:
    """Locate the chromedriver binary (used by the Selenium backend on Termux)."""
    prefix = os.environ.get("PREFIX", "")
    candidates = [
        os.path.join(prefix, "bin", "chromedriver") if prefix else "",
        "/data/data/com.termux/files/usr/bin/chromedriver",
        shutil.which("chromedriver") or "",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _selenium_available() -> bool:
    try:
        import selenium  # noqa: F401
        return True
    except ImportError:
        return False


_PLAYWRIGHT_HINT = (
    "playwright Python package is not installed.\n"
    "Fix: pip install playwright && playwright install chromium"
)

_SELENIUM_HINT = (
    "selenium is not installed.\n"
    "Fix: pip install selenium\n"
    "Also ensure Chromium is installed: pkg install tur-repo x11-repo && pkg install chromium"
)

_CHROMIUM_HINT = (
    "Chromium not found.  Install it:\n"
    "  Desktop: playwright install chromium\n"
    "  Termux:  pkg install tur-repo x11-repo && pkg install chromium"
)


# ---------------------------------------------------------------------------
# Selenium page wrapper
# Presents the subset of Playwright's page API that browser.py uses,
# so all action functions work unchanged against either backend.
# ---------------------------------------------------------------------------

class _SeleniumKeyboard:
    def __init__(self, driver: Any) -> None:
        self._d = driver

    def press(self, key: str) -> None:
        from selenium.webdriver.common.by import By      # type: ignore[import]
        from selenium.webdriver.common.keys import Keys  # type: ignore[import]
        key_map = {"PageDown": Keys.PAGE_DOWN, "PageUp": Keys.PAGE_UP}
        self._d.find_element(By.TAG_NAME, "body").send_keys(key_map.get(key, key))


class _SeleniumLocator:
    """Minimal stand-in for Playwright's Locator (get_by_text().first.click())."""

    def __init__(self, driver: Any, text: str) -> None:
        self._d = driver
        self._text = text
        self.first = self  # caller does .first.click()

    def click(self, timeout: int = 5_000) -> None:
        from selenium.webdriver.common.by import By                    # type: ignore[import]
        from selenium.webdriver.support.ui import WebDriverWait        # type: ignore[import]
        from selenium.webdriver.support import expected_conditions as EC  # type: ignore[import]
        loc = (By.XPATH,
               f'//*[contains(normalize-space(.), "{self._text}")]')
        WebDriverWait(self._d, timeout / 1000).until(
            EC.element_to_be_clickable(loc)
        ).click()


class _SeleniumPage:
    """Selenium WebDriver wrapped to match the Playwright page API used in browser.py."""

    def __init__(self, driver: Any) -> None:
        self._d = driver
        self.keyboard = _SeleniumKeyboard(driver)

    @property
    def url(self) -> str:
        return self._d.current_url

    def goto(self, url: str, wait_until: str = "load", timeout: int = 30_000) -> None:
        self._d.set_page_load_timeout(timeout / 1000)
        self._d.get(url)

    def title(self) -> str:
        return self._d.title

    def content(self) -> str:
        return self._d.page_source

    def inner_text(self, selector: str) -> str:
        try:
            from selenium.webdriver.common.by import By  # type: ignore[import]
            return self._d.find_element(By.CSS_SELECTOR, selector).text
        except Exception:
            return ""

    def click(self, selector: str, timeout: int = 5_000) -> None:
        from selenium.webdriver.common.by import By                    # type: ignore[import]
        from selenium.webdriver.support.ui import WebDriverWait        # type: ignore[import]
        from selenium.webdriver.support import expected_conditions as EC  # type: ignore[import]
        WebDriverWait(self._d, timeout / 1000).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
        ).click()

    def get_by_text(self, text: str) -> _SeleniumLocator:
        return _SeleniumLocator(self._d, text)

    def fill(self, selector: str, text: str) -> None:
        from selenium.webdriver.common.by import By  # type: ignore[import]
        el = self._d.find_element(By.CSS_SELECTOR, selector)
        el.clear()
        el.send_keys(text)

    def screenshot(self, path: str, full_page: bool = False) -> None:
        self._d.save_screenshot(path)


# ---------------------------------------------------------------------------
# Browser session  (Playwright on desktop, Selenium on Termux)
# ---------------------------------------------------------------------------

class _Session:
    """A single long-running browser session shared across all Pilot commands."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._page: Any = None   # _SeleniumPage (Termux) or Playwright page (desktop)
        self._pw: Any = None     # sync_playwright context (desktop only)
        self._browser: Any = None  # Playwright browser (desktop only)

    # -- Termux path: Selenium + system Chromium ----------------------------

    def _start_selenium(self) -> Optional[str]:
        if not _selenium_available():
            return _SELENIUM_HINT

        chromium = _find_system_chromium()
        if not chromium:
            return (
                "Chromium not found on Termux.\n"
                "Install: pkg install tur-repo x11-repo && pkg install chromium"
            )

        chromedriver = _find_chromedriver()
        if not chromedriver:
            return (
                "chromedriver not found.\n"
                "It ships with Termux's chromium package: pkg install chromium"
            )

        try:
            from selenium import webdriver                            # type: ignore[import]
            from selenium.webdriver.chrome.service import Service    # type: ignore[import]
            from selenium.webdriver.chrome.options import Options    # type: ignore[import]

            opts = Options()
            opts.binary_location = chromium
            for arg in ("--headless", "--no-sandbox",
                        "--disable-dev-shm-usage", "--disable-gpu"):
                opts.add_argument(arg)

            service = Service(executable_path=chromedriver)
            driver = webdriver.Chrome(service=service, options=opts)
            self._page = _SeleniumPage(driver)
            return None
        except Exception as exc:
            self._page = None
            return f"Could not start Selenium/Chromium session: {exc}\n{_SELENIUM_HINT}"

    # -- Desktop path: Playwright -------------------------------------------

    def _start_playwright(self) -> Optional[str]:
        if not _playwright_available():
            return _PLAYWRIGHT_HINT

        extra_args: list[str] = []
        executable_path: Optional[str] = None

        # Try playwright's bundled Chromium first; fall back to system binary.
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
            _test_pw = sync_playwright().__enter__()
            try:
                _test_pw.chromium.launch(headless=True).close()
                _test_pw.__exit__(None, None, None)
            except Exception:
                _test_pw.__exit__(None, None, None)
                executable_path = _find_system_chromium()
                if not executable_path:
                    return _CHROMIUM_HINT
        except Exception:
            return _PLAYWRIGHT_HINT

        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
            self._pw = sync_playwright().__enter__()
            kwargs: dict[str, Any] = {"headless": True, "args": extra_args}
            if executable_path:
                kwargs["executable_path"] = executable_path
            self._browser = self._pw.chromium.launch(**kwargs)
            self._page = self._browser.new_page()
            return None
        except Exception as exc:
            self._pw = None
            self._browser = None
            self._page = None
            return f"Could not start browser session: {exc}\n{_CHROMIUM_HINT}"

    # -- Shared interface ---------------------------------------------------

    def _start(self) -> Optional[str]:
        if _platform() == "termux":
            return self._start_selenium()
        return self._start_playwright()

    def ensure(self) -> Optional[str]:
        with self._lock:
            if self._page is None:
                return self._start()
        return None

    def page(self) -> Any:
        return self._page

    def close(self) -> None:
        with self._lock:
            try:
                if isinstance(self._page, _SeleniumPage):
                    self._page._d.quit()
                else:
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
    """Extract readable text from the current page."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]
        soup = BeautifulSoup(page.content(), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        lines = [ln for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
        return "\n".join(lines[:max_lines])
    except ImportError:
        return page.inner_text("body")[:8000]
    except Exception as exc:
        return f"(could not extract page text: {exc})"


# ---------------------------------------------------------------------------
# Action: open URL in the system's GUI browser
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
        return f"Browser opener not found ({exc}). Visit: {url}"
    except Exception as exc:
        return f"Could not open browser ({exc}). Visit: {url}"


# ---------------------------------------------------------------------------
# Action: HTTP fetch (no JS rendering — works everywhere without Chromium)
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
        lines = [ln for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
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
                    "Install 'requests' and 'beautifulsoup4'.")
    except Exception as exc:
        return f"Error fetching {url}: {exc}"


# ---------------------------------------------------------------------------
# Session-based actions (Playwright or Selenium, all platforms)
# ---------------------------------------------------------------------------

def navigate(url: str) -> str:
    err = _session.ensure()
    if err:
        return err
    try:
        _session.page().goto(url, wait_until="domcontentloaded", timeout=30_000)
        return f"Navigated to: {url}\nPage title: {_session.page().title()}"
    except Exception as exc:
        return f"Navigation failed: {exc}"


def click_element(selector_or_text: str) -> str:
    err = _session.ensure()
    if err:
        return err
    page = _session.page()
    try:
        try:
            page.click(selector_or_text, timeout=5_000)
        except Exception:
            page.get_by_text(selector_or_text).first.click(timeout=5_000)
        return f"Clicked: {selector_or_text}"
    except Exception as exc:
        return f"Click failed for '{selector_or_text}': {exc}"


def type_into(selector: str, text: str) -> str:
    err = _session.ensure()
    if err:
        return err
    try:
        _session.page().fill(selector, text)
        return f"Typed into '{selector}': {text!r}"
    except Exception as exc:
        return f"Type failed for '{selector}': {exc}"


def read_current_page() -> str:
    err = _session.ensure()
    if err:
        return err
    try:
        return f"[Current page: {_session.page().url}]\n\n{_page_text(_session.page())}"
    except Exception as exc:
        return f"Could not read page: {exc}"


def scroll_page(direction: str) -> str:
    err = _session.ensure()
    if err:
        return err
    try:
        key = "PageDown" if direction.lower() in ("down", "d") else \
              "PageUp"   if direction.lower() in ("up", "u")   else None
        if key is None:
            return f"Unknown direction '{direction}'. Use 'up' or 'down'."
        _session.page().keyboard.press(key)
        return f"Scrolled {direction}"
    except Exception as exc:
        return f"Scroll failed: {exc}"


def screenshot(url: Optional[str] = None) -> str:
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
    import urllib.parse
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
    err = _session.ensure()
    if err:
        return open_url(url)
    try:
        _session.page().goto(url, wait_until="domcontentloaded", timeout=20_000)
        return f"[Search: {query}]\n{url}\n\n{_page_text(_session.page(), max_lines=100)}"
    except Exception as exc:
        return (f"Search navigation failed ({exc}) — "
                f"opening in system browser.\n{open_url(url)}")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_browser_action(command: str) -> str:
    """Dispatch a browser command from Pilot."""
    parts = command.strip().split(None, 1)
    if not parts:
        return (
            "Usage: browser <action> [args]\n"
            "Actions: open fetch navigate click type fill read scroll "
            "screenshot search close"
        )

    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    def _with_scheme(u: str) -> str:
        return u if u.startswith(("http://", "https://")) else "https://" + u

    if action == "open":
        return open_url(_with_scheme(rest)) if rest else "Usage: browser open <url>"

    elif action == "fetch":
        if not rest:
            return "Usage: browser fetch <url>"
        return f"[Content from {rest}]\n\n{fetch_page(_with_scheme(rest))}"

    elif action == "navigate":
        return navigate(_with_scheme(rest)) if rest else "Usage: browser navigate <url>"

    elif action == "click":
        return click_element(rest) if rest else "Usage: browser click <selector|text>"

    elif action in ("type", "fill"):
        tp = rest.split(None, 1)
        if len(tp) < 2:
            return f"Usage: browser {action} <selector> <text>"
        return type_into(tp[0], tp[1])

    elif action == "read":
        return read_current_page()

    elif action == "scroll":
        return scroll_page(rest or "down")

    elif action in ("screenshot", "capture"):
        return screenshot(_with_scheme(rest) if rest else None)

    elif action == "close":
        return close_session()

    elif action == "search":
        return search_web(rest) if rest else "Usage: browser search <query>"

    else:
        return (
            f"Unknown browser action: '{action}'.\n"
            "Valid: open fetch navigate click type fill read scroll "
            "screenshot search close"
        )
