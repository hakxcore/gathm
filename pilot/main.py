import os
import subprocess
import glob
from pathlib import Path
from langchain_community.llms import Ollama
from langchain.agents import create_react_agent, AgentExecutor
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"

# --- 1. Initialize Ollama LLM ---
llm = Ollama(model="gemma3:12b", temperature=0.7)


# --- 2. Discover available gathm tools ---
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
    """Extract the description from a tool's script or tool.yaml."""
    tool_path = TOOLS_DIR / tool_name / tool_name
    yaml_path = TOOLS_DIR / tool_name / "tool.yaml"

    # Try tool.yaml first
    if yaml_path.is_file():
        try:
            for line in yaml_path.read_text().splitlines():
                if line.strip().startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if desc:
                        return desc
        except Exception:
            pass

    # Fallback: grep for "# Description:" in the script
    if tool_path.is_file():
        try:
            for line in tool_path.read_text().splitlines()[:20]:
                if "Description:" in line:
                    return line.split("Description:", 1)[1].strip()
        except Exception:
            pass

    return f"Run the {tool_name} tool"


# --- 3. Define the gathm tool runner ---
def run_gathm_tool(command: str) -> str:
    """
    Runs a gathm tool directly. The command should be in the format:
    '<tool_name> [arguments]', e.g., 'weather London' or 'news'.
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return "Error: No tool specified. Use format: '<tool_name> [arguments]'"

    tool_name = parts[0]
    tool_args = parts[1] if len(parts) > 1 else ""
    tool_path = TOOLS_DIR / tool_name / tool_name

    if not tool_path.is_file():
        available = discover_tools()
        return (
            f"Error: Tool '{tool_name}' not found.\n"
            f"Available tools: {', '.join(available)}"
        )

    try:
        env = {**os.environ, "TERM": "xterm-256color"}
        # Source utils.bash and run the tool so it has access to shared functions
        shell_cmd = f'source "{GATHM_ROOT}/lib/utils.bash" && "{tool_path}" {tool_args}'
        result = subprocess.run(
            ["bash", "-c", shell_cmd],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Tool '{tool_name}' timed out after 30 seconds."
    except Exception as e:
        return f"Error running tool '{tool_name}': {e}"


# Build tool descriptions dynamically
available_tools = discover_tools()
tool_descriptions = "\n".join(
    f"- '{name}': {get_tool_description(name)}" for name in available_tools
)

gathm_tool = Tool(
    name="gathm",
    func=run_gathm_tool,
    description=f"""Execute gathm tools by providing a tool name and arguments.
Format: '<tool_name> [arguments]'

Available tools:
{tool_descriptions}

Examples:
- 'weather London' to get weather for London.
- 'news' to get latest news headlines.
- 'cryptocurrency' to check crypto prices.
- 'movie The Matrix' to look up a movie.
- 'define hello' to define a word.
- 'geo -w' to get your WAN IP address.
""",
)

tools = [gathm_tool]

# --- 4. Create the Agent ---
template = """Answer the following questions as best you can. You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

You are a helpful AI assistant named Pilot. You have access to a tool called 'gathm'
which gives you access to many utilities (weather, news, crypto, movies, etc.).
Always use the gathm tool to fetch real data before answering.
Your goal is to provide concise and helpful answers.

Begin!

Question: {input}
Thought:{agent_scratchpad}"""

prompt = PromptTemplate.from_template(template)

# Create the ReAct agent
agent = create_react_agent(llm, tools, prompt)
agent_executor = AgentExecutor(
    agent=agent, tools=tools, verbose=True, handle_parsing_errors=True
)


# --- 5. Interaction Loop ---
def main():
    print("Pilot AI Agent activated. Type 'exit' to quit.")
    print(f"Loaded {len(available_tools)} gathm tools: {', '.join(available_tools)}")
    print("Ask me anything, like 'What's the weather in Paris?' or 'Tell me about Bitcoin.'")
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() == "exit":
            break

        try:
            response = agent_executor.invoke({"input": user_input})
            print(f"Pilot: {response['output']}")
        except Exception as e:
            print(f"Pilot encountered an error: {e}")


if __name__ == "__main__":
    main()
