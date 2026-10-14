"""Regression cases from real hallucinated search, desktop and app replies."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import control_intent, system_control, web_knowledge
from lib.llm import LLMConfig, LLMProvider

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("grounded_pilot", ROOT / "pilot/main.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
QUESTION = "okay leave it can help me to find how many files are there on my desktop?"
HITS = json.dumps({"results": [{"title": "hakxcore profile", "url": "https://github.com/hakxcore",
                                  "snippet": "A public profile using the exact handle hakxcore."}]})


class GroundingPolicyTests(unittest.TestCase):
    def test_changing_facts_need_evidence_but_stable_explanations_do_not(self):
        self.assertTrue(web_knowledge.requires_lookup("What is the latest Python version?"))
        self.assertTrue(web_knowledge.requires_lookup("What is the weather in Mumbai today?"))
        self.assertFalse(web_knowledge.requires_lookup("Explain how electric current works"))
        self.assertFalse(web_knowledge.requires_lookup("Explain Python lists"))
        self.assertFalse(web_knowledge.unbacked_action_claim("I opened a magical door.", "Write a story"))

    def test_explicit_lookup_variants_keep_exact_handle(self):
        for query in ("find who is hakxcore?", 'I want you to find information about "hakxcore"',
                      "no find for Hakxcore", "yes but find it look for Hakxcore",
                      "browser for hakxcore and you will get the info", "google for hakxcore now",
                      "no I'm referring to hakxcore not hak5"):
            with self.subTest(query=query):
                self.assertTrue(web_knowledge.explicit_lookup(query))
                self.assertIn("hakxcore", web_knowledge.public_query(query).lower())
                self.assertNotIn("hak5", web_knowledge.public_query(query).lower())

    def test_desktop_count_is_a_read_request_and_followup_keeps_target(self):
        from langchain_core.messages import AIMessage, HumanMessage
        self.assertEqual(shlex.split(control_intent.route_control_request(QUESTION)["command"]),
                         ["files", "count", "~/Desktop"])
        history = [HumanMessage(content=QUESTION), AIMessage(content="Invented old desktop listing"),
                   HumanMessage(content="you do and tell me")]
        self.assertEqual(control_intent.read_only_followup(history), QUESTION)

    def test_followups_cannot_approve_writes_or_reuse_older_targets(self):
        from langchain_core.messages import HumanMessage
        for previous, current in (("Open Notes", "do it"), ("Delete ~/Desktop", "you do and tell me"),
                                  (QUESTION, "yes"), ("Explain Python", "do it")):
            self.assertEqual(control_intent.read_only_followup([
                HumanMessage(content=QUESTION), HumanMessage(content=previous),
                HumanMessage(content=current)]), "")

    def test_open_music_prefers_app_and_explicit_folder_remains_folder(self):
        self.assertEqual(shlex.split(control_intent.route_control_request("open music")["command"]),
                         ["apps", "open", "music"])
        self.assertEqual(shlex.split(control_intent.route_control_request("open folder music")["command"]),
                         ["files", "open", "~/Music"])

    def test_unsupported_writing_and_playback_do_not_become_instructions(self):
        for query in ("open notes and write Mukesh on it", "play music on my system", "pleay music"):
            plan = control_intent.route_control_request(query)
            self.assertIn("not supported", plan["reply"])
            self.assertNotIn("command", plan)

    def test_ollama_sampling_default_and_explicit_override(self):
        constructor = Mock()
        provider = LLMProvider(LLMConfig(backend="ollama", model="test-model"))
        with patch.dict(sys.modules, {"langchain_ollama": SimpleNamespace(ChatOllama=constructor)}), \
                patch.dict(os.environ):
            os.environ.pop("GATHM_LLM_TEMPERATURE", None)
            provider.langchain_chat_model()
            self.assertEqual(constructor.call_args.kwargs["temperature"], 0.2)
            os.environ["GATHM_LLM_TEMPERATURE"] = "0.6"
            provider.langchain_chat_model()
            self.assertEqual(constructor.call_args.kwargs["temperature"], 0.6)


class ActualControlEvidenceTests(unittest.TestCase):
    def test_count_uses_complete_directory_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, "home", return_value=Path(temp)), \
                patch.object(system_control, "_audit"):
            desktop = Path(temp).resolve() / "Desktop"
            desktop.mkdir()
            for i in range(305):
                (desktop / str(i)).touch()
            (desktop / ".hidden").touch()
            (desktop / "folder").mkdir()
            (desktop / "folder/nested").touch()
            (desktop / "link").symlink_to(desktop / "folder")
            plan = system_control.prepare(shlex.join(["files", "count", str(desktop)]))
            self.assertFalse(plan["requires_confirmation"])
            result = system_control.execute(plan)
            self.assertTrue(result["ok"], result)
            self.assertIn("Files: 306", result["output"])
            self.assertIn("Folders: 1", result["output"])
            self.assertIn("Symlinks: 1", result["output"])

    def test_app_aliases_resolve_only_to_installed_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            apps = {}
            for name in ("Music", "Mail", "Safari", "System Settings"):
                path = Path(temp) / (name + ".app")
                path.mkdir()
                apps[str(path)] = name
            with patch.object(system_control, "platform_name", return_value="macos"), \
                    patch.object(system_control, "_which", return_value="/test/open"), \
                    patch.object(system_control, "_installed_apps", return_value=apps), \
                    patch.object(system_control, "_run") as run:
                for alias, name in (("Apple Music", "Music"), ("email", "Mail"),
                                    ("safari browser", "Safari"), ("settings", "System Settings")):
                    plan = system_control.prepare(shlex.join(["apps", "open", alias]))
                    self.assertEqual(plan["_args"], [str(Path(temp) / (name + ".app"))])
                    self.assertTrue(plan["requires_confirmation"])
                with self.assertRaises(ValueError):
                    system_control.prepare("apps open /Applications/Applemusic.app")
                run.assert_not_called()


@unittest.skipUnless(pilot.LANGCHAIN_AVAILABLE, "LangGraph required")
class GroundedGraphTests(unittest.TestCase):
    def test_search_returns_real_sources_without_a_model_biography(self):
        from langchain_core.messages import HumanMessage
        with patch.object(pilot, "current_connectivity", return_value="online"), \
                patch.object(pilot, "_WEB_RETRY_AT", 0.0), patch.object(pilot, "_build_llm") as llm, \
                patch.object(pilot, "print_tool_exec"), \
                patch.object(pilot, "run_gathm_tool_raw", return_value=HITS) as tool:
            events = list(pilot.app.stream({"messages": [HumanMessage(content="find who is hakxcore?")]}))
        llm.assert_not_called()
        self.assertIn("hakxcore", tool.call_args.args[0])
        self.assertNotIn("hak5", tool.call_args.args[0])
        answer = events[-1]["action"]["messages"][0].content
        self.assertIn("https://github.com/hakxcore", answer)
        self.assertNotIn("Hak5", answer)
        self.assertEqual(events[-1]["action"]["next_step"], "end")

    def test_offline_and_failed_search_cannot_generate_a_fake_identity(self):
        from langchain_core.messages import HumanMessage
        for status in ("online", "offline"):
            with self.subTest(status=status), patch.object(pilot, "current_connectivity", return_value=status), \
                    patch.object(pilot, "_WEB_RETRY_AT", 0.0), patch.object(pilot, "_build_llm") as llm, \
                    patch.object(pilot, "print_tool_exec"), \
                    patch.object(pilot, "run_gathm_tool_raw", return_value="Error: unavailable") as tool:
                events = list(pilot.app.stream({"messages": [HumanMessage(content="find who is hakxcore?")]}))
            llm.assert_not_called()
            self.assertEqual(tool.call_count, int(status == "online"))
            self.assertIn("cannot verify", events[-1]["action"]["messages"][0].content)

    def test_desktop_count_followup_returns_actual_tool_result_without_rewrite(self):
        from langchain_core.messages import HumanMessage, AIMessage
        history = [HumanMessage(content=QUESTION), AIMessage(content="Try listing Desktop"),
                   HumanMessage(content="you do and tell me")]
        with patch.object(pilot, "_build_llm") as llm, patch.object(pilot, "print_tool_exec"), \
                patch.object(pilot, "run_gathm_tool_raw", return_value="Files: 7\nFolders: 2") as tool:
            events = list(pilot.app.stream({"messages": history}))
        llm.assert_not_called()
        self.assertEqual(shlex.split(tool.call_args.args[0]), ["control", "files", "count", "~/Desktop"])
        self.assertIn("Files: 7\nFolders: 2", events[-1]["action"]["messages"][0].content)

    def test_fabricated_launch_claim_is_neither_shown_nor_streamed(self):
        from langchain_core.messages import HumanMessage, AIMessage
        invented = "Launch requested for /Applications/Applemusic.app"
        class Model:
            def stream(self, messages):
                yield AIMessage(content=invented)
        sink = Mock()
        with patch.object(pilot, "_build_llm", return_value=Model()), \
                patch.object(pilot, "current_connectivity", return_value="offline"), \
                patch.object(pilot, "_TOKEN_SINK", sink):
            result = pilot.call_model({"messages": [HumanMessage(content="apple music")]})
        self.assertNotIn(invented, result["messages"][0].content)
        self.assertIn("No lookup or device action ran", result["messages"][0].content)
        sink.feed.assert_not_called()

    def test_instruction_role_is_system_and_history_is_retained(self):
        from langchain_core.messages import HumanMessage, AIMessage
        captured = []
        history = [HumanMessage(content="Explain DNS"), AIMessage(content="A naming system."),
                   HumanMessage(content="Explain caching")]
        with patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "current_connectivity", return_value="online"), \
                patch.object(pilot, "_invoke_spoken", side_effect=lambda _, m: captured.append(m) or AIMessage(content="Caches reuse results.")):
            pilot.call_model({"messages": history})
        self.assertEqual(captured[0][0].type, "system")
        self.assertEqual(captured[0][1:], history)

    def test_tool_error_ends_turn_without_fabricated_package_advice(self):
        from langchain_core.messages import HumanMessage, AIMessage
        with patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "current_connectivity", return_value="online"), \
                patch.object(pilot, "_invoke_spoken", return_value=AIMessage(content="Action: gathm\nAction Input: jukebox")) as model, \
                patch.object(pilot, "print_tool_exec"), \
                patch.object(pilot, "run_gathm_tool_raw", return_value="Error: Required command 'telnet' is not installed."):
            events = list(pilot.app.stream({"messages": [HumanMessage(content="Run jukebox")]}))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(events[-1]["action"]["messages"][0].content,
                         "Error: Required command 'telnet' is not installed.")


if __name__ == "__main__":
    unittest.main()
