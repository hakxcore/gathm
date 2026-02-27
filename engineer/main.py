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

# --- Exclusive Claude Configuration ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OLLAMA_MODEL = os.getenv("GATHM_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "gemma3:12b"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

def get_model_client():
    """Smart model selection: Claude when API key is available, Ollama as fallback."""
    if ANTHROPIC_API_KEY:
        try:
            from autogen_ext.models.anthropic import AnthropicChatCompletionClient
            print("[*] Engineer using Claude API (Anthropic)")
            return AnthropicChatCompletionClient(
                model="claude-sonnet-4-6",
                api_key=ANTHROPIC_API_KEY,
            )
        except Exception as e:
            print(f"[!] Claude init failed ({e}), falling back to Ollama")

    from autogen_ext.models.openai import OpenAIChatCompletionClient
    print(f"[*] Engineer using local model ({OLLAMA_MODEL}) via Ollama")
    return OpenAIChatCompletionClient(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        api_key="NotRequired",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "family": "unknown",
            "structured_output": False,
            "multiple_system_messages": True,
        }
    )

async def run_engineer_task(task: str):
    print(f"[*] Gathm AutoGen Engineer starting task: {task}")

    model_client = get_model_client()

    # 1. The Engineer Agent (Planner & Coder)
    engineer = AssistantAgent(
        name="Engineer",
        model_client=model_client,
        system_message='''You are the Gathm Framework Engineer, a senior AI software engineer specialized in framework maintenance.
Your goal is to keep the Gathm Framework robust, secure, and modern.

OPERATIONAL PROTOCOL:
1.  **Analyze First:** Always read the existing files fully before suggesting or making any changes. Use `ls` and `cat` to understand the structure.
2.  **Surgical Updates:** Do not overwrite entire files unless absolutely necessary. Prefer precise modifications.
3.  **Preserve Logic:** Never remove core functionality, tricolor beautification, or existing imports unless specifically asked.
4.  **Use Libraries:** Leverage Gathm's shared libraries in `lib/` (utils.bash, schema.bash, etc.) for any new tool development.
5.  **Verify:** Always run the code or tests after making changes to ensure everything works as expected.
6.  **Communication:** Explain your reasoning clearly. When finished and verified, summarize and end with "TERMINATE".

The workspace root is the current directory. When Pilot faces issues, it depends on your expertise to fix the framework without introducing regressions.''',
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
