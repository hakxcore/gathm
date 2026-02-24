import os
import subprocess
from pathlib import Path
from typing import Annotated, List, TypedDict, Union

from dotenv import load_dotenv
from langchain_community.llms import Ollama
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"

# --- 1. Tool Discovery & Definitions ---

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
                    desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if desc: return desc
        except Exception: pass

    if tool_path.is_file():
        try:
            for line in tool_path.read_text().splitlines()[:20]:
                if "Description:" in line:
                    return line.split("Description:", 1)[1].strip()
        except Exception: pass

    return f"Run the {tool_name} tool"

# --- 2. Gathm Tool Execution Logic ---

def run_gathm_tool_raw(command: str) -> str:
    """The core execution engine for gathm tools."""
    parts = command.strip().split(None, 1)
    if not parts:
        return "Error: No tool specified."

    tool_name = parts[0]
    tool_args = parts[1] if len(parts) > 1 else ""
    tool_path = TOOLS_DIR / tool_name / tool_name

    if not tool_path.is_file():
        return f"Error: Tool '{tool_name}' not found."

    try:
        env = {**os.environ, "TERM": "xterm-256color"}
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
        return f"Error: {e}"

# --- 3. LangGraph Setup ---

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]

# Define the dynamic tool for LangChain
@tool
def gathm(command: str):
    """
    Execute gathm tools by providing a tool name and arguments.
    Format: '<tool_name> [arguments]'
    Example: 'weather London' or 'news'
    """
    return run_gathm_tool_raw(command)

# Update the tool docstring with discovered tools
available_tools = discover_tools()
tool_list_str = "\n".join([f"- {t}: {get_tool_description(t)}" for t in available_tools])
gathm.description += f"\n\nAvailable tools:\n{tool_list_str}"

tools = [gathm]
tool_node = ToolNode(tools)

# Initialize LLM
llm = Ollama(model="gemma3:12b", temperature=0.7)
llm_with_tools = llm.bind_tools(tools)

# Define Logic Nodes
def call_model(state: AgentState):
    messages = state['messages']
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are Pilot, a helpful AI assistant for the Gathm ecosystem. "
                   "You have access to a variety of tools via the 'gathm' function. "
                   "Always use the tools to provide accurate, real-time data."),
        MessagesPlaceholder(variable_name="messages"),
    ])
    chain = prompt | llm_with_tools
    response = chain.invoke(messages)
    return {"messages": [response]}

def should_continue(state: AgentState):
    messages = state['messages']
    last_message = messages[-1]
    if last_message.tool_calls:
        return "continue"
    return "end"

# Build Graph
workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("action", tool_node)

workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "action",
        "end": END
    }
)
workflow.add_edge("action", "agent")

app = workflow.compile()

# --- 4. Main Loop ---

def main():
    print("Pilot AI Agent (LangGraph Edition) activated. Type 'exit' to quit.")
    print(f"Loaded {len(available_tools)} gathm tools.")
    
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ["exit", "quit"]:
                break

            inputs = {"messages": [HumanMessage(content=user_input)]}
            # Stream the output for better visibility
            for output in app.stream(inputs):
                for key, value in output.items():
                    if key == "agent":
                        msg = value["messages"][-1]
                        if not msg.tool_calls:
                            print(f"Pilot: {msg.content}")
                    elif key == "action":
                        print(f"[*] Executing tool...")
        except EOFError:
            break
        except Exception as e:
            print(f"Pilot encountered an error: {e}")

if __name__ == "__main__":
    main()
