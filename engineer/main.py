import os
import sys
import asyncio
from pathlib import Path
from typing import List

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from autogen_agentchat.agents import AssistantAgent, CodeExecutorAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.ui import Console
    from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor
    from autogen_ext.models.anthropic import AnthropicChatCompletionClient
    AUTOGEN_AVAILABLE = True
except ImportError as e:
    print(f"Import error: {e}")
    AUTOGEN_AVAILABLE = False

ENGINEER_DIR = Path(__file__).resolve().parent
GATHM_ROOT = ENGINEER_DIR.parent

# --- Unified LLM provider (single source of truth for backend/model config) ---
sys.path.insert(0, str(GATHM_ROOT))
try:
    from lib.llm import LLMConfig, LLMProvider as _LLMProvider
    _llm_config = LLMConfig.from_env()
except Exception:
    _llm_config = None

def get_model_client():
    """Return an AutoGen model client via the unified LLM provider."""
    if _llm_config is not None:
        provider = _LLMProvider(_llm_config)
        print(f"[*] Engineer using {_llm_config.backend.upper()} — {_llm_config.model}")
        return provider.autogen_model_client()

    # Fallback if lib.llm failed to import
    from autogen_ext.models.openai import OpenAIChatCompletionClient  # type: ignore[import]
    model = os.getenv("GATHM_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "gemma3:12b"))
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    print(f"[*] Engineer using Ollama (fallback) — {model}")
    return OpenAIChatCompletionClient(
        model=model,
        base_url=base_url,
        api_key="NotRequired",
        model_info={"vision": False, "function_calling": True, "json_output": True,
                    "family": "unknown", "structured_output": False,
                    "multiple_system_messages": True},
    )

def _build_codebase_context() -> str:
    """Build a concise snapshot of the Gathm codebase for the Engineer's system prompt."""
    lines: List[str] = []

    lines.append("## Codebase Snapshot")
    lines.append(f"Root: {GATHM_ROOT}")
    lines.append("")

    # Top-level layout
    lines.append("### Top-level layout")
    skip = {"__pycache__", ".git", ".env", "node_modules", "media"}
    for item in sorted(GATHM_ROOT.iterdir()):
        if item.name.startswith(".") or item.name in skip:
            continue
        marker = "/" if item.is_dir() else ""
        lines.append(f"  {item.name}{marker}")

    # Library files
    lib_dir = GATHM_ROOT / "lib"
    if lib_dir.is_dir():
        lines.append("")
        lines.append("### lib/ (shared Bash libraries — source these in new tools)")
        for f in sorted(lib_dir.iterdir()):
            if f.suffix == ".bash":
                lines.append(f"  lib/{f.name}")

    # Agent scripts
    agent_dir = GATHM_ROOT / "agent"
    if agent_dir.is_dir():
        lines.append("")
        lines.append("### agent/ (orchestrator and planner)")
        for f in sorted(agent_dir.iterdir()):
            if f.is_file():
                lines.append(f"  agent/{f.name}")

    # Tools
    tools_dir = GATHM_ROOT / "tools"
    if tools_dir.is_dir():
        lines.append("")
        lines.append("### tools/ (each tool lives in tools/<name>/<name>)")
        for tool_dir in sorted(tools_dir.iterdir()):
            if not tool_dir.is_dir():
                continue
            desc = ""
            yaml_path = tool_dir / "tool.yaml"
            if yaml_path.is_file():
                for line in yaml_path.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith("description:"):
                        desc = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                        break
            lines.append(f"  {tool_dir.name}: {desc or '(no description)'}")

    # Test files
    tests_dir = GATHM_ROOT / "tests"
    if tests_dir.is_dir():
        lines.append("")
        lines.append("### tests/")
        for f in sorted(tests_dir.iterdir()):
            if f.is_file():
                lines.append(f"  tests/{f.name}")

    return "\n".join(lines)


async def run_engineer_task(task: str):
    print(f"[*] Gathm AutoGen Engineer starting task: {task}")

    model_client = get_model_client()
    codebase_context = _build_codebase_context()

    # 1. The Engineer Agent (Planner & Coder)
    engineer = AssistantAgent(
        name="Engineer",
        model_client=model_client,
        system_message=f'''You are the Gathm Framework Engineer, a senior AI software engineer specialized in framework maintenance.
Your goal is to keep the Gathm Framework robust, secure, and modern.

OPERATIONAL PROTOCOL:
1.  **Analyze First:** You already have a codebase snapshot below. Read individual files with `cat` before modifying them.
2.  **Surgical Updates:** Do not overwrite entire files unless absolutely necessary. Prefer precise modifications.
3.  **Preserve Logic:** Never remove core functionality, tricolor beautification, or existing imports unless specifically asked.
4.  **Use Libraries:** Leverage Gathm's shared libraries in `lib/` (utils.bash, schema.bash, etc.) for any new tool development.
5.  **Verify:** Always run the code or tests after making changes to ensure everything works as expected.
6.  **Communication:** Explain your reasoning clearly. When finished and verified, summarize and end with "TERMINATE".

The workspace root is: {GATHM_ROOT}
When Pilot faces issues, it depends on your expertise to fix the framework without introducing regressions.

---
{codebase_context}
---''',
    )

    # 2. The Code Executor Agent (Runner)
    executor = CodeExecutorAgent(
        name="Executor",
        code_executor=LocalCommandLineCodeExecutor(work_dir=str(GATHM_ROOT)),
    )

    # 3. Define termination condition
    termination = TextMentionTermination("TERMINATE")

    # 4. Create the team (Group Chat)
    team = RoundRobinGroupChat([engineer, executor], termination_condition=termination)

    # 5. Run the task
    await Console(team.run_stream(task=task))

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 engineer/main.py <task>")
        sys.exit(1)
        
    task = sys.argv[1]

    if not AUTOGEN_AVAILABLE:
        print("Error: Required libraries are not installed.")
        print("Run: pip install -r engineer/requirements.txt")
        sys.exit(1)

    asyncio.run(run_engineer_task(task))

if __name__ == "__main__":
    main()
