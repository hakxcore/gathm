from pathlib import Path

p = Path("pilot/main.py")
s = p.read_text()

marker = "def _handle_slash_command(cmd: str) -> bool:"

if marker not in s:
    raise SystemExit("ERROR: Could not find _handle_slash_command()")

new_code = r'''
def _is_termux() -> bool:
    """Return True when Pilot is running inside Termux/Android."""
    return shutil.which("termux-setup-storage") is not None


def _termux_system_prompt() -> str:
    """Build the system prompt for the lightweight Termux Pilot runtime."""
    available_tools = discover_tools()

    offline = current_connectivity() == "offline"
    tool_lines = []
    offline_only = []

    for name in available_tools:
        desc = get_tool_description(name)

        if offline and tool_requires_internet(name):
            tool_lines.append(
                f"- {name}: {desc}  [UNAVAILABLE — needs internet]"
            )
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

    return f"""You are Pilot, a helpful AI assistant for the Gathm ecosystem.

You have access to the following gathm tools:
{tool_descriptions}

{offline_notice}

CRITICAL RULES:

0. CONVERSATIONAL RESPONSES:
For greetings (hi, hello, hey, thanks), questions about yourself, or any message
that does not require fetching data, respond in plain conversational text with
NO Action/Thought format.

1. To use a tool, use exactly:
Thought: [brief reasoning]
Action: gathm
Action Input: [tool_name] [arguments]

2. For MATH (derivatives, integrals, etc.), use the "newton" tool.

3. For company STOCKS (Apple, Google), use the "stocks" tool.

4. For CRYPTO (Bitcoin, ETH), use the "cryptocurrency" tool.

5. If a tool fails, explain the failure and reassure the user that the Engineer
is on the way.

6. For CURRENCY conversion, use:
currency [base] [target] [amount]

7. For GIF searches, use a single keyword argument.

8. Remember conversation context for follow-up questions.

9. Never output "Action: <tool>" directly.
Always use:
Action: gathm
Action Input: ...

10. Refuse requests asking to find exposed/publicly accessible cameras,
FTP servers, or similar reconnaissance targets.

11. If a tool-related error occurs, tell the user:
"This issue will be taken care by our engineer, don't worry it will be resolve shortly."

12. ONLY use tools from the provided tool list.
Never invent tools.

13. For WEB BROWSING use the "browser" tool when it is available.

When you have a final answer, provide it directly without Action format.
"""


def _termux_complete(
    conversation_history: list[dict[str, str]],
    user_input: str,
) -> str:
    """
    Lightweight Pilot execution path for Termux.

    This deliberately avoids LangChain/LangGraph/Pydantic and talks directly
    to the unified LLMProvider. Desktop/server LangGraph remains unchanged.
    """
    cfg = LLMConfig.from_env()
    provider = LLMProvider(cfg)

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _termux_system_prompt(),
        }
    ]

    messages.extend(conversation_history)
    messages.append({
        "role": "user",
        "content": user_input,
    })

    max_turns = 10

    for _ in range(max_turns):
        response = provider.complete(messages)

        if not response:
            return "I didn't receive a response from the local AI model."

        tool_input = extract_tool_input(
            response,
            set(discover_tools()),
        )

        if not tool_input:
            return _clean_agent_response(response)

        try:
            normalized_input = normalize_tool_command(tool_input)
        except Exception:
            normalized_input = tool_input

        print_tool_exec(normalized_input)

        result = run_gathm_tool_raw(normalized_input)

        messages.append({
            "role": "assistant",
            "content": response,
        })

        messages.append({
            "role": "user",
            "content": f"Observation: {result}",
        })

    return (
        "I reached the maximum number of tool steps for this request. "
        "Please try the question again."
    )


def _run_termux_pilot(
    conversation_history: list[dict[str, str]],
    user_input: str,
) -> tuple[str, list[dict[str, str]]]:
    """Execute one Termux Pilot request."""
    try:
        reply = _termux_complete(
            conversation_history,
            user_input,
        )

        updated = list(conversation_history)
        updated.append({
            "role": "user",
            "content": user_input,
        })
        updated.append({
            "role": "assistant",
            "content": reply,
        })

        updated = updated[-(PILOT_MAX_HISTORY * 2):]

        return reply, updated

    except KeyboardInterrupt:
        raise

    except Exception as exc:
        report_to_engineer(str(exc), user_input)
        raise


'''

s = s.replace(
    marker,
    new_code + "\n" + marker,
    1,
)

p.write_text(s)
print("Termux lightweight Pilot engine added.")
