import os
import shutil
import subprocess
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Annotated, Any, List, Optional, TypedDict

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:  # type: ignore[override]
        return False

LANGCHAIN_IMPORT_ERROR: Optional[Exception] = None
LANGCHAIN_AVAILABLE = True
try:
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
    from langgraph.graph import StateGraph, END
except ImportError as exc:
    # ImportError, not just ModuleNotFoundError. langchain_core resolves public
    # names lazily through a module __getattr__ and re-raises any miss as a
    # bare ImportError -- e.g. "module 'langchain_core.runnables'.'base' not
    # found" when a version mismatch or a broken install prevents a submodule
    # from importing. That is not a ModuleNotFoundError, so a narrower except
    # let it escape and crashed Pilot with a traceback instead of falling back
    # to the degraded no-LangChain mode this block exists to provide.
    LANGCHAIN_IMPORT_ERROR = exc
    LANGCHAIN_AVAILABLE = False
    AIMessage = Any  # type: ignore[assignment]
    BaseMessage = Any  # type: ignore[assignment]
    HumanMessage = Any  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    END = "__END__"

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"

# --- Unified LLM provider (single source of truth for backend/model config) ---
sys.path.insert(0, str(GATHM_ROOT))
try:
    from lib.llm import LLMConfig, LLMProvider
    _llm_config = LLMConfig.from_env()
    LLM_BACKEND = _llm_config.backend
    OLLAMA_MODEL = _llm_config.model
except Exception:
    LLM_BACKEND = os.getenv("GATHM_LLM_BACKEND", "ollama")
    OLLAMA_MODEL = os.getenv("GATHM_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "gemma3:12b"))
    _llm_config = None

PILOT_MAX_HISTORY = int(os.getenv("PILOT_MAX_HISTORY", "12"))

def _build_llm():
    """Instantiate the LangChain chat model via the unified LLM provider."""
    if _llm_config is not None:
        return LLMProvider(_llm_config).langchain_chat_model()
    # Fallback if lib.llm failed to import
    from langchain_ollama import ChatOllama  # type: ignore[import]
    return ChatOllama(model=OLLAMA_MODEL)

# Colors (kept for non-TUI code paths)
SAFFRON = "\033[38;5;208m"
WHITE_BOLD = "\033[1;37m"
INDIAN_GREEN = "\033[38;5;28m"
ASHOKA_BLUE = "\033[38;5;20m"
RESET = "\033[0m"

# TUI module — try the package-relative import first, then the flat one
# (Pilot is launched as `python main.py`, so `pilot` may not be on sys.path).
# A genuinely missing dependency (e.g. `rich`) is reported cleanly instead
# of crashing with a raw ModuleNotFoundError traceback.
try:
    from pilot.tui import (
        render_welcome, print_prompt, render_response, print_tool_exec,
        print_status_bar, render_help, render_tools_list, render_error,
        render_goodbye, check_connectivity,
        start_waiting, stop_waiting, print_user_message, get_user_input,
        stop_speaking, start_reply_stream, console,
    )
except ImportError:
    try:
        from tui import (  # type: ignore[no-redef]
            render_welcome, print_prompt, render_response, print_tool_exec,
            print_status_bar, render_help, render_tools_list, render_error,
            render_goodbye, check_connectivity,
            start_waiting, stop_waiting, print_user_message, get_user_input,
            stop_speaking, start_reply_stream, console,
        )
    except ImportError as exc:
        missing = getattr(exc, "name", None) or str(exc)
        sys.stderr.write(
            f"\nPilot can't start — a required dependency is missing: {missing}\n\n"
            "Install Pilot's dependencies and try again:\n"
            "    pip install -r pilot/requirements.txt\n"
            "or re-run the installer:\n"
            "    ./install\n\n"
        )
        raise SystemExit(1)


TOOL_ALIASES = {
    "movies": "movie",
}

# Built-in tools handled directly in Python (not shell scripts in tools/)
BUILTIN_TOOLS: dict[str, str] = {
    "browser": "Open URLs, fetch web pages, or take screenshots. "
               "Usage: browser open <url> | browser fetch <url> | browser screenshot <url>",
    "system": "Run a shell command on this machine to inspect or control it "
              "(disk space, processes, network, installing things, files). "
              "Usage: system <command>",
}

# Lazy-import the browser module so a missing optional dep doesn't crash Pilot
def _run_browser_action(command: str) -> str:
    try:
        try:
            from pilot.browser import run_browser_action
        except ImportError:
            from browser import run_browser_action  # type: ignore[no-redef]
        return run_browser_action(command)
    except Exception as exc:
        return f"Browser error: {exc}"

# --- Running commands on this machine -------------------------------------
# lib/sysexec.py decides what may run; this decides who gets asked. The two are
# separate on purpose: the rules are testable without a terminal, and the
# prompt is the only part that needs one.
try:
    from lib import sysexec as _sysexec
except Exception:  # noqa: BLE001 - degrade to "no system control"
    _sysexec = None


def _can_ask_the_user() -> bool:
    """Whether there is a human at a terminal to confirm with.

    chat_once (the GUI's per-turn process) has no usable stdin, and prompting
    into a void would hang a web request forever. Anything needing
    confirmation is refused there instead, with a message saying where it can
    be confirmed.
    """
    if os.environ.get("GATHM_NON_INTERACTIVE") == "1":
        return False
    try:
        return sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        return False


def _confirm_command(command: str, reason: str) -> bool:
    """Ask before running something that can change the machine."""
    # The shimmer animation is mid-write on this line; leaving it running would
    # scribble over the question.
    stop_waiting()
    console.print()
    console.print(
        "  [color(208) bold][?][/color(208) bold] Gathm wants to run a command "
        "on this machine:"
    )
    console.print(f"      [bold]{command}[/bold]")
    console.print(f"      [color(244)]{reason}[/color(244)]")
    try:
        answer = input("      run it? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    granted = answer in ("y", "yes")
    console.print()
    start_waiting()
    return granted


def _run_system_command(command: str) -> str:
    """The `system` tool: run a shell command, with the guardrails."""
    if _sysexec is None:
        return ("Error: system control is unavailable — lib/sysexec.py could "
                "not be imported.")
    command = (command or "").strip()
    if not command:
        # The example has to be runnable here, or a small model will copy a
        # bash command onto a Windows box and then explain the failure to us.
        example = ("system Get-Volume"
                   if _sysexec.shell_dialect() == "windows"
                   else "system df -h")
        return (f"Usage: system <command>   e.g. {example}\n"
                f"This machine is: {_sysexec.platform_summary()}\n"
                f"Commands run in: {_sysexec.shell_label()}")

    approve = _confirm_command if _can_ask_the_user() else None
    ok_flag, output = _sysexec.run(command, approve=approve)
    if ok_flag:
        return output
    return f"Command not completed: {output}"


HIGH_RISK_QUERY_PATTERNS = (
    r"\bopen(?:ly)?\s+available\s+(?:ftp|cameras?)\b",
    r"\bpublic(?:ly)?\s+accessible\s+(?:ftp|cameras?)\b",
    r"\bopen\s+ftp\s+servers?\b",
    r"\bopen\s+cameras?\b",
    r"\bopennel\b.*\bcameras?\b",
)

# --- 1. Tool Discovery & Execution ---

_PLATFORM_LABELS = {
    "termux": "Termux (Android)",
    "macos": "macOS",
    "linux": "Linux",
    "windows": "Windows",
    "ios": "iOS",
}


def _detect_platform() -> str:
    """Detect platform for the welcome box.

    This defers to sysexec.platform_name() rather than detecting anything
    itself. It used to ask platform.system(), which on a current Termux
    answers "Android" — so it fell past both branches and printed the raw
    string, and the termux-setup-storage check that would have said "Termux"
    could never run. Gathm had four platform detectors with four different
    vocabularies; disagreements between two of them have now cost a day
    twice, so this one is no longer a detector.
    """
    if _sysexec is not None:
        name = _sysexec.platform_name()
        return _PLATFORM_LABELS.get(name, name)

    import platform
    system = platform.system().lower()
    if system == "darwin":
        return "macOS"
    if system in ("linux", "android"):
        if shutil.which("termux-setup-storage"):
            return "Termux (Android)"
        return "Linux"
    return system

def print_tricolor_banner():
    """Print the full tricolor TUI welcome screen."""
    tool_count = len(discover_tools())
    plat = _detect_platform()
    model_label = f"{OLLAMA_MODEL} [{LLM_BACKEND.upper()}]"
    connectivity = check_connectivity()
    # render_welcome handles os.system("clear") internally
    render_welcome(model_label, tool_count, plat, connectivity=connectivity)
    print_status_bar()

def describe_agent_failure(exc: BaseException) -> str:
    """Turn a raw agent exception into something the user can act on.

    A dead Ollama server surfaced as bare "[Errno 111] Connection refused" in
    the TUI and "agent error: [Errno 111] ..." over the API — neither says what
    to start. Walk the
    exception chain looking for a refused/unreachable connection and name the
    server and the fix instead.
    """
    seen = []
    cur: BaseException | None = exc
    while cur is not None and len(seen) < 10:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__

    text = " | ".join("%s: %s" % (type(e).__name__, e) for e in seen).lower()
    refused = (
        "errno 111" in text
        or "connection refused" in text
        or "connectionerror" in text
        or "failed to establish a new connection" in text
        or "max retries exceeded" in text
        or "all connection attempts failed" in text
        or "cannot connect to host" in text
    )
    if not refused:
        return "agent error: %s" % exc

    backend = os.environ.get("GATHM_LLM_BACKEND", "ollama")
    if backend == "ollama":
        url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        host = url.split("/v1")[0]
        return (
            "cannot reach the Ollama server at %s — it is not running. "
            "Start it with:  ollama serve  (then retry)" % host
        )
    return (
        "cannot reach the %s LLM backend — the connection was refused. "
        "Check that it is running and reachable." % backend
    )


# How many identical failures in a row before Pilot gives up on its own loop.
LOOP_ERROR_LIMIT = 3


def track_loop_error(message: str, last: str, count: int) -> tuple:
    """(last, count, give_up) for one pass of the main loop's error handler.

    An error raised while READING INPUT reproduces on the very next pass, so a
    handler that renders it and continues spins as fast as it can print — which
    is how one broken prompt became hundreds of identical panels. Counting
    identical consecutive failures turns that into three panels and an exit.
    """
    if message == last:
        count += 1
    else:
        last, count = message, 1
    return last, count, count >= LOOP_ERROR_LIMIT


def report_to_engineer(error_msg: str, task: str):
    """Notify the user and trigger the AutoGen Engineer."""
    print(f"\n{SAFFRON}[!] Issue Detected:{RESET} {error_msg}")
    print(f"{WHITE_BOLD}[*] Don't worry, our Engineer will take care of this! It will be resolved shortly.{RESET}")
    
    # In a real system, we'd trigger the background engineer here:
    # subprocess.run(["bash", "-c", f"gathm engineer 'Fix the following error in task \"{task}\": {error_msg}'"], is_background=True)
    # For now, we simulate the hand-off.
    log_file = Path.home() / ".gathm" / "agent" / "engineer_tasks.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(f"Task: {task} | Error: {error_msg}\n")

def discover_tools():
    """Return available tool names: shell tools from tools/ plus built-ins."""
    available = list(BUILTIN_TOOLS.keys())  # built-ins always present
    if TOOLS_DIR.is_dir():
        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if tool_dir.is_dir():
                executable = tool_dir / tool_dir.name
                if executable.is_file() and os.access(executable, os.X_OK):
                    available.append(tool_dir.name)
    return available

def get_tool_description(tool_name):
    """Return description for a tool (built-in or shell-based)."""
    if tool_name in BUILTIN_TOOLS:
        return BUILTIN_TOOLS[tool_name]
    yaml_path = TOOLS_DIR / tool_name / "tool.yaml"
    if yaml_path.is_file():
        try:
            for line in yaml_path.read_text().splitlines():
                if line.strip().startswith("description:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return f"Run the {tool_name} tool"

# Tools whose manifest sets `requires_internet: false` work offline. We cache
# the parsed value per tool since the manifest set is static during a session.
_REQUIRES_INTERNET_CACHE: dict[str, bool] = {}

def tool_requires_internet(tool_name: str) -> bool:
    """Whether a tool needs an internet connection for its core function.

    Reads `requires_internet` from the tool's manifest. Defaults to True for
    anything we can't read (built-ins, missing/garbled manifest) so we never
    offer a tool offline that would just fail — the conservative choice.
    """
    if tool_name in _REQUIRES_INTERNET_CACHE:
        return _REQUIRES_INTERNET_CACHE[tool_name]

    result = True  # safe default
    if tool_name not in BUILTIN_TOOLS:
        yaml_path = TOOLS_DIR / tool_name / "tool.yaml"
        if yaml_path.is_file():
            try:
                for line in yaml_path.read_text().splitlines():
                    s = line.strip()
                    if s.startswith("requires_internet:"):
                        val = s.split(":", 1)[1].split("#", 1)[0].strip()
                        val = val.strip('"').strip("'").lower()
                        result = val not in ("false", "no", "0", "off")
                        break
            except Exception:
                pass

    _REQUIRES_INTERNET_CACHE[tool_name] = result
    return result

# Connectivity can change mid-session, but check_connectivity() opens a socket
# with a 3s timeout — too costly to call on every model turn. Cache the result
# for a short window so an offline session pays that cost at most once per TTL.
_CONN_CACHE: dict[str, float | str] = {"status": "", "ts": 0.0}
_CONN_TTL_SECONDS = 30.0

def current_connectivity() -> str:
    """Return 'online'/'offline', cached for _CONN_TTL_SECONDS."""
    now = time.monotonic()
    if not _CONN_CACHE["status"] or (now - float(_CONN_CACHE["ts"])) > _CONN_TTL_SECONDS:
        _CONN_CACHE["status"] = check_connectivity()
        _CONN_CACHE["ts"] = now
    return str(_CONN_CACHE["status"])

def _looks_number(value: str) -> bool:
    return bool(re.match(r"^-?\d+(\.\d+)?$", value.strip()))

def _normalize_tool_invocation(parts: List[str]) -> List[str]:
    if not parts:
        return parts

    tool_name = TOOL_ALIASES.get(parts[0].lower(), parts[0].lower())
    args = parts[1:]

    # Normalize common LLM currency mix-up:
    # currency 100 USD EUR -> currency USD EUR 100
    if tool_name == "currency" and len(args) == 3:
        if _looks_number(args[0]) and not _looks_number(args[2]):
            args = [args[1], args[2], args[0]]

    # gif only accepts a single keyword argument; normalize multi-word variants.
    if tool_name == "gif":
        if args and args[0].lower() in {"show", "search"}:
            args = args[1:]
        if len(args) > 1:
            args = ["_".join(args)]

    return [tool_name, *args]

def normalize_tool_command(command: str) -> str:
    parts = shlex.split(command.strip())
    normalized = _normalize_tool_invocation(parts)
    return shlex.join(normalized)

def run_gathm_tool_raw(command: str) -> str:
    text = (command or "").strip()

    # `system` is handled before any parsing. Its argument is a shell command,
    # so quotes, pipes and redirects are meaningful — shlex.split would tokenise
    # them and shlex.join would quote them back into something different, and an
    # unbalanced quote would be rejected here rather than by the shell that has
    # to run it. The string the classifier reads is the string that runs.
    if text == "system" or text.startswith("system "):
        return _run_system_command(text[len("system"):].strip())

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return f"Error: Invalid command syntax ({exc})."
    if not parts:
        return "Error: No tool specified."

    normalized_parts = _normalize_tool_invocation(parts)
    tool_name = normalized_parts[0]
    tool_args = normalized_parts[1:]

    # Dispatch built-in tools before looking in the shell tools directory
    if tool_name == "browser":
        return _run_browser_action(" ".join(tool_args))

    tool_path = TOOLS_DIR / tool_name / tool_name
    if not tool_path.is_file():
        return f"Error: Tool '{tool_name}' not found."
    try:
        env = {**os.environ, "TERM": "xterm-256color", "GATHM_NON_INTERACTIVE": "1"}
        shell_cmd = f'source "{GATHM_ROOT}/lib/utils.bash" && "{tool_path}" "$@"'
        result = subprocess.run(
            ["bash", "-c", shell_cmd, "gathm-tool", *tool_args],
            capture_output=True,
            text=True,
            timeout=120,
            env=env
        )
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        return output if output else "(no output)"
    except Exception as e:
        return f"Error: {e}"

def classify_high_risk_query(user_input: str) -> Optional[str]:
    normalized = user_input.lower().strip()
    for pattern in HIGH_RISK_QUERY_PATTERNS:
        if re.search(pattern, normalized):
            return "exposed-infrastructure-discovery"
    return None

def safety_refusal_message() -> str:
    return (
        "I can't help with finding exposed/publicly accessible cameras or FTP servers. "
        "I can help with defensive steps like securing your own services or setting up authorized test targets."
    )

def extract_tool_input(content: str, available_tools: Optional[set] = None) -> Optional[str]:
    if not isinstance(content, str):
        return None

    known_tools = available_tools or set(discover_tools())

    action_input_match = re.search(r"Action Input:\s*(.+)", content, re.IGNORECASE)
    if action_input_match:
        action_input = action_input_match.group(1).strip()
        if action_input and "tool_name" not in action_input.lower():
            # Reject placeholder strings
            if any(x in action_input.lower() for x in ["[tool_name]", "[arguments]", "<arg>"]):
                return None
            # Validate the first word is a real tool.
            # Without this check, hallucinated tool names like "define" route to
            # tool_node → "Error: Tool not found" → model retries → recursion limit.
            try:
                first_word = shlex.split(action_input)[0].lower()
                first_word = TOOL_ALIASES.get(first_word, first_word)
                if first_word in known_tools:
                    return action_input
            except ValueError:
                pass
            return None

    action_lines = [line.strip() for line in content.splitlines() if line.strip().lower().startswith("action:")]
    if not action_lines:
        return None

    payload = action_lines[-1].split(":", 1)[1].strip()
    if not payload:
        return None

    if payload.lower() == "gathm":
        return None
    if payload.lower().startswith("gathm "):
        payload = payload[6:].strip()
        return payload or None

    try:
        payload_parts = shlex.split(payload)
    except ValueError:
        return None
    if not payload_parts:
        return None

    tool_name = TOOL_ALIASES.get(payload_parts[0].lower(), payload_parts[0].lower())

    # Check if it is a real tool and not a placeholder
    if tool_name in known_tools:
        if any(x in payload.lower() for x in ["[tool_name]", "[arguments]", "<arg>"]):
            return None
        payload_parts[0] = tool_name
        return shlex.join(payload_parts)

    return None

# --- Speaking the reply while it is still being written -------------------
#
# The model is the slow part of a spoken answer: several seconds on a phone
# before invoke() returns anything at all. Streaming the tokens into the
# speech stream means the first sentence is being said while the rest is
# still being generated, which is what makes a voice conversation feel live
# rather than walkie-talkie.
#
# The catch is that a ReAct tool call looks like a reply until you read it.
# "Thought: I should use the weather tool" must never be spoken, and it is
# only recognisable once the first line exists — so the head of the response
# is held back until it is clear this is a plain answer.

_REACT_MARKER_RE = re.compile(r"(?im)^\s*(thought|action|action input|observation)\s*:")
_REACT_HEAD_CHARS = 80

# Set for the duration of one query by the main loop; None the rest of the
# time, which is also what makes this a no-op for the API and chat_once paths.
_TOKEN_SINK = None


class _ReplySpeech:
    """Feeds assistant tokens to a speech stream as they are generated."""

    def __init__(self, stream):
        self.stream = stream
        self.fed = False

    def feed(self, text: str) -> None:
        if not text:
            return
        try:
            self.stream.feed(text)
            self.fed = True
        except Exception:  # noqa: BLE001 - speech never breaks a reply
            pass

    def close(self) -> None:
        try:
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass


def _set_token_sink(sink) -> None:
    global _TOKEN_SINK
    _TOKEN_SINK = sink


def _token_text(content) -> str:
    """The text of one streamed chunk.

    Chat models mostly yield a plain string, but some yield a list of content
    blocks (`[{"type": "text", "text": "..."}]`). Joining the wrong shape would
    put Python repr into the spoken reply, so both are handled here.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                out.append(str(block.get("text") or ""))
        return "".join(out)
    return "" if content is None else str(content)


def _invoke_spoken(llm, messages):
    """llm.invoke(), but speaking the answer as the tokens arrive.

    Falls back to a plain invoke when nothing is listening or the model has no
    streaming interface, so behaviour is unchanged wherever speech is off.
    """
    sink = _TOKEN_SINK
    if sink is None or not hasattr(llm, "stream"):
        return llm.invoke(messages)

    try:
        from langchain_core.messages import AIMessage as _AIMsg
    except ImportError:
        # Degraded mode (no LangChain installed) still has a caller that only
        # reads .content, so a bare carrier is enough.
        class _AIMsg:  # type: ignore[no-redef]
            def __init__(self, content: str):
                self.content = content

    parts: List[str] = []
    head = ""
    speaking: Optional[bool] = None      # None = still deciding
    try:
        for piece in llm.stream(messages):
            token = _token_text(getattr(piece, "content", ""))
            if not token:
                continue
            parts.append(token)
            if speaking is None:
                head += token
                if _REACT_MARKER_RE.search(head):
                    speaking = False     # a tool call — say nothing
                elif len(head) >= _REACT_HEAD_CHARS:
                    speaking = True
                    sink.feed(head)
            elif speaking:
                sink.feed(token)
    except Exception:
        # Partial output is still worth returning; a failure with nothing to
        # show is a real error and belongs with the caller's handler.
        if not parts:
            raise

    text = "".join(parts)
    # A short answer that never reached the head limit: decide now.
    if speaking is None and text and not _REACT_MARKER_RE.search(text):
        sink.feed(text)
    return _AIMsg(content=text)


def _clean_agent_response(content: str) -> str:
    """Strip ReAct scaffolding lines (Thought/Action/Observation) from the
    model's reply so the user sees only the actual answer.

    Small models like gemma3:4b sometimes emit the full chain-of-thought even
    when they are not invoking a tool.  This removes those lines while
    preserving everything else.  Falls back to the original content when
    cleaning would leave an empty string.
    """
    _STRIP_PREFIXES = ("thought:", "action:", "action input:", "observation:")
    lines = content.splitlines()
    kept = [ln for ln in lines if not ln.strip().lower().startswith(_STRIP_PREFIXES)]
    cleaned = "\n".join(kept).strip()
    return cleaned if cleaned else content


# --- 2. LangGraph Stateful Reasoning (Text-based Tool Calling) ---

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    next_step: str

def _require_langchain_runtime() -> None:
    if not LANGCHAIN_AVAILABLE:
        detail = f": {LANGCHAIN_IMPORT_ERROR}" if LANGCHAIN_IMPORT_ERROR else ""
        raise RuntimeError(
            "Pilot AI runtime dependencies are missing. "
            "Install pilot/requirements.txt and retry" + detail
        )

# Messages that never need a tool. Kept deliberately tight: the whole message
# must be small talk, so "hi" matches but "hi, what is AAPL trading at" does not.
_SMALL_TALK = {
    "hi", "hii", "hiii", "hey", "heyy", "hello", "helo", "yo", "sup",
    "hi there", "hello there", "hey there", "good morning", "good afternoon",
    "good evening", "good night", "gm", "gn",
    "thanks", "thank you", "thx", "ty", "cheers", "nice", "cool", "ok", "okay",
    "bye", "goodbye", "see you", "who are you", "what are you", "what can you do",
    "how are you", "how are you?", "what's up", "whats up",
}


def _is_small_talk(text: str) -> bool:
    """True when the entire message is a greeting or pleasantry."""
    cleaned = re.sub(r"[^\w\s']", "", (text or "")).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or len(cleaned) > 24:
        return False
    return cleaned in _SMALL_TALK


# Short prompt for small talk: no tool list at all.
#
# The full prompt lists every tool plus 14 numbered rules, and rule 0 already
# says not to call a tool for a greeting. A 1B model on a phone cannot reliably
# honour that — "hi" came back as a stocks lookup reciting a 2023 AAPL price,
# because the rules mention "For company STOCKS (Apple, Google)". Removing the
# tools from the prompt makes the misroute structurally impossible instead of
# merely forbidden, and the far shorter prompt is markedly faster on-device.
_SMALL_TALK_PROMPT = """You are Pilot, a friendly AI assistant for the Gathm ecosystem.
Reply to the user conversationally in one or two short sentences.
Do not mention tools, actions, or your own instructions. Plain text only."""


# Every turn used to send all 56 tool descriptions — 4.4 KB, about 1100 tokens
# of the ~1800-token system prompt — before the model could produce a single
# word. On a phone that prefill is the bulk of the wait, which is why
# `ollama run gemma3:1b` felt instant next to Pilot. Only the tools plausibly
# related to the question are listed now. 0 restores the full list.
TOOL_SHORTLIST = int(os.getenv("GATHM_TOOL_SHORTLIST", "10"))

# Words that carry no routing signal, so they must not score a tool.
_STOPWORDS = frozenset("""
a an and are as at be but by can could do does for from get give go had has have
how i if in into is it its me my no not of on or please should show so tell that
the their them then there these this to up us use was we were what when where
which who why will with would you your
""".split())

# Asked about its own capabilities, the model does need the whole catalogue.
_CATALOGUE_RE = re.compile(
    r"\b(what (can|do) you (do|have)|which tools|list (the )?tools|"
    r"your tools|available tools|capabilit(y|ies)|help me with)\b", re.I)


def _query_terms(query: str) -> set:
    words = re.findall(r"[a-z0-9]+", (query or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


_TOOL_INDEX: dict = {}


# Built-in tools have no tool.yaml, so they have no tags — and tags are where
# the words a user actually types live. Without these, "what macOS version am
# I on" scored zero against every tool and fell through to the
# weather/dns/ipinfo fallback, so Pilot answered a question about the machine
# with a geolocation lookup. These are index-only: they are never shown to the
# model, so the descriptions above stay readable prose.
#
# Words other tools own are deliberately absent — "ip" belongs to ipinfo,
# "search" to websearch — because taking them would break those instead.
BUILTIN_TAGS = {
    "system": {
        "machine", "computer", "laptop", "device", "phone", "os", "version",
        "macos", "mac", "osx", "windows", "linux", "termux", "android", "ios",
        "kernel", "uname", "hostname", "arch", "architecture",
        "memory", "ram", "swap", "cpu", "processor", "core", "load",
        "disk", "storage", "space", "free", "usage", "battery", "uptime",
        "process", "running", "port", "listening", "service", "daemon",
        "install", "uninstall", "update", "upgrade", "package", "brew", "apt",
        "pkg", "dpkg", "pip", "npm",
        "file", "folder", "directory", "path", "permission", "size",
        "shell", "command", "terminal", "bash", "zsh", "powershell",
        "environment", "variable", "user", "whoami", "sudo", "root",
    },
    "browser": {
        "url", "website", "webpage", "page", "site", "click", "screenshot",
        "navigate", "form", "link", "scroll", "chrome", "chromium", "tab",
    },
}


def _tool_index() -> dict:
    """{tool: (words, tags)} from its description and manifest, built once.

    The manifest tags matter: a description is prose aimed at the model, while
    tags are the vocabulary a user actually types ("registrar", "calculus"),
    and reading them is what keeps narrowing from hiding the right tool.
    """
    if _TOOL_INDEX:
        return _TOOL_INDEX
    for name in discover_tools():
        text = (get_tool_description(name) or "").lower()
        tags: set = set()
        manifest = TOOLS_DIR / name / "tool.yaml"
        try:
            for line in manifest.read_text().splitlines():
                if line.startswith("tags:"):
                    tags = {t.strip().strip("\"'") for t in
                            line.split(":", 1)[1].strip(" []").split(",")}
                    tags = {t for t in tags if t}
                    break
        except Exception:  # noqa: BLE001 - a tool without a manifest still works
            tags = set()
        tags |= BUILTIN_TAGS.get(name, set())
        words = set(re.findall(r"[a-z0-9]+", text)) | tags
        _TOOL_INDEX[name] = (words, tags)
    return _TOOL_INDEX


def _matches(term: str, words: set) -> int:
    """Score one query term against one tool's vocabulary."""
    if term in words:
        return 4
    # Light stemming by shared prefix: "derivative" has to find "derive",
    # "registered" has to find "registrar", "transcription" has to find
    # "transcribe". Five characters is long enough not to collide by accident.
    if len(term) >= 5:
        for word in words:
            if len(word) >= 5 and (term.startswith(word[:5]) or word.startswith(term[:5])):
                return 2
    return 0


def _shortlist_tools(query: str, tools: list) -> list:
    """Tools worth showing the model for this question, best first.

    Scores each tool's name, description and manifest tags against the query's
    content words. A tool named outright always wins; when nothing matches at
    all the everyday tools are offered rather than an empty list, so a vague
    question can still route.
    """
    if TOOL_SHORTLIST <= 0 or len(tools) <= TOOL_SHORTLIST:
        return tools
    if _CATALOGUE_RE.search(query or ""):
        return tools

    terms = _query_terms(query)
    index = _tool_index()
    lowered = (query or "").lower()
    scored = []
    for name in tools:
        words, tags = index.get(name, (set(), set()))
        score = 0
        if name.lower() in lowered:
            score += 100                      # named the tool explicitly
        for term in terms:
            if term == name.lower():
                score += 50
            elif term in name.lower():
                score += 8
            if term in tags:
                score += 6
            score += _matches(term, words)
        if score:
            scored.append((score, name))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    picked = [name for _score, name in scored[:TOOL_SHORTLIST]]

    if not picked:
        # No signal at all: the common cases, so "how hot is it" still finds a
        # way. `system` is in here because a question with no other signal is
        # at least as likely to be about the machine in front of the user as
        # about the weather, and without it the model cannot even see which
        # platform it is on.
        fallback = ["weather", "websearch", "system", "dns", "ipinfo",
                    "define", "news", "browser", "stocks", "cryptocurrency",
                    "currency"]
        picked = [t for t in fallback if t in tools][:TOOL_SHORTLIST]
    return picked


_SYSTEM_HELP = """13. To INSPECT OR CONTROL THIS MACHINE use the 'system' tool with a shell
    command: Action Input: system <command>
    This machine is: {platform}
    Commands run in: {shell}
    System control is currently: {state}
    Write the command for THAT platform and THAT shell:
    - macOS: `sw_vers` not `lsb_release`, `vm_stat` not `free`, `brew` for packages
    - Termux (Android): `pkg` not `apt`, `getprop` for device details
    - Linux: `lsb_release`, `free`, `apt`/`dpkg`
    - Windows (powershell/pwsh): `Get-ComputerInfo` not `uname`,
      `Get-ChildItem` not `ls`, `Get-Process` not `ps`, `ipconfig` not
      `ifconfig`, `Get-PSDrive` or `Get-Volume` not `df`. There is no bash
      here, so POSIX commands will fail.
    - iOS terminal (iSH/a-Shell): a small POSIX userland — plain `sh`
      commands, no systemd, no package manager worth relying on.
    One command per action, and prefer the narrowest one that answers the
    question. Read-only commands run immediately; anything that could change
    the machine asks the user first, so do not try to avoid the prompt by
    chaining commands together.
    Never run a command the user did not ask for, and never one whose purpose
    you cannot state in a sentence."""

_BROWSER_HELP = """14. For WEB BROWSING use the 'browser' tool. Available actions:
    - browser open <url>              → open URL in the user's system browser
    - browser fetch <url>             → read page text (HTTP, works everywhere)
    - browser navigate <url>          → go to URL in the controlled session
    - browser click <selector|text>   → click element (CSS selector or visible text)
    - browser type <selector> <text>  → type text into a field
    - browser fill <selector> <value> → fill a form field
    - browser read                    → read current page text
    - browser scroll up|down          → scroll the page
    - browser screenshot [url]        → capture a screenshot
    - browser search <query>          → DuckDuckGo search and return results
    - browser close                   → close the controlled browser session
    Works on all platforms including Termux (requires `pkg install chromium` on Termux)."""


# System prompt for models without native tool support
def call_model(state: AgentState):
    _require_langchain_runtime()

    # Small talk never needs a tool. Answer with the short prompt and finish,
    # skipping tool discovery and the long rule list entirely.
    _last = ""
    for _m in reversed(state.get("messages") or []):
        if isinstance(_m, HumanMessage):
            _last = str(getattr(_m, "content", "") or "")
            break
    if _is_small_talk(_last):
        from langchain_core.messages import AIMessage as _AIMsg
        _llm = _build_llm()
        _resp = _invoke_spoken(_llm, [HumanMessage(content=_SMALL_TALK_PROMPT),
                                      HumanMessage(content=_last)])
        _text = _clean_agent_response(_resp.content)
        return {"messages": [_AIMsg(content=_text)], "next_step": "end"}

    # Re-discover tools to ensure we have the latest descriptions
    available_tools = discover_tools()
    available_tools = _shortlist_tools(_last, available_tools)

    # When offline, flag tools that need internet so the model doesn't try
    # them (and can tell the user why). Online, the list is left clean.
    offline = current_connectivity() == "offline"
    tool_lines = []
    offline_only = []
    for name in available_tools:
        desc = get_tool_description(name)
        if offline and tool_requires_internet(name):
            tool_lines.append(f"- {name}: {desc}  [UNAVAILABLE — needs internet]")
        else:
            tool_lines.append(f"- {name}: {desc}")
            if offline:
                offline_only.append(name)
    tool_descriptions = "\n".join(tool_lines)

    offline_notice = ""
    if offline:
        usable = ", ".join(offline_only) if offline_only else "none"
        offline_notice = f"""
NETWORK STATUS: You are currently OFFLINE.
Tools marked "[UNAVAILABLE — needs internet]" will fail right now — do NOT call them.
If the user asks for something that needs an unavailable tool, briefly explain it
requires an internet connection and offer to retry once they're back online.
Tools you CAN use offline: {usable}.
"""

    # Only spell out the browser sub-commands when the browser is actually on
    # offer — otherwise it is ~800 characters of prompt for an unlisted tool.
    browser_help = _BROWSER_HELP if "browser" in available_tools else ""

    # Same reasoning for `system`: only describe it when it is on offer, and
    # tell the model which machine it is on — the command for "how much disk is
    # left" is not the same on a Mac as it is in Termux, and a model guessing
    # the platform will guess wrong about half the time.
    system_help = ""
    if "system" in available_tools and _sysexec is not None:
        system_help = _SYSTEM_HELP.format(
            platform=_sysexec.platform_summary(),
            shell=_sysexec.shell_label(),
            state=("ENABLED" if _sysexec.enabled() else
                   "DISABLED — say so and stop; do not retry"),
        )

    system_prompt = f"""You are Pilot, a helpful AI assistant for the Gathm ecosystem.
You have access to the following gathm tools:
{tool_descriptions}
{offline_notice}
CRITICAL RULES:
0. CONVERSATIONAL RESPONSES: For greetings (hi, hello, hey, thanks), questions about yourself, or any message that does not require fetching data, respond in plain conversational text with NO Action/Thought format at all. Only use the Action format when you genuinely need to call one of the tools listed above.
0a. QUESTIONS ABOUT YOUR TOOLS ARE NOT TOOL CALLS. If the user asks what tools exist, what you can do, whether some other tool is available, or which tool to use, ANSWER IN TEXT from the list above. Never run a tool to answer a question about tools.
0b. NEVER call a tool without the arguments it needs. If a tool requires a target (a domain, a query, a file) and the user has not given one, ask for it instead of running the tool bare.
1. To use a tool, you MUST use the exact format:
Thought: [your reasoning]
Action: gathm
Action Input: [tool_name] [arguments]

2. For MATH (derivatives, integrals, etc.), use the 'newton' tool.
3. For company STOCKS (Apple, Google), use the 'stocks' tool.
4. For CRYPTO (Bitcoin, ETH), use the 'cryptocurrency' tool.
5. For anything you need from the INTERNET — who a person is, what something means, current events — use the 'websearch' tool.
6. For CURRENCY conversion, use exact order: currency [base] [target] [amount], e.g. currency USD EUR 100
7. For GIF searches, use a single keyword argument, e.g. gif dancing or gif funny_cats
8. You MUST remember conversation context for follow-ups (for example, if user asks "where is it compromised?" after an email breach check).
9. Never output "Action: <tool>" directly. Always use "Action: gathm" with "Action Input:".
10. Refuse requests that ask to find exposed/publicly accessible cameras, FTP servers, or similar reconnaissance targets.
11. If a tool fails, say in one sentence WHAT failed and quote the error text you were given, then add that the engineer has been notified. Never replace the error with a generic message — the user cannot fix what they cannot see.
12. ONLY use tool names from the list above. Never invent tool names like 'define', 'help', 'done', 'exit', etc.
{system_help}
{browser_help}
When you have a final answer, provide it directly without the Action format.
"""
    messages = [HumanMessage(content=system_prompt)] + state["messages"]
    llm = _build_llm()
    response = _invoke_spoken(llm, messages)

    # Check for tool call in the text
    content = response.content
    if extract_tool_input(content):
        return {
            "messages": [response],
            "next_step": "action"
        }

    # No valid tool call — clean ReAct scaffolding before returning the
    # final answer so the user sees only the actual response text.
    from langchain_core.messages import AIMessage as _AIMsg
    cleaned = _clean_agent_response(content)
    final = _AIMsg(content=cleaned) if cleaned != content else response
    return {
        "messages": [final],
        "next_step": "end"
    }

# Terminal art is for humans. `weather` alone returns ~3.5 KB of box-drawing and
# ANSI escapes, which a 1B model has to read as ~1000 tokens of noise before it
# can answer — and did not survive: it produced the canned failure line for a
# lookup that had actually worked. Observations are stripped and capped.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
_BOX_RE = re.compile(r"[\u2500-\u257f\u2580-\u259f]+")
OBS_MAX_CHARS = int(os.getenv("GATHM_OBS_MAX_CHARS", "1500"))


def _clean_observation(output: str) -> str:
    """Reduce a tool's terminal output to what a model can actually use."""
    text = _ANSI_RE.sub("", output or "")
    text = _BOX_RE.sub(" ", text)
    # Collapse the runs of spaces that the art leaves behind, and drop the blank
    # lines that come with it, without joining separate lines together.
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    if OBS_MAX_CHARS > 0 and len(text) > OBS_MAX_CHARS:
        text = (text[:OBS_MAX_CHARS].rstrip()
                + f"\n[... output truncated at {OBS_MAX_CHARS} characters ...]")
    return text


def _looks_like_tool_failure(output: str) -> bool:
    """Whether a tool's output is an error rather than an answer."""
    text = (output or "").strip()
    if not text or text == "(no output)":
        return True
    lowered = text.lower()
    return (lowered.startswith(("error:", "usage:")) or "[stderr]:" in lowered
            or "command not found" in lowered or "not installed" in lowered
            or '"error"' in lowered)


def tool_node(state: AgentState):
    last_message = state["messages"][-1]
    content = last_message.content
    
    # Extract tool input
    tool_input = extract_tool_input(content)
    if tool_input:
        try:
            normalized_input = normalize_tool_command(tool_input)
        except Exception:
            normalized_input = tool_input
        print_tool_exec(normalized_input)
        result = run_gathm_tool_raw(normalized_input)

        # Label a failure as a failure. Left as a bare "Observation:", a tool
        # error was answered with the canned engineer line and the actual
        # reason never reached the user — which is how a broken weather lookup
        # became "This issue will be taken care by our engineer".
        if _looks_like_tool_failure(result):
            return {"messages": [HumanMessage(content=(
                f"Observation: the tool FAILED. Its exact output was:\n"
                f"{_clean_observation(result)}\n"
                "Tell the user what failed and quote that error, then say the "
                "engineer has been notified. Do not try the same tool again."
            ))]}
        return {"messages": [HumanMessage(content=
                                          f"Observation: {_clean_observation(result)}")]}
    return {"messages": [HumanMessage(content="Error: Could not parse tool input.")]}

def should_continue(state: AgentState):
    return state.get("next_step", "end")

if LANGCHAIN_AVAILABLE:
    try:
        llm = _build_llm()
    except RuntimeError:
        llm = None

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("action", tool_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"action": "action", "end": END})
    workflow.add_edge("action", "agent")
    app = workflow.compile()
else:
    llm = None
    workflow = None
    app = None

def _voice_input(arg: str) -> Optional[str]:
    """/listen — record from the mic and return the transcript as the query.

    Deliberately not a tool: the agent needs the words before it can decide
    anything, so listening happens at the input edge, ahead of the graph.
    """
    try:
        from lib import speech
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n  [color(196)][x][/color(196)] speech unavailable: {exc}")
        return None

    seconds = None
    if arg:
        try:
            seconds = int(arg.split()[0])
        except ValueError:
            console.print(f"\n  [color(214)][!][/color(214)] /listen takes seconds, "
                          f"got '{arg}'")
            return None

    if not speech.asr_enabled():
        cfg = speech.resolve_asr()
        if not cfg["bin"]:
            console.print("\n  [color(214)][!][/color(214)] Speech runtime not "
                          "installed. Run ./install on Termux.")
        else:
            console.print("\n  [color(214)][!][/color(214)] No speech-to-text model "
                          "installed. Add it with:")
            console.print("      [dim]GATHM_AUDIOCPP_MODELS=pocket_tts,sense_asr "
                          "GATHM_AUDIOCPP_FORCE=1 ./install[/dim]")
        return None

    if speech.find_recorder() is None:
        console.print("\n  [color(214)][!][/color(214)] No way to record audio. On "
                      "Termux: [dim]pkg install termux-api[/dim] plus the "
                      "Termux:API app.")
        return None

    secs = seconds or int(os.getenv("GATHM_LISTEN_SECONDS", "8"))
    console.print(f"\n  [color(208)]🎤 Listening for {secs}s...[/color(208)]")
    ok, text = speech.listen(seconds)
    if not ok:
        console.print(f"  [color(196)][x][/color(196)] {text}")
        return None
    console.print(f"  [dim]heard:[/dim] {text}")
    return text


def _handle_speak_command(arg: str) -> None:
    """/speak — inspect, toggle, or test the audio.cpp voice.

    Speech is opt-out via GATHM_SPEAK, and on a phone it is worth being able to
    silence a long answer without restarting Pilot.
    """
    try:
        from lib import speech
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n  [color(196)][x][/color(196)] speech unavailable: {exc}")
        return

    arg_lower = arg.lower()

    if arg_lower in ("on", "enable"):
        os.environ["GATHM_SPEAK"] = "1"
        cfg = speech.resolve()
        if not cfg["bin"] or not cfg["model"]:
            console.print("\n  [color(214)][!][/color(214)] Speech on, but the voice "
                          "runtime is not installed. Run ./install on Termux.")
        else:
            console.print("\n  [color(40)][+][/color(40)] Speech on.")
        return

    if arg_lower in ("off", "disable", "mute"):
        os.environ["GATHM_SPEAK"] = "0"
        speech.stop()
        console.print("\n  [color(40)][+][/color(40)] Speech off.")
        return

    if arg:  # anything else is a phrase to try out loud
        console.print(f"\n  [dim]speaking:[/dim] {arg}")
        if not speech.speak(arg, quiet=False):
            console.print("  [color(214)][!][/color(214)] Nothing was played — "
                          "see /speak for the reason.")
        return

    cfg = speech.resolve()
    player = speech.find_player()
    console.print("")
    console.print(f"  [color(208)]Runtime:[/color(208)] {cfg['bin'] or 'not installed'}")
    console.print(f"  [color(208)]Voice:[/color(208)]   {cfg['voice']} ({cfg['family']})")
    console.print(f"  [color(208)]Model:[/color(208)]   {cfg['model'] or 'not configured'}")
    console.print(f"  [color(208)]Player:[/color(208)]  "
                  f"{' '.join(player) if player else 'none — pkg install mpv'}")
    console.print(f"  [color(208)]Enabled:[/color(208)] {speech.enabled()}")
    if not speech.enabled() or not player:
        console.print("  [dim]Fix what is missing above, then: /speak hello[/dim]")


def _handle_slash_command(cmd: str) -> bool:
    """Handle slash commands. Returns True if a command was handled."""
    cmd_lower = cmd.strip().lower()

    if cmd_lower in ("/help", "?"):
        render_help()
        return True

    if cmd_lower == "/tools":
        tools = discover_tools()
        tool_info = [(t, get_tool_description(t)) for t in tools]
        render_tools_list(tool_info)
        return True

    if cmd_lower == "/clear":
        print_tricolor_banner()
        return True

    if cmd_lower == "/model":
        console.print(f"\n  [color(208)]Backend:[/color(208)] {LLM_BACKEND.upper()}")
        console.print(f"  [color(208)]Model:[/color(208)]   {OLLAMA_MODEL}")
        return True

    if cmd_lower == "/speak" or cmd_lower.startswith("/speak "):
        _handle_speak_command(cmd.strip()[len("/speak"):].strip())
        return True

    if cmd_lower in ("/quit", "/exit"):
        return False  # signal to exit handled in main loop

    return False


def main():
    import signal

    # Graceful shutdown on SIGTERM (e.g. kill, docker stop)
    def _handle_sigterm(_sig, _frame):
        stop_waiting()
        render_goodbye()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Ignore SIGPIPE (broken pipe) to avoid crashes when piping output
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass  # SIGPIPE not available on Windows

    if app is None:
        _require_langchain_runtime()

    print_tricolor_banner()
    conversation_history: List[BaseMessage] = []

    # Guards against an error that recurs on every pass (see the handler below).
    last_loop_error = ""
    repeated_errors = 0

    while True:
        try:
            user_input = get_user_input()
            if not user_input:
                continue

            # The previous answer may still be being read aloud; synthesis of a
            # long reply outlives the turn that produced it. Whatever the user
            # types next takes precedence over hearing the rest of it.
            stop_speaking()

            # /listen is not a slash command like the others: it produces the
            # query rather than printing something, so the transcript falls
            # through into the normal turn below.
            if user_input.lower() == "/listen" or user_input.lower().startswith("/listen "):
                heard = _voice_input(user_input[len("/listen"):].strip())
                if not heard:
                    continue
                user_input = heard

            # ── Exit ──
            if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
                render_goodbye()
                break

            # ── Slash commands ──
            if user_input.startswith("/") or user_input == "?":
                _handle_slash_command(user_input)
                continue

            # ── Show user message in the chat log ──
            print_user_message(user_input)

            # ── Safety check ──
            risk_category = classify_high_risk_query(user_input)
            if risk_category:
                refusal = safety_refusal_message()
                render_response(refusal)
                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=refusal),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]
                continue

            # ── AI reasoning loop (with shimmer animation) ──
            state = {"messages": conversation_history + [HumanMessage(content=user_input)]}
            final_agent_reply: Optional[str] = None
            _stream_error = False
            # Speak the answer while the model is still writing it, so a voice
            # conversation does not wait out the whole generation in silence.
            _reply_speech = start_reply_stream()
            _set_token_sink(_ReplySpeech(_reply_speech) if _reply_speech else None)
            start_waiting()
            try:
                for output in app.stream(state, config={"recursion_limit": 25}):
                    for key, value in output.items():
                        if key == "agent" and value.get("next_step") == "end":
                            final_agent_reply = value["messages"][-1].content  # type: ignore[index]
            except KeyboardInterrupt:
                # Ctrl+C during AI processing — cancel the current query, not the app
                stop_waiting()
                console.print("\n  [color(208)]\\[*][/color(208)] Query cancelled.")
                continue
            except Exception as e:
                # The TUI used to print the raw exception, so a stopped Ollama
                # server read as "[Errno 111] Connection refused" with no hint
                # that `ollama serve` is the fix. Same describer as the API path.
                described = describe_agent_failure(e)
                report_to_engineer(str(e), user_input)
                _stream_error = True
                stop_waiting()
                render_error(described)
                final_agent_reply = described
            finally:
                stop_waiting()
                _sink = _TOKEN_SINK
                _set_token_sink(None)
                if _sink is not None:
                    _sink.close()

            if final_agent_reply:
                # Only render the response panel for successful AI replies
                # (error case already displayed render_error above)
                if not _stream_error:
                    # Already spoken token by token? Then don't say it twice.
                    render_response(
                        final_agent_reply,
                        speak=not (_sink is not None and _sink.fed),
                    )
                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=final_agent_reply),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]

        except (EOFError, KeyboardInterrupt):
            render_goodbye()
            break
        except Exception as e:
            # An error here may well come from reading input, and retrying that
            # immediately reproduces it — which is how one broken prompt turned
            # into hundreds of identical panels scrolling past. Repeat the same
            # failure twice and stop, with something the user can act on.
            render_error(str(e))
            last_loop_error, repeated_errors, give_up = track_loop_error(
                str(e), last_loop_error, repeated_errors)
            if give_up:
                console.print(
                    "\n  [color(208)]\\[!][/color(208)] The same error keeps "
                    "recurring, so Pilot is stopping rather than looping.\n"
                    f"      {describe_agent_failure(e)}\n"
                    "      Restart with:  gathm tui"
                )
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        render_goodbye()
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)
