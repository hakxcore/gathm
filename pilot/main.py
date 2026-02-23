import os
import subprocess
from langchain_community.llms import Ollama
from langchain_classic.agents import create_react_agent, AgentExecutor
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- 1. Initialize Ollama LLM ---
llm = Ollama(model="gemma3:12b", temperature=0.7)

# --- 2. Define the gathm tool ---
def run_gathm_command(command: str) -> str:
    """
    Runs a gathm command and returns its output.
    The command should be a valid gathm command, e.g., "weather London" or "news".
    """
    try:
        result = subprocess.run(
            ["gathm", *command.split()],
            capture_output=True,
            text=True,
            check=True,
            env={"TERM": "xterm-256color", **os.environ}
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error executing gathm command: {e.stderr}"
    except FileNotFoundError:
        return "Error: 'gathm' command not found. Is Gathm installed and in your PATH?"

gathm_tool = Tool(
    name="gathm",
    func=run_gathm_command,
    description="""A command-line tool for various utilities like weather, news, crypto, etc.
Use it by providing a sub-command and arguments, e.g., "weather London".
Example usage:
- 'weather <city>' to get weather information.
- 'news' to get recent news.
- 'cryptocurrency' to get cryptocurrency info.
- 'movie <movie_name>' to get movie information.
- 'lyrics -a <artist> -s <song>' to get song lyrics.
""",
)

tools = [gathm_tool]

# --- 3. Create the Agent ---
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

You are a helpful AI assistant named Pilot. You have access to a tool called 'gathm'.
Your goal is to provide concise and helpful answers.

Begin!

Question: {input}
Thought:{agent_scratchpad}"""

prompt = PromptTemplate.from_template(template)

# Create the ReAct agent
agent = create_react_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

# --- 4. Interaction Loop ---
def main():
    print("Pilot AI Agent activated. Type 'exit' to quit.")
    print("Ask me anything, like 'What's the weather in Paris?' or 'Tell me about Bitcoin.'")
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() == 'exit':
            break
        
        try:
            response = agent_executor.invoke({"input": user_input})
            print(f"Pilot: {response['output']}")
        except Exception as e:
            print(f"Pilot encountered an error: {e}")

if __name__ == "__main__":
    main()
