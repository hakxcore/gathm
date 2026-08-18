import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Make the Gathm root importable when this file is launched directly:
# python3 pilot/termux.py
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_model():
    env_model = os.getenv("GATHM_OLLAMA_MODEL")
    if env_model:
        return env_model.strip()

    model_file = Path.home() / ".gathm" / "model"
    if model_file.is_file():
        model = model_file.read_text().strip()
        if model:
            return model

    return "gemma3:1b"


MODEL = get_model()


def tools():
    d = ROOT / "tools"

    if not d.is_dir():
        return set()

    result = set()

    for x in d.iterdir():
        if not x.is_dir():
            continue

        executable = x / x.name

        if executable.is_file() and os.access(executable, os.X_OK):
            result.add(x.name)

    return result


def run_tool(name, args):
    """
    Execute a Gathm tool directly.

    The result is returned directly to the user.
    We intentionally DO NOT send tool output back through Gemma 1B.
    """

    available = tools()

    if name not in available:
        return f"Error: Unknown tool '{name}'."

    tool_path = ROOT / "tools" / name / name

    try:
        result = subprocess.run(
            [str(tool_path), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout.strip()

        if result.returncode != 0:
            stderr = result.stderr.strip()

            if stderr:
                return f"{output}\n[stderr]: {stderr}".strip()

            return output or f"Tool '{name}' failed."

        return output or "(no output)"

    except subprocess.TimeoutExpired:
        return f"Tool '{name}' timed out."

    except Exception as exc:
        return f"Tool '{name}' error: {exc}"


def simple_response(user_input):
    """
    Handle messages that absolutely do not need an LLM.
    """

    text = user_input.strip().lower()

    greetings = {
        "hi",
        "hello",
        "hey",
        "hiya",
        "hii",
        "hiii",
        "good morning",
        "good afternoon",
        "good evening",
    }

    if text in greetings:
        return "Hi! How can I help you?"

    thanks = {
        "thanks",
        "thank you",
        "thx",
        "ty",
    }

    if text in thanks:
        return "You're welcome! How can I help you?"

    if text in {
        "who are you",
        "what are you",
        "what is gathm",
    }:
        return "I'm Gathm Pilot, your local AI assistant running on Termux."

    return None


def direct_tool(user_input):
    """
    Deterministic routing for obvious commands.

    Small models such as Gemma 3 1B should not be responsible
    for recognizing simple commands such as:

        weather Paris
        forecast London

    """

    text = user_input.strip()

    if not text:
        return None

    # weather Paris
    match = re.match(
        r"^(?:weather|forecast)\s+(.+?)\s*$",
        text,
        re.IGNORECASE,
    )

    if match:
        location = match.group(1).strip()
        return "weather", [location]

    return None


def ask_model(user_input):
    """
    Use Gemma 3 1B only for requests that genuinely need
    natural-language reasoning.
    """

    try:
        from lib.llm import LLMConfig, LLMProvider

        cfg = LLMConfig.from_env()
        provider = LLMProvider(cfg)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Gathm, a helpful local AI assistant running "
                    "on Android/Termux. Answer concisely and naturally. "
                    "Do not invent tools. Do not output TOOL:, Action:, "
                    "Thought:, or Observation:."
                ),
            },
            {
                "role": "user",
                "content": user_input,
            },
        ]

        answer = provider.complete(messages)

        if answer and answer.strip():
            return answer.strip()

        return "I didn't receive a response from the local AI model."

    except Exception as exc:
        return f"Local AI error: {exc}"


def main():
    available_tools = tools()

    print(f"Gathm Termux Pilot • Ollama • {MODEL}")
    print(f"Tools available: {len(available_tools)}")
    print("Type /exit to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()

        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not user_input:
            continue

        if user_input.lower() in {
            "/exit",
            "/quit",
            "exit",
            "quit",
        }:
            return

        # ---------------------------------------------------------
        # 1. Simple conversation — NO LLM
        # ---------------------------------------------------------

        simple = simple_response(user_input)

        if simple is not None:
            print(f"\nGathm: {simple}\n")
            continue

        # ---------------------------------------------------------
        # 2. Deterministic tool routing
        # ---------------------------------------------------------

        routed = direct_tool(user_input)

        if routed is not None:
            tool_name, args = routed

            print(f"\n▶ running: {tool_name} {' '.join(args)}")

            result = run_tool(tool_name, args)

            # IMPORTANT:
            # Return the tool result directly.
            # DO NOT send it through Gemma 1B.
            print(f"\nGathm:\n{result}\n")

            continue

        # ---------------------------------------------------------
        # 3. Everything else → Gemma 1B
        # ---------------------------------------------------------

        answer = ask_model(user_input)

        print(f"\nGathm: {answer}\n")


if __name__ == "__main__":
    main()
