"""Natural-language control routing stays deterministic and never executes actions."""
import shlex
import unittest

from lib.control_intent import route_control_request


class ControlIntentTests(unittest.TestCase):
    def assertCommand(self, text, expected):
        result = route_control_request(text)
        self.assertIsInstance(result, dict, text)
        self.assertIn("command", result, text)
        self.assertNotIn("reply", result, text)
        self.assertEqual(shlex.split(result["command"]), shlex.split(expected), text)

    def test_read_only_requests_from_mac_trial(self):
        for text, command in (
            ("List my installed applications.", "apps list"),
            ("List installed apps", "apps list"),
            ("What is my current output volume?", "volume get"),
            ("List my running processes.", "processes list"),
            ("Which system controls are available on this Mac?", "status"),
        ):
            with self.subTest(text=text):
                self.assertCommand(text, command)

    def test_application_and_audio_requests_from_mac_trial(self):
        for text, command in (
            ("Open Calculator.", "apps open Calculator"),
            ("now increase the volume to 80%", "volume set 80"),
            ("Mute the audio.", "volume mute"),
            ("Unmute the audio.", "volume unmute"),
        ):
            with self.subTest(text=text):
                self.assertCommand(text, command)

    def test_file_requests_keep_exact_path_without_sentence_punctuation(self):
        for text, command in (
            ("Create a folder at ~/Documents/Gathm-Test.",
             "files mkdir ~/Documents/Gathm-Test"),
            ('Create another folder "TestingMK" in Documents',
             "files mkdir ~/Documents/TestingMK"),
            ("Rename ~/Documents/Gathm-Test to ~/Documents/Gathm-Test-Renamed.",
             "files move ~/Documents/Gathm-Test ~/Documents/Gathm-Test-Renamed"),
        ):
            with self.subTest(text=text):
                self.assertCommand(text, command)

    def test_screenshot_request_with_saved_location_question(self):
        self.assertCommand("Take a screenshot and tell me where you saved it.", "screenshot")

    def test_ambiguous_trash_request_requires_clarification(self):
        result = route_control_request(
            "Move ~/Documents/Gathm-Test-Renamed or Gathm-Test to Gathm’s recoverable trash."
        )
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("reply"))
        self.assertNotIn("command", result)

    def test_context_mentions_never_become_commands(self):
        for text in (
            "Do not restart my computer",
            "Don't mute the audio",
            "Never open Calculator",
            "How do I restart my computer?",
            "How can I create a folder at ~/Documents/Gathm-Test?",
            'Write a story about "restart my computer"',
            'Translate "Open Calculator" into Hindi',
            '"Shut down my computer"',
            "If I finish work, shut down my computer",
            "If the volume is low, set volume to 80%",
            "Set volume to 30; power shutdown",
            "Open Calculator and restart my computer",
        ):
            with self.subTest(text=text):
                result = route_control_request(text)
                self.assertTrue(result is None or (result.get("reply") and "command" not in result), text)

    def test_relative_audio_changes_do_not_become_absolute_levels(self):
        for text in (
            "Increase the volume by 10%",
            "Decrease volume by 20 percent",
            "Turn the volume up a little",
        ):
            with self.subTest(text=text):
                result = route_control_request(text)
                self.assertTrue(result is None or (result.get("reply") and "command" not in result), text)

    def test_web_destinations_remain_available_to_browser_tools(self):
        for text in (
            "Open https://example.com",
            "Open http://localhost:8766/",
            "Open www.example.com",
            "Open the website https://example.com",
            "Open https://example.com in Safari",
        ):
            with self.subTest(text=text):
                self.assertIsNone(route_control_request(text), text)

    def test_deferred_and_sequential_requests_are_not_app_names(self):
        for text in (
            "Open Calculator, then restart my computer",
            "Open Calculator when I finish work",
            "Open Calculator after five minutes",
        ):
            with self.subTest(text=text):
                result = route_control_request(text)
                self.assertTrue(result is None or (result.get("reply") and "command" not in result), text)

    def test_quoted_app_names_still_work(self):
        self.assertCommand('Open "Activity Monitor"', 'apps open "Activity Monitor"')


if __name__ == "__main__":
    unittest.main()
