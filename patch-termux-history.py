from pathlib import Path

p = Path("pilot/main.py")
s = p.read_text()

old = '''                conversation_history.extend([
                    HumanMessage(content=user_input),
                    AIMessage(content=final_agent_reply),
                ])
                conversation_history = conversation_history[-PILOT_MAX_HISTORY:]
'''

new = '''                # Termux history is already updated by _run_termux_pilot().
                # Desktop/server history continues to use LangChain messages.
                if not termux_mode:
                    conversation_history.extend([
                        HumanMessage(content=user_input),
                        AIMessage(content=final_agent_reply),
                    ])
                    conversation_history = conversation_history[
                        -PILOT_MAX_HISTORY:
                    ]
'''

if old not in s:
    raise SystemExit("ERROR: conversation history block not found")

s = s.replace(old, new, 1)

p.write_text(s)
print("Step 5 applied: platform-specific conversation history fixed.")
