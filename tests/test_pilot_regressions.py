import importlib.util
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PILOT_MAIN_PATH = PROJECT_ROOT / "pilot" / "main.py"


def _load_pilot_module():
    spec = importlib.util.spec_from_file_location("pilot_main", PILOT_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load pilot module from {PILOT_MAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = _load_pilot_module()


class TestPilotRegressions(unittest.TestCase):
    def test_extract_tool_input_standard_action_format(self):
        text = (
            "Thought: I should use a dictionary.\n"
            "Action: gathm\n"
            "Action Input: define ubiquitous\n"
        )
        self.assertEqual(PILOT.extract_tool_input(text), "define ubiquitous")

    def test_extract_tool_input_nonstandard_action_line(self):
        text = "Thought: Need search.\nAction: websearch \"The Matrix movie\" -n 3"
        self.assertEqual(PILOT.extract_tool_input(text), "websearch 'The Matrix movie' -n 3")

    def test_extract_tool_input_ignores_placeholder_template(self):
        text = "Action: gathm\nAction Input: [tool_name] [arguments]"
        self.assertIsNone(PILOT.extract_tool_input(text))

    def test_currency_argument_order_normalization(self):
        normalized = PILOT.normalize_tool_command("currency 100 USD EUR")
        self.assertEqual(normalized, "currency USD EUR 100")

    def test_gif_multiword_normalization(self):
        normalized = PILOT.normalize_tool_command("gif show cute cats")
        self.assertEqual(normalized, "gif cute_cats")

    def test_high_risk_query_detection_for_open_ftp(self):
        category = PILOT.classify_high_risk_query(
            "can you find me openly available ftp server from the internet?"
        )
        self.assertEqual(category, "exposed-infrastructure-discovery")

    def test_high_risk_query_detection_for_open_cameras(self):
        category = PILOT.classify_high_risk_query(
            "can you find me opennel available cameras ?"
        )
        self.assertEqual(category, "exposed-infrastructure-discovery")

    def test_non_risky_query_not_blocked(self):
        category = PILOT.classify_high_risk_query("tell me whats the weather in pune today?")
        self.assertIsNone(category)


    def test_prompt_keeps_constants_before_variables(self):
        """The rules must precede the tool list, or the prefix cache is worthless.

        llama.cpp reuses the KV state of a prompt PREFIX. This template used to
        open with the shortlisted tool list, which changes with the question, so
        the cacheable prefix ended after about fifteen tokens and the ~700
        tokens of rules below it were re-prefilled every turn — measured at
        twenty-six seconds of re-reading unchanged text per question on a phone.

        This guards the ordering, not the wording. A variable field that drifts
        back above a constant one costs a full prefill per turn, and nothing
        else in this suite would notice.
        """
        template = PILOT_MAIN_PATH.read_text().split(
            'system_prompt = f"""', 1)[1].split('"""', 1)[0]

        first_variable = re.search(r"\{[a-z_]+\}", template)
        self.assertIsNotNone(first_variable, "the template has no substitutions")
        prefix = template[: first_variable.start()]

        self.assertIn("CRITICAL RULES:", prefix,
                      "CRITICAL RULES moved below a variable field — prefix cache dead")
        self.assertGreater(len(prefix), 2000,
                           "stable prefix is only %d chars; a variable field moved up"
                           % len(prefix))
        self.assertGreater(template.index("{tool_descriptions}"),
                           template.index("CRITICAL RULES:"),
                           "the tool list is above the rules again")
        # The rules point at the list; with the list below them, so must the text.
        self.assertNotIn("list above", template,
                         "a rule still refers to a tool list 'above' it")

    def test_tool_output_is_not_mistaken_for_the_question(self):
        """The shortlist must route on what the user asked, not on the answer.

        A tool's output re-enters the conversation as a HumanMessage, because
        that is what the ReAct loop feeds back. "The last HumanMessage" is
        therefore the OBSERVATION once a tool has run, so step two of a turn was
        choosing its tools by reading the weather report rather than the
        question that asked for it.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        conversation = [
            HumanMessage(content="weather in Delhi"),
            AIMessage(content="Action: gathm\nAction Input: weather Delhi"),
            HumanMessage(content="Observation: Delhi 31C haze, humidity 44%"),
        ]
        self.assertEqual(PILOT._last_user_question(conversation), "weather in Delhi")

        # A failed tool comes back the same way and must also be ignored.
        conversation.append(HumanMessage(content="Error: Could not parse tool input."))
        self.assertEqual(PILOT._last_user_question(conversation), "weather in Delhi")

        # A genuine follow-up IS the question again.
        conversation.append(HumanMessage(content="and in Mumbai?"))
        self.assertEqual(PILOT._last_user_question(conversation), "and in Mumbai?")

        # Nothing from the user at all is an empty string, not a crash.
        self.assertEqual(PILOT._last_user_question([]), "")
        self.assertEqual(PILOT._last_user_question(None), "")

    def test_shortlist_is_stable_across_react_steps(self):
        """The tool list is part of the system prompt, so it must not move.

        If step two offers different tools than step one, the system prompt
        differs, and llama.cpp cannot reuse the prefix it just cached — the
        whole prompt is prefilled again, at ~26 tokens per second on a phone.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        tools = PILOT.discover_tools()
        question = "MX records for gmail.com"
        step_one = PILOT._shortlist_tools(question, tools)

        conversation = [
            HumanMessage(content=question),
            AIMessage(content="Action: gathm\nAction Input: dns -t MX gmail.com"),
            HumanMessage(content="Observation: gmail.com MX 10 alt1.aspmx.l.google.com"),
        ]
        step_two = PILOT._shortlist_tools(
            PILOT._last_user_question(conversation), tools)
        self.assertEqual(step_one, step_two,
                         "the tool list changed between ReAct steps")

if __name__ == "__main__":
    unittest.main()
