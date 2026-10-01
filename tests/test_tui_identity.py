"""Exercise terminal layouts and rendering boundaries without a live model."""

from contextlib import contextmanager
from io import StringIO
import os
import unittest
from unittest.mock import patch

from rich.cells import cell_len
from rich.console import Console

from pilot import tui


class TestTerminalConversation(unittest.TestCase):
    @contextmanager
    def _capture(self, width=80):
        output = StringIO()
        with patch.object(tui, "console", Console(
            file=output, width=width, color_system=None,
        )), patch.object(tui.shutil, "get_terminal_size",
                         return_value=os.terminal_size((width, 30))), \
                patch.object(tui.os, "system"):
            yield output

    def test_conversation_and_controls_fit_phone_and_desktop(self):
        for width in (32, 40, 80, 120):
            with self.subTest(width=width), self._capture(width) as output:
                tui.render_welcome("Llama-3.2-1B-Instruct-Q4_K_M", 44,
                                   "Termux", connectivity="offline")
                tui.print_status_bar()
                tui.print_user_message("Help me plan my day. I also want time for a walk.")
                tui.render_response(
                    "Start with **one priority**.\n\n1. Keep time for your meeting.\n"
                    "2. Take a walk.\n\nWhen is your meeting?", speak=False,
                )
                tui.render_help()
                tui.render_tools_list([
                    ("weather", "Check current conditions and forecasts for a location."),
                ])
                tui.render_error("The connection was interrupted. Please try again.")
                tui.render_goodbye()
                rendered = output.getvalue()
                self.assertTrue(all(cell_len(line) <= width
                                    for line in rendered.splitlines()))
                self.assertIn("/listen", rendered)
                self.assertIn("/speak", rendered)
                self.assertIn("/tools", rendered)
                self.assertNotIn("Pilot", rendered)

    def test_dynamic_labels_are_literal_not_rich_markup(self):
        with self._capture() as output:
            tui.render_welcome("[red]local model[/red]", 1, "Termux", "offline")
            tui.print_user_message("Explain [bold]please[/bold]")
            tui.print_tool_exec("websearch '[red]literal query[/red]'")
            tui.render_tools_list([("[bold]tool[/bold]", "[red]description[/red]")])
            tui.render_error("Unexpected token [red]value[/red]")
            rendered = output.getvalue()
            for value in (
                "[red]local model[/red]", "[bold]please[/bold]",
                "[red]literal query[/red]", "[bold]tool[/bold]",
                "[red]description[/red]", "[red]value[/red]",
            ):
                self.assertIn(value, rendered)

    def test_rendering_does_not_repeat_an_already_spoken_reply(self):
        with self._capture(), patch.object(tui, "speak_reply") as speak:
            tui.render_response("A streamed reply", speak=False)
            speak.assert_not_called()
            tui.render_response("A complete reply")
            speak.assert_called_once_with("A complete reply")

    def test_empty_tool_catalog_remains_readable(self):
        with self._capture(40) as output:
            tui.render_tools_list([])
            self.assertIn("No tools are available", output.getvalue())
            self.assertTrue(all(cell_len(line) <= 40
                                for line in output.getvalue().splitlines()))


if __name__ == "__main__":
    unittest.main()
