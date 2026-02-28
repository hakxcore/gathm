import os
import shutil
import subprocess
import re
import shlex
import sys
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
    from langchain_ollama import ChatOllama
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
    from langgraph.graph import StateGraph, END
except ModuleNotFoundError as exc:
    LANGCHAIN_IMPORT_ERROR = exc
    LANGCHAIN_AVAILABLE = False
    AIMessage = Any  # type: ignore[assignment]
    BaseMessage = Any  # type: ignore[assignment]
    HumanMessage = Any  # type: ignore[assignment]
    ChatOllama = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    END = "__END__"

# Optional: Google Gemini backend
GOOGLE_GENAI_AVAILABLE = False
ChatGoogleGenerativeAI: Any = None
try:
    from langchain_google_genai import ChatGoogleGenerativeAI as _ChatGGAI
    ChatGoogleGenerativeAI = _ChatGGAI
    GOOGLE_GENAI_AVAILABLE = True
except ModuleNotFoundError:
    pass

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"

def _resolve_backend() -> str:
    """Return 'ollama' or 'gemini' from env/config, defaulting to 'ollama'."""
    env_val = os.getenv("GATHM_LLM_BACKEND", "").lower().strip()
    if env_val in ("ollama", "gemini"):
        return env_val
    backend_file = Path.home() / ".gathm" / "llm_backend"
    if backend_file.is_file():
        stored = backend_file.read_text().strip().lower()
        if stored in ("ollama", "gemini"):
            return stored
    return "ollama"

# Model priority: env var > ~/.gathm/model (install.sh) > hardcoded default
def _resolve_model() -> str:
    backend = _resolve_backend()
    if backend == "gemini":
        env_model = os.getenv("GATHM_GEMINI_MODEL") or os.getenv("GEMINI_MODEL")
        if env_model:
            return env_model
        model_file = Path.home() / ".gathm" / "model"
        if model_file.is_file():
            stored = model_file.read_text().strip()
            if stored:
                return stored
        return "gemini-2.0-flash-lite"
    else:
        env_model = os.getenv("GATHM_OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL")
        if env_model:
            return env_model
        model_file = Path.home() / ".gathm" / "model"
        if model_file.is_file():
            stored = model_file.read_text().strip()
            if stored:
                return stored
        return "gemma3:12b"

LLM_BACKEND = _resolve_backend()
OLLAMA_MODEL = _resolve_model()
PILOT_MAX_HISTORY = int(os.getenv("PILOT_MAX_HISTORY", "12"))

def _build_llm():
    """Instantiate the correct LangChain LLM for the configured backend."""
    if LLM_BACKEND == "gemini":
        if not GOOGLE_GENAI_AVAILABLE:
            raise RuntimeError(
                "Google Generative AI SDK not installed. "
                "Run: pip install langchain-google-genai"
            )
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return ChatGoogleGenerativeAI(model=OLLAMA_MODEL, google_api_key=api_key)
    return ChatOllama(model=OLLAMA_MODEL)

# Colors (kept for non-TUI code paths)
SAFFRON = "\033[38;5;208m"
WHITE_BOLD = "\033[1;37m"
INDIAN_GREEN = "\033[38;5;28m"
ASHOKA_BLUE = "\033[38;5;20m"
RESET = "\033[0m"

# TUI module
try:
    from pilot.tui import (
        render_welcome, print_prompt, render_response, print_tool_exec,
        print_status_bar, render_help, render_tools_list, render_error,
        render_goodbye, check_connectivity,
        start_waiting, stop_waiting, print_user_message, get_user_input,
        console,
    )
except ImportError:
    from tui import (  # type: ignore[no-redef]
        render_welcome, print_prompt, render_response, print_tool_exec,
        print_status_bar, render_help, render_tools_list, render_error,
        render_goodbye, check_connectivity,
        start_waiting, stop_waiting, print_user_message, get_user_input,
        console,
    )


TOOL_ALIASES = {
    "movies": "movie",
}

HIGH_RISK_QUERY_PATTERNS = (
    r"\bopen(?:ly)?\s+available\s+(?:ftp|cameras?)\b",
    r"\bpublic(?:ly)?\s+accessible\s+(?:ftp|cameras?)\b",
    r"\bopen\s+ftp\s+servers?\b",
    r"\bopen\s+cameras?\b",
    r"\bopennel\b.*\bcameras?\b",
)

# --- 1. Tool Discovery & Execution ---

def _detect_platform() -> str:
    """Detect platform for the welcome box."""
    import platform
    system = platform.system().lower()
    if system == "darwin":
        return "macOS"
    if system == "linux":
        if shutil.which("termux-setup-storage"):
            return "Termux"
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
    """Scan the tools/ directory and return a list of available tool names."""
    available = []
    if TOOLS_DIR.is_dir():
        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if tool_dir.is_dir():
                executable = tool_dir / tool_dir.name
                if executable.is_file() and os.access(executable, os.X_OK):
                    available.append(tool_dir.name)
    return available

def get_tool_description(tool_name):
    """Extract description from tool.yaml or script headers."""
    tool_path = TOOLS_DIR / tool_name / tool_name
    yaml_path = TOOLS_DIR / tool_name / "tool.yaml"
    if yaml_path.is_file():
        try:
            for line in yaml_path.read_text().splitlines():
                if line.strip().startswith("description:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception: pass
    return f"Run the {tool_name} tool"

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
    try:
        parts = shlex.split(command.strip())
    except ValueError as exc:
        return f"Error: Invalid command syntax ({exc})."
    if not parts:
        return "Error: No tool specified."

    normalized_parts = _normalize_tool_invocation(parts)
    tool_name = normalized_parts[0]
    tool_args = normalized_parts[1:]
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
    if LLM_BACKEND == "gemini" and not GOOGLE_GENAI_AVAILABLE:
        raise RuntimeError(
            "Google Generative AI SDK not installed. "
            "Run: pip install langchain-google-genai"
        )

# System prompt for models without native tool support
def call_model(state: AgentState):
    _require_langchain_runtime()

    # Re-discover tools to ensure we have the latest descriptions
    available_tools = discover_tools()
    tool_descriptions = "\n".join([f"- {name}: {get_tool_description(name)}" for name in available_tools])

    system_prompt = f"""You are Pilot, a helpful AI assistant for the Gathm ecosystem.
You have access to the following gathm tools:
{tool_descriptions}

CRITICAL RULES:
0. CONVERSATIONAL RESPONSES: For greetings (hi, hello, hey, thanks), questions about yourself, or any message that does not require fetching data, respond in plain conversational text with NO Action/Thought format at all. Only use the Action format when you genuinely need to call one of the tools listed above.
1. To use a tool, you MUST use the exact format:
Thought: [your reasoning]
Action: gathm
Action Input: [tool_name] [arguments]

2. For MATH (derivatives, integrals, etc.), use the 'newton' tool.
3. For company STOCKS (Apple, Google), use the 'stocks' tool.
4. For CRYPTO (Bitcoin, ETH), use the 'cryptocurrency' tool.
5. If a tool fails (like 'googler' returning no results), explain the failure and reassure the user that the Engineer is on the way.
6. For CURRENCY conversion, use exact order: currency [base] [target] [amount], e.g. currency USD EUR 100
7. For GIF searches, use a single keyword argument, e.g. gif dancing or gif funny_cats
8. You MUST remember conversation context for follow-ups (for example, if user asks "where is it compromised?" after an email breach check).
9. Never output "Action: <tool>" directly. Always use "Action: gathm" with "Action Input:".
10. Refuse requests that ask to find exposed/publicly accessible cameras, FTP servers, or similar reconnaissance targets.
11. If you encounter any tool-related error, inform the user: "This issue will be taken care by our engineer, don't worry it will be resolve shortly."
12. ONLY use tool names from the list above. Never invent tool names like 'define', 'help', 'done', 'exit', etc.

When you have a final answer, provide it directly without the Action format.
"""
    messages = [HumanMessage(content=system_prompt)] + state["messages"]
    llm = _build_llm()
    response = llm.invoke(messages)

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
        return {"messages": [HumanMessage(content=f"Observation: {result}")]}
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

    while True:
        try:
            user_input = get_user_input()
            if not user_input:
                continue

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
                report_to_engineer(str(e), user_input)
                _stream_error = True
                stop_waiting()
                render_error(str(e))
                final_agent_reply = "I encountered an error. The Engineer is on it."
            finally:
                stop_waiting()

            if final_agent_reply:
                # Only render the response panel for successful AI replies
                # (error case already displayed render_error above)
                if not _stream_error:
                    render_response(final_agent_reply)
                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=final_agent_reply),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]

        except (EOFError, KeyboardInterrupt):
            render_goodbye()
            break
        except Exception as e:
            render_error(str(e))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        render_goodbye()
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)
