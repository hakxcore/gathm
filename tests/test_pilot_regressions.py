import importlib.util
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
        text = "Thought: Need search.\nAction: googler \"The Matrix movie\" --np"
        self.assertEqual(PILOT.extract_tool_input(text), "googler 'The Matrix movie' --np")

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


if __name__ == "__main__":
    unittest.main()
