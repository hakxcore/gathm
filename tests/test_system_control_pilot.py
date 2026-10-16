"""Assistant routing and human approval; models and OS adapters are mocked."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from lib import system_control

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("system_control_pilot", ROOT / "pilot/main.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class PilotControlTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"command": "volume set 30", "summary": "Set volume to 30%.",
                     "warning": "", "requires_confirmation": True}
        prepare = patch.object(system_control, "prepare", return_value=self.plan)
        self.prepare = prepare.start()
        self.addCleanup(prepare.stop)
        execute = patch.object(system_control, "execute", return_value={"ok": True, "output": "Volume set to 30%."})
        self.execute = execute.start()
        self.addCleanup(execute.stop)

    def test_noninteractive_action_stops_with_proposal_without_execution(self):
        with patch.object(pilot, "_can_ask_the_user", return_value=False):
            with self.assertRaises(pilot.ControlApprovalRequired) as raised:
                pilot.run_gathm_tool_raw("control volume set 30")
        self.assertEqual(raised.exception.command, "volume set 30")
        self.execute.assert_not_called()

    def test_terminal_approval_runs_exact_plan_and_decline_does_not_run(self):
        with patch.object(pilot, "_can_ask_the_user", return_value=True), \
                patch.object(pilot, "_confirm_command", return_value=False) as confirm:
            self.assertIn("Cancelled", pilot.run_gathm_tool_raw("control volume set 30"))
            self.execute.assert_not_called()
            confirm.return_value = True
            self.assertEqual(pilot.run_gathm_tool_raw("control volume set 30"), "Volume set to 30%.")
            self.execute.assert_called_once_with(self.plan)

    def test_inspection_needs_no_approval_and_denied_role_never_prepares(self):
        self.plan["requires_confirmation"] = False
        with patch.object(pilot, "_confirm_command") as confirm:
            pilot.run_gathm_tool_raw("control volume get")
            confirm.assert_not_called()
        self.prepare.reset_mock()
        with patch.dict(os.environ, {"GATHM_ALLOWED_TOOLS": "weather"}):
            self.assertIn("access denied", pilot.run_gathm_tool_raw("control volume get"))
            self.prepare.assert_not_called()

    def test_control_is_offline_and_shortlisted_for_everyday_intents(self):
        self.assertFalse(pilot.tool_requires_internet("control"))
        for query in ("Set volume to 30 percent", "Restart my computer", "Move a file in Downloads",
                      "Stop process 123", "Take a screenshot", "Open Calculator", "Dim screen brightness"):
            self.assertIn("control", pilot._shortlist_tools(query, pilot.discover_tools()), query)

    def test_device_capability_question_returns_observed_status_without_inference(self):
        from langchain_core.messages import HumanMessage
        self.plan.update(command="status", requires_confirmation=False)
        self.execute.return_value = {"ok": True, "output": "Brightness unavailable: helper missing"}
        with patch.object(pilot, "_build_llm") as model, patch.object(pilot, "print_tool_exec"):
            events = list(pilot.app.stream({"messages": [HumanMessage(content="Which system controls are available?")]}))
        self.assertEqual(events[-1]["action"]["messages"][0].content, "Brightness unavailable: helper missing")
        model.assert_not_called()

    def test_disabled_raw_shell_prompt_preserves_typed_controls(self):
        from langchain_core.messages import HumanMessage, AIMessage
        with patch.object(pilot, "discover_tools", return_value=["control", "system"]), \
                patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "current_connectivity", return_value="offline"), \
                patch.object(pilot._sysexec, "enabled", return_value=False), \
                patch.object(pilot, "_invoke_spoken", return_value=AIMessage(content="Ready.")) as model:
            pilot.call_model({"messages": [HumanMessage(content="I would like you to restart the computer for me")]})
        prompt = model.call_args.args[1][0].content
        self.assertIn("Raw shell access through 'system' is currently: DISABLED", prompt)
        self.assertIn("This setting does not disable the separate typed 'control' tool", prompt)
        self.assertIn("Tools you CAN use offline: control.", prompt)
        self.assertNotIn("System control is currently: DISABLED", prompt)

    def test_raw_shell_invocation_is_not_rewritten_as_typed_control(self):
        command = "printf '%s' ~/Downloads | head -n 1"
        with patch.object(pilot, "_run_system_command", return_value="raw result") as raw:
            self.assertEqual(pilot.run_gathm_tool_raw("system " + command), "raw result")
        raw.assert_called_once_with(command)
        self.prepare.assert_not_called()
        self.execute.assert_not_called()

    def test_graph_does_not_continue_after_pending_action(self):
        from langchain_core.messages import HumanMessage, AIMessage
        with patch.object(pilot, "_can_ask_the_user", return_value=False), \
                patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "current_connectivity", return_value="offline"), \
                patch.object(pilot, "_invoke_spoken", return_value=AIMessage(content="Action: gathm\nAction Input: control volume set 30")) as model, \
                patch.object(pilot, "print_tool_exec"):
            with self.assertRaises(pilot.ControlApprovalRequired):
                list(pilot.app.stream({"messages": [HumanMessage(content="I'd like my volume adjusted to thirty percent")]},
                                     config={"recursion_limit": 6}))
        model.assert_called_once()
        self.execute.assert_not_called()

    def test_explicit_power_and_level_requests_do_not_depend_on_model_formatting(self):
        from langchain_core.messages import HumanMessage
        for query, command in (("Shut down my computer", "power shutdown"),
                               ("Restart my computer", "power restart"),
                               ("Set volume to 30 percent", "volume set 30")):
            self.plan["command"] = command
            with patch.object(pilot, "_can_ask_the_user", return_value=False), \
                    patch.object(pilot, "_build_llm") as model, patch.object(pilot, "print_tool_exec"):
                with self.assertRaises(pilot.ControlApprovalRequired):
                    list(pilot.app.stream({"messages": [HumanMessage(content=query)]}))
                model.assert_not_called()
        self.execute.assert_not_called()
        for query in ("Do not restart my computer", 'Write a story about "restart my computer"',
                      "If I finish work, shut down my computer", "How do I restart my computer?", "Set volume to 30; power shutdown"):
            self.assertEqual(pilot._direct_control_request(query), "")

    def test_chat_once_serializes_proposal_without_approval_tokens(self):
        spec = importlib.util.spec_from_file_location("control_chat_once", ROOT / "pilot/chat_once.py")
        chat = importlib.util.module_from_spec(spec)
        # chat_once redirects stdout for its protocol; keep that local to this test.
        original = sys.stdout
        try:
            spec.loader.exec_module(chat)
        finally:
            sys.stdout = original
        with patch.dict(sys.modules, {"main": pilot}), \
                patch.object(pilot.app, "stream", side_effect=pilot.ControlApprovalRequired("volume set 30")):
            result, code = chat.run_turn({"query": "Set volume to 30 percent"})
        self.assertEqual(code, 0)
        self.assertEqual(result["pending_control"], {"command": "volume set 30"})
        self.assertNotIn("approval", result)
        self.execute.assert_not_called()

    def test_terminal_default_no_and_markup_are_not_interpreted(self):
        for answer in ("", "no", "yes"):
            with patch("builtins.input", return_value=answer), patch.object(pilot, "console") as console, \
                    patch.object(pilot, "start_waiting"), patch.object(pilot, "stop_waiting"):
                self.assertEqual(pilot._confirm_command("[link=evil]target[/link]", "reason"), answer == "yes")
                self.assertTrue(any(call.kwargs.get("markup") is False for call in console.print.call_args_list))

    def test_transcript_requests_route_after_prior_hallucinated_history(self):
        from langchain_core.messages import HumanMessage, AIMessage
        history = [HumanMessage(content="Open Calculator"),
                   AIMessage(content="Calculator has been opened.")]
        cases = (
            ("List my installed applications.", "apps list"),
            ("List my running processes.", "processes list"),
            ("Open Calculator.", "apps open Calculator"),
            ("now increase the volume to 80%", "volume set 80"),
            ("Mute the audio.", "volume mute"),
            ('create another folder "TestingMK" in Documents', "files mkdir ~/Documents/TestingMK"),
            ("Rename ~/Documents/Gathm-Test to ~/Documents/Gathm-Test-Renamed.",
             "files move ~/Documents/Gathm-Test ~/Documents/Gathm-Test-Renamed"),
            ("Take a screenshot and tell me where you saved it.", "screenshot"),
        )
        import shlex
        for query, expected in cases:
            with self.subTest(query=query), patch.object(pilot, "_build_llm") as model, \
                    patch.object(pilot, "print_tool_exec"), \
                    patch.object(pilot, "run_gathm_tool_raw", return_value="Actual device result") as tool:
                events = list(pilot.app.stream({"messages": history + [HumanMessage(content=query)]}))
                self.assertEqual(shlex.split(tool.call_args.args[0]), ["control", *shlex.split(expected)])
                self.assertEqual(events[-1]["action"]["messages"][0].content, "Actual device result")
                model.assert_not_called()

    def test_control_output_preserves_long_list_and_errors_without_model_rewrite(self):
        from langchain_core.messages import HumanMessage
        for output in ("Error: actual OS refusal", "Cancelled. The system action was not run.",
                       "\n".join("Application " + str(i) for i in range(250))):
            with patch.object(pilot, "_build_llm") as model, patch.object(pilot, "print_tool_exec"), \
                    patch.object(pilot, "run_gathm_tool_raw", return_value=output):
                events = list(pilot.app.stream({"messages": [HumanMessage(content="List my installed apps")]}))
                self.assertIn(output, events[-1]["action"]["messages"][0].content)
                model.assert_not_called()

    def test_unparsed_device_claim_is_not_shown_or_streamed(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from unittest.mock import Mock
        for invented in ("The volume has been set to 30%.", "Operation not permitted."):
            model, sink = Mock(), Mock()
            model.invoke.return_value = AIMessage(content=invented)
            with patch.object(pilot, "_build_llm", return_value=model), \
                    patch.object(pilot, "current_connectivity", return_value="offline"), \
                    patch.object(pilot, "_TOKEN_SINK", sink):
                result = pilot.call_model({"messages": [HumanMessage(content="I'd like my volume adjusted to thirty percent")]})
            self.assertIn("No system action was run", result["messages"][0].content)
            self.assertNotIn(invented, result["messages"][0].content)
            model.stream.assert_not_called()
            sink.feed.assert_not_called()

    def test_correction_and_chat_yes_do_not_invent_result_or_authorize_retry(self):
        from langchain_core.messages import HumanMessage, AIMessage
        for query in ("it's not opened", "it's not", "it's still there", "yes"):
            history = [HumanMessage(content="Open Calculator"), AIMessage(content="Opened.")]
            with patch.object(pilot, "_build_llm") as model:
                result = pilot.call_model({"messages": history + [HumanMessage(content=query)]})
            self.assertIn("No new system action has run", result["messages"][0].content)
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
