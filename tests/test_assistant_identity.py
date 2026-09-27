"""Assistant prompt contracts without starting a model or executing tools."""
import importlib.util
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("assistant_main", ROOT / "pilot" / "main.py")
ASSISTANT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ASSISTANT)


@unittest.skipUnless(ASSISTANT.LANGCHAIN_AVAILABLE, "LangChain messages required")
class AssistantIdentityTests(unittest.TestCase):
    def invoke(self, question, reply="Let's work through it together.", history=()):
        from langchain_core.messages import AIMessage, HumanMessage

        messages = [*history, HumanMessage(content=question)]
        captured = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(ASSISTANT, "_build_llm", return_value=object()))
            stack.enter_context(patch.object(ASSISTANT, "current_connectivity", return_value="online"))
            stack.enter_context(patch.object(ASSISTANT, "_invoke_spoken", side_effect=lambda _, msgs:
                                            captured.append(msgs) or AIMessage(content=reply)))
            result = ASSISTANT.call_model({"messages": messages})
        return result, captured[0]

    def test_greeting_and_capabilities_use_gathm_identity_without_tool_discovery(self):
        for question in ("hello", "who are you", "what can you do"):
            with self.subTest(question=question), patch.object(
                    ASSISTANT, "discover_tools", side_effect=AssertionError("unnecessary discovery")):
                result, messages = self.invoke(question)
                self.assertEqual(result["next_step"], "end")
                self.assertTrue(messages[0].content.startswith("You are Gathm,"))
                self.assertNotIn("You are Pilot", messages[0].content)
                self.assertIn("think, learn, write, plan", messages[0].content)
                self.assertNotIn("AVAILABLE TOOLS", messages[0].content)

    def test_short_response_retains_conversation_context(self):
        from langchain_core.messages import AIMessage, HumanMessage
        history = [HumanMessage(content="Call me Sam in this conversation."),
                   AIMessage(content="Sure, Sam.")]
        _, messages = self.invoke("thanks", history=history)
        self.assertEqual(messages[1:-1], history)

    def test_writing_explanation_and_planning_can_end_without_a_tool(self):
        for question in ("Help me with a thoughtful birthday message",
                         "Explain how DNS works", "Help me plan a study routine",
                         "Teach me the derivative of x squared"):
            with self.subTest(question=question):
                result, messages = self.invoke(question, "Here is a useful starting point.")
                self.assertEqual(result["next_step"], "end")
                prompt = messages[0].content
                self.assertIn("Answer explanations, drafting, brainstorming, planning", prompt)
                self.assertIn("Having a relevant tool does not mean you must use it", prompt)
                self.assertNotIn("For MATH", prompt)
                self.assertNotIn("what something means", prompt)
                self.assertNotIn("Thought: [your reasoning]", prompt)
                self.assertLess(len(prompt.split("AVAILABLE TOOLS", 1)[0]), 3000)

    def test_generic_help_does_not_expand_to_the_whole_catalogue(self):
        self.assertIsNone(ASSISTANT._CATALOGUE_RE.search("help me with a birthday message"))
        self.assertIsNotNone(ASSISTANT._CATALOGUE_RE.search("which tools are available"))
        tools = ASSISTANT.discover_tools()
        with patch.object(ASSISTANT, "TOOL_SHORTLIST", 2):
            self.assertLessEqual(len(ASSISTANT._shortlist_tools("help me with a birthday message", tools)), 2)
            self.assertEqual(ASSISTANT._shortlist_tools("zzzz qqqq wwww", tools),
                             ["websearch", "system"])

    def test_current_device_and_mixed_requests_retain_action_path(self):
        cases = [("weather in Delhi today", "weather Delhi", "weather"),
                 ("How much disk space is free?", "system df -h", "system"),
                 ("Write a note to a file in my home folder", "system touch ~/note.txt", "system"),
                 ("Help me plan tomorrow using the weather forecast in Delhi",
                  "weather -f Delhi", "weather"),
                 ("Search for the latest space news", "websearch space news", "websearch")]
        for question, command, tool in cases:
            with self.subTest(question=question):
                result, messages = self.invoke(question, f"Action: gathm\nAction Input: {command}")
                self.assertEqual(result["next_step"], "action")
                self.assertIn(f"- {tool}:", messages[0].content)
                self.assertIn("Respect approval requests and disabled capabilities", messages[0].content)

    def test_dynamic_tools_follow_a_shared_cacheable_prefix(self):
        _, first = self.invoke("weather in Delhi")
        _, second = self.invoke("DNS records for example.org")
        marker = "AVAILABLE TOOLS — optional capabilities for this request:"
        self.assertEqual(first[0].content.split(marker)[0], second[0].content.split(marker)[0])
        self.assertTrue(first[0].content.startswith(ASSISTANT._ASSISTANT_PROMPT))

    def test_failure_does_not_claim_a_background_engineer_was_notified(self):
        from langchain_core.messages import AIMessage, HumanMessage
        state = {"messages": [HumanMessage(content="weather in Delhi"),
                              AIMessage(content="Action: gathm\nAction Input: weather Delhi")]}
        with patch.object(ASSISTANT, "print_tool_exec"), patch.object(
                ASSISTANT, "run_gathm_tool_raw", return_value="Error: network unreachable"):
            result = ASSISTANT.tool_node(state)
        text = result["messages"][0].content
        self.assertIn("Error: network unreachable", text)
        self.assertNotIn("engineer has been notified", text)
        self.assertIn("without promising a background repair", text)

    def test_error_reporting_only_promises_its_local_record(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
                ASSISTANT.Path, "home", return_value=Path(temp)), patch("builtins.print") as output:
            ASSISTANT.report_to_engineer("backend unavailable", "hello")
            record = (Path(temp) / ".gathm/agent/engineer_tasks.log").read_text()
        self.assertIn("backend unavailable", record)
        printed = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("saved locally", printed)
        self.assertNotIn("resolved shortly", printed)


if __name__ == "__main__":
    unittest.main()
