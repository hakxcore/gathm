import os
import subprocess
import re
from pathlib import Path
from typing import Annotated, List, TypedDict, Union

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END

# Load environment variables
load_dotenv()

# --- Resolve gathm root directory ---
PILOT_DIR = Path(__file__).resolve().parent
GATHM_ROOT = PILOT_DIR.parent
TOOLS_DIR = GATHM_ROOT / "tools"

# --- 1. Tool Discovery & Execution ---

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

def run_gathm_tool_raw(command: str) -> str:
    parts = command.strip().split(None, 1)
    if not parts: return "Error: No tool specified."
    tool_name = parts[0]
    tool_args = parts[1] if len(parts) > 1 else ""
    tool_path = TOOLS_DIR / tool_name / tool_name
    if not tool_path.is_file(): return f"Error: Tool '{tool_name}' not found."
    try:
        env = {**os.environ, "TERM": "xterm-256color", "GATHM_NON_INTERACTIVE": "1"}
        shell_cmd = f'source "{GATHM_ROOT}/lib/utils.bash" && "{tool_path}" {tool_args}'
        result = subprocess.run(["bash", "-c", shell_cmd], capture_output=True, text=True, timeout=30, env=env)
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        return output if output else "(no output)"
    except Exception as e: return f"Error: {e}"

# --- 2. LangGraph Stateful Reasoning (Text-based Tool Calling) ---

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    next_step: str

# System prompt for models without native tool support
available_tools = discover_tools()
tool_descriptions = "\n".join([f"- {name}: {get_tool_description(name)}" for name in available_tools])

SYSTEM_PROMPT = f"""You are Pilot, a helpful AI assistant for the Gathm ecosystem.
You have access to the following gathm tools:
{tool_descriptions}

To use a tool, you MUST use the following format:
Thought: [your reasoning]
Action: gathm
Action Input: [tool_name] [arguments]

The 'Action Input' must be a valid gathm tool command.
Example:
Thought: I need to check the weather in London.
Action: gathm
Action Input: weather London

When you have a final answer, provide it directly without the Action format.
Always use tools to fetch real data before answering questions about weather, news, crypto, etc.
"""

llm = ChatOllama(model="gemma3:12b", temperature=0)

def call_model(state: AgentState):
    messages = [HumanMessage(content=SYSTEM_PROMPT)] + state['messages']
    response = llm.invoke(messages)
    
    # Check for tool call in the text
    content = response.content
    if "Action: gathm" in content and "Action Input:" in content:
        return {
            "messages": [response],
            "next_step": "action"
        }
    return {
        "messages": [response],
        "next_step": "end"
    }

def tool_node(state: AgentState):
    last_message = state['messages'][-1]
    content = last_message.content
    
    # Extract tool input
    match = re.search(r"Action Input:\s*(.*)", content)
    if match:
        tool_input = match.group(1).strip()
        print(f"[*] Executing gathm tool: {tool_input}")
        result = run_gathm_tool_raw(tool_input)
        return {"messages": [HumanMessage(content=f"Observation: {result}")]}
    return {"messages": [HumanMessage(content="Error: Could not parse tool input.")]}

def should_continue(state: AgentState):
    return state.get("next_step", "end")

# Build Graph
workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("action", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"action": "action", "end": END})
workflow.add_edge("action", "agent")
app = workflow.compile()

def main():
    print("Pilot AI Agent (LangGraph/Manual) activated. Type 'exit' to quit.")
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ["exit", "quit"]: break
            
            # Reset state for each new query to prevent infinite loops in this simple version
            state = {"messages": [HumanMessage(content=user_input)]}
            for output in app.stream(state, config={"recursion_limit": 10}):
                for key, value in output.items():
                    if key == "agent" and value.get("next_step") == "end":
                        print(f"Pilot: {value['messages'][-1].content}")
        except EOFError: break
        except Exception as e: print(f"Pilot encountered an error: {e}")

if __name__ == "__main__":
    main()
