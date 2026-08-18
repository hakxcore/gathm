from pathlib import Path

p = Path("pilot/main.py")
s = p.read_text()

old = '''    if app is None:
        _require_langchain_runtime()

    print_tricolor_banner()
    conversation_history: List[BaseMessage] = []
'''

new = '''    # Termux uses the lightweight direct LLMProvider runtime.
    # Other operating systems keep the existing LangGraph runtime.
    termux_mode = _is_termux()

    if not termux_mode and app is None:
        _require_langchain_runtime()

    print_tricolor_banner()

    if termux_mode:
        conversation_history: list[dict[str, str]] = []
    else:
        conversation_history: List[BaseMessage] = []
'''

if old not in s:
    raise SystemExit("ERROR: main() initialization block not found")

s = s.replace(old, new, 1)

p.write_text(s)
print("Step 3 applied: Termux main runtime selection added.")
