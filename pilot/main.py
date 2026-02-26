import os
import shutil
import subprocess
import re
import shlex
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

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"
# Model priority: env var > ~/.gathm/model (install.sh) > hardcoded default
def _resolve_model() -> str:
    env_model = os.getenv("GATHM_OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL")
    if env_model:
        return env_model
    model_file = Path.home() / ".gathm" / "model"
    if model_file.is_file():
        stored = model_file.read_text().strip()
        if stored:
            return stored
    return "gemma3:12b"

OLLAMA_MODEL = _resolve_model()
PILOT_MAX_HISTORY = int(os.getenv("PILOT_MAX_HISTORY", "12"))

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
    )
except ImportError:
    from tui import (  # type: ignore[no-redef]
        render_welcome, print_prompt, render_response, print_tool_exec,
        print_status_bar, render_help, render_tools_list, render_error,
        render_goodbye, check_connectivity,
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
    model_label = OLLAMA_MODEL
    connectivity = check_connectivity()
    os.system("clear" if os.name != "nt" else "cls")
    print(render_welcome(model_label, tool_count, plat, connectivity=connectivity))
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

    action_input_match = re.search(r"Action Input:\s*(.+)", content, re.IGNORECASE)
    if action_input_match:
        action_input = action_input_match.group(1).strip()
        if action_input and "tool_name" not in action_input.lower():
            # Final check to ignore placeholders
            if any(x in action_input.lower() for x in ["[tool_name]", "[arguments]", "<arg>"]):
                return None
            return action_input

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
    known_tools = available_tools or set(discover_tools())
    
    # Check if it is a real tool and not a placeholder
    if tool_name in known_tools:
        # Ignore placeholders often used in explanations
        if any(x in payload.lower() for x in ["[tool_name]", "[arguments]", "<arg>"]):
            return None
            
        payload_parts[0] = tool_name
        return shlex.join(payload_parts)

    return None

# --- 2. LangGraph Stateful Reasoning (Text-based Tool Calling) ---

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    next_step: str

def _require_langchain_runtime() -> None:
    if LANGCHAIN_AVAILABLE:
        return
    detail = f": {LANGCHAIN_IMPORT_ERROR}" if LANGCHAIN_IMPORT_ERROR else ""
    raise RuntimeError(
        "Pilot AI runtime dependencies are missing. "
        "Install pilot/requirements.txt and retry" + detail
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

When you have a final answer, provide it directly without the Action format.
"""
    messages = [HumanMessage(content=system_prompt)] + state["messages"]
    llm = ChatOllama(model=OLLAMA_MODEL)
    response = llm.invoke(messages)
    
    # Check for tool call in the text
    content = response.content
    if extract_tool_input(content):
        return {
            "messages": [response],
            "next_step": "action"
        }
    return {
        "messages": [response],
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
    llm = ChatOllama(model=OLLAMA_MODEL)

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
        print(render_help())
        return True

    if cmd_lower == "/tools":
        tools = discover_tools()
        tool_info = [(t, get_tool_description(t)) for t in tools]
        print(render_tools_list(tool_info))
        return True

    if cmd_lower == "/clear":
        print_tricolor_banner()
        return True

    if cmd_lower == "/model":
        print(f"\n  {SAFFRON}Model:{RESET} {OLLAMA_MODEL}")
        return True

    if cmd_lower in ("/quit", "/exit"):
        return False  # signal to exit handled in main loop

    return False


def main():
    import signal

    # Graceful shutdown on SIGTERM (e.g. kill, docker stop)
    def _handle_sigterm(_sig, _frame):
        print(render_goodbye())
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
            print_prompt()
            user_input = input().strip()
            if not user_input:
                continue

            # ── Exit ──
            if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
                print(render_goodbye())
                break

            # ── Slash commands ──
            if user_input.startswith("/") or user_input == "?":
                _handle_slash_command(user_input)
                continue

            # ── Safety check ──
            risk_category = classify_high_risk_query(user_input)
            if risk_category:
                refusal = safety_refusal_message()
                print(render_response(refusal))
                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=refusal),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]
                continue

            # ── AI reasoning loop ──
            state = {"messages": conversation_history + [HumanMessage(content=user_input)]}
            final_agent_reply: Optional[str] = None
            try:
                for output in app.stream(state, config={"recursion_limit": 25}):
                    for key, value in output.items():
                        if key == "agent" and value.get("next_step") == "end":
                            final_agent_reply = value["messages"][-1].content  # type: ignore[index]
                            print(render_response(final_agent_reply))
            except KeyboardInterrupt:
                # Ctrl+C during AI processing — cancel the current query, not the app
                print(f"\n  {SAFFRON}[*]{RESET} Query cancelled.")
                continue
            except Exception as e:
                report_to_engineer(str(e), user_input)
                print(render_error(str(e)))
                final_agent_reply = "I encountered an error. The Engineer is on it."

            if final_agent_reply:
                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=final_agent_reply),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]

        except (EOFError, KeyboardInterrupt):
            print(render_goodbye())
            break
        except Exception as e:
            print(render_error(str(e)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(render_goodbye())
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)
