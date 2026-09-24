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

if __name__ == "__main__":
    unittest.main()
