from pathlib import Path

p = Path("pilot/main.py")
s = p.read_text()

old = '''            # ── AI reasoning loop (with shimmer animation) ──
            state = {"messages": conversation_history + [HumanMessage(content=user_input)]}
            final_agent_reply: Optional[str] = None
            _stream_error = False
            start_waiting()
            try:
                for output in app.stream(state, config={"recursion_limit": 25}):
                    for key, value in output.items():
                        if key == "agent" and value.get("next_step") == "end":
                            final_agent_reply = value["messages"][-1].content  # type: ignore[index]
            except KeyboardInterrupt:
                # Ctrl+C during AI processing — cancel the current query, not the app
                stop_waiting()
                console.print("\\n  [color(208)]\\\\[*][/color(208)] Query cancelled.")
                continue
            except Exception as e:
                report_to_engineer(str(e), user_input)
                _stream_error = True
                stop_waiting()
                render_error(str(e))
                final_agent_reply = "I encountered an error. The Engineer is on it."
            finally:
                stop_waiting()
'''

new = '''            # ── AI reasoning loop ─────────────────────────────────────
            # Termux uses the lightweight direct LLMProvider path.
            # Desktop/server platforms keep the existing LangGraph path.
            final_agent_reply: Optional[str] = None
            _stream_error = False
            start_waiting()

            try:
                if termux_mode:
                    final_agent_reply, conversation_history = _run_termux_pilot(
                        conversation_history,
                        user_input,
                    )
                else:
                    state = {
                        "messages": conversation_history + [
                            HumanMessage(content=user_input)
                        ]
                    }

                    for output in app.stream(
                        state,
                        config={"recursion_limit": 25},
                    ):
                        for key, value in output.items():
                            if (
                                key == "agent"
                                and value.get("next_step") == "end"
                            ):
                                final_agent_reply = (
                                    value["messages"][-1].content
                                )

            except KeyboardInterrupt:
                stop_waiting()
                console.print(
                    "\\n  [color(208)]\\\\[*][/color(208)] Query cancelled."
                )
                continue

            except Exception as e:
                report_to_engineer(str(e), user_input)
                _stream_error = True
                stop_waiting()
                render_error(str(e))
                final_agent_reply = (
                    "I encountered an error. The Engineer is on it."
                )

            finally:
                stop_waiting()
'''

if old not in s:
    raise SystemExit("ERROR: AI reasoning loop not found")

s = s.replace(old, new, 1)

p.write_text(s)
print("Step 4 applied: Termux AI loop added.")
