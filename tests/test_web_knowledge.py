"""Automatic knowledge fallback: real graph, mocked network/model boundaries."""
import importlib.util
import importlib.machinery
import json
import os
import socket
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from lib import web_knowledge as policy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_pilot", ROOT / "pilot/main.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
loader = importlib.machinery.SourceFileLoader("web_tool", str(ROOT / "tools/websearch/websearch"))
search_spec = importlib.util.spec_from_loader(loader.name, loader)
search = importlib.util.module_from_spec(search_spec)
loader.exec_module(search)
HITS = json.dumps({"results": [{"title": "Public evidence", "url": "https://example.org/fact",
                                  "snippet": "The documented answer is 42."}]})


class WebPolicyTests(unittest.TestCase):
    def test_private_context_and_offline_requests_are_not_search_queries(self):
        for question in ("What is my password?", "What did we discuss yesterday?",
                         "Find alice@example.com", "What is in /Users/sam/private.txt?",
                         "What does https://example.org/?token=secret mean?",
                         "Explain this without internet", "What is account 12345678?",
                         "Write a note", "What is my medical record?"):
            with self.subTest(question=question):
                self.assertEqual(policy.public_query(question), "")
        self.assertEqual(policy.public_query("Who discovered pulsars?"), "Who discovered pulsars?")

    def test_uncertainty_does_not_override_refusals(self):
        self.assertTrue(policy.needs_lookup("I don't have reliable information about that."))
        self.assertTrue(policy.needs_lookup("I'm not sure who discovered it."))
        self.assertFalse(policy.needs_lookup("I can't help with that. I don't know."))
        self.assertFalse(policy.needs_lookup("The answer is 42."))

    def test_sources_reject_script_credentials_malformed_and_duplicate_urls(self):
        raw = json.dumps({"results": [{"url": "javascript:alert(1)"},
                                      {"url": "https://user:secret@example.org/"},
                                      {"url": "https://example.org/ok"}]})
        evidence, sources = policy.search_evidence(raw)
        self.assertEqual(sources, ["https://example.org/ok"])
        self.assertNotIn("secret", evidence)
        for bad in ("not JSON", "[]", '{"results": null}', '{"results": {}}'):
            self.assertEqual(policy.search_evidence(bad), ("", []))

    def test_network_failure_does_not_try_every_provider_or_leak_url(self):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test", "GATHM_SEARX_URL": "https://private:secret@search/"}), \
                patch.object(search, "search_brave", side_effect=search.urllib.error.URLError(socket.gaierror())), \
                patch.object(search, "search_searx") as alternate:
            hits, backend, error = search.search("public topic", 3)
        alternate.assert_not_called()
        self.assertEqual((hits, backend), ([], ""))
        self.assertNotIn("secret", error)
        self.assertIn("network unavailable", error)

    def test_response_size_is_bounded(self):
        with patch.object(search, "_DEADLINE", 0.0), patch.object(search.urllib.request, "urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"x" * (search.MAX_RESPONSE_BYTES + 1)
            with self.assertRaises(ValueError):
                search._fetch("https://example.org")
            opened.return_value.__enter__.return_value.read.assert_called_once_with(search.MAX_RESPONSE_BYTES + 1)


@unittest.skipUnless(pilot.LANGCHAIN_AVAILABLE, "LangGraph required")
class WebGraphTests(unittest.TestCase):
    def run_graph(self, replies, *, question="Who discovered pulsars?", status="online", result=HITS, allowed=None):
        from langchain_core.messages import AIMessage, HumanMessage
        with ExitStack() as stack:
            stack.enter_context(patch.object(pilot, "_WEB_RETRY_AT", 0.0))
            stack.enter_context(patch.object(pilot, "_build_llm", return_value=object()))
            stack.enter_context(patch.object(pilot, "current_connectivity", return_value=status))
            stack.enter_context(patch.object(pilot, "print_tool_exec"))
            if allowed is not None:
                stack.enter_context(patch.dict(os.environ, {"GATHM_ALLOWED_TOOLS": allowed}))
            model = stack.enter_context(patch.object(pilot, "_invoke_spoken", side_effect=[AIMessage(content=r) for r in replies]))
            tool = stack.enter_context(patch.object(pilot, "run_gathm_tool_raw", return_value=result))
            events = list(pilot.app.stream({"messages": [HumanMessage(content=question)]},
                                          {"recursion_limit": pilot.AGENT_MAX_STEPS}))
            retry_at = pilot._WEB_RETRY_AT
        return events[-1][next(iter(events[-1]))]["messages"][-1].content, tool, model, retry_at

    def test_unknown_answer_searches_once_and_keeps_actual_sources(self):
        answer, tool, model, _ = self.run_graph(["I don't know.", "The documented answer is 42."])
        tool.assert_called_once_with("websearch 'Who discovered pulsars?'")
        self.assertIn("https://example.org/fact", answer)
        self.assertEqual(model.call_count, 1)
        self.assertIn("documented answer is 42", answer)

    def test_known_answer_has_no_network_or_extra_inference(self):
        answer, tool, model, _ = self.run_graph(["Jocelyn Bell Burnell discovered pulsars."])
        tool.assert_not_called()
        self.assertEqual(model.call_count, 1)

    def test_offline_uncertainty_keeps_local_reply_with_clear_gap(self):
        answer, tool, _, _ = self.run_graph(["I'm not sure."], status="offline")
        tool.assert_not_called()
        self.assertIn("I'm not sure", answer)
        self.assertIn("Internet is unavailable", answer)

    def test_model_cannot_force_web_when_offline(self):
        answer, tool, _, _ = self.run_graph(["Action: gathm\nAction Input: websearch pulsars",
                                            "Pulsars are rotating neutron stars."], status="offline")
        tool.assert_not_called()
        self.assertIn("No online lookup ran", answer)
        self.assertIn("local knowledge", answer)

    def test_network_loss_after_online_probe_uses_local_answer_and_backoff(self):
        answer, tool, _, retry_at = self.run_graph(["I don't know.", "Pulsars are rotating neutron stars."],
                                                 result="Error: network unreachable")
        self.assertEqual(tool.call_count, 1)
        self.assertGreater(retry_at, pilot.time.monotonic())
        self.assertIn("cannot verify", answer)
        self.assertNotIn("Pulsars are rotating neutron stars", answer)
        self.assertNotIn("Sources:", answer)

    def test_denied_tool_never_searches(self):
        _, tool, model, _ = self.run_graph(["I don't know."], allowed="weather")
        tool.assert_not_called()
        self.assertEqual(model.call_count, 1)

    def test_model_query_cannot_exfiltrate_learned_private_context(self):
        _, tool, _, _ = self.run_graph(["Action: gathm\nAction Input: websearch secret-from-memory", "42"])
        tool.assert_called_once_with("websearch 'Who discovered pulsars?'")

    def test_private_or_opted_out_question_does_not_auto_search(self):
        for question in ("What is my password?", "Who discovered pulsars? Do not search"):
            _, tool, _, _ = self.run_graph(["I don't know."], question=question)
            tool.assert_not_called()

    def test_private_model_web_call_is_blocked(self):
        answer, tool, _, _ = self.run_graph(["Action: gathm\nAction Input: websearch secret"],
                                           question="What is my password?")
        tool.assert_not_called()
        self.assertIn("standalone public question", answer)

    def test_second_search_with_different_terms_does_not_retry(self):
        answer, tool, _, _ = self.run_graph(["I don't know.", "Action: gathm\nAction Input: websearch other terms"])
        self.assertEqual(tool.call_count, 1)
        self.assertIn("https://example.org/fact", answer)

    def test_failed_search_cooldown_blocks_later_turn(self):
        from langchain_core.messages import AIMessage, HumanMessage
        with patch.object(pilot, "_WEB_RETRY_AT", pilot.time.monotonic() + 30), \
                patch.object(pilot, "current_connectivity", return_value="online"), \
                patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "_invoke_spoken", return_value=AIMessage(content="I don't know.")):
            result = pilot.call_model({"messages": [HumanMessage(content="Who discovered pulsars?")]})
        self.assertEqual(result["next_step"], "end")
        self.assertIn("temporarily unavailable", result["messages"][-1].content)

    def test_uncertainty_does_not_exceed_graph_tool_budget(self):
        from langchain_core.messages import AIMessage, HumanMessage
        messages = [HumanMessage(content="Who discovered pulsars?"),
                    AIMessage(content="Action: gathm\nAction Input: define pulsars"),
                    HumanMessage(content="Observation: no answer"),
                    AIMessage(content="Action: gathm\nAction Input: news pulsars"),
                    HumanMessage(content="Observation: no answer")]
        with patch.object(pilot, "current_connectivity", return_value="online"), \
                patch.object(pilot, "_build_llm", return_value=object()), \
                patch.object(pilot, "_invoke_spoken", return_value=AIMessage(content="I don't know.")):
            result = pilot.call_model({"messages": messages})
        self.assertEqual(result["next_step"], "end")

    def test_search_subprocess_has_deadline_compact_json_and_permission_check(self):
        from subprocess import CompletedProcess
        with patch.object(pilot.subprocess, "run", return_value=CompletedProcess([], 0, HITS, "")) as run:
            with patch.dict(os.environ, {"GATHM_ALLOWED_TOOLS": "websearch"}):
                self.assertEqual(pilot.run_gathm_tool_raw("websearch pulsars"), HITS)
            self.assertEqual(run.call_args.kwargs["timeout"], 8)
            self.assertEqual(run.call_args.kwargs["env"]["GATHM_WEBSEARCH_AGENT"], "1")
            self.assertEqual(run.call_args.kwargs["env"]["GATHM_WEBSEARCH_TIMEOUT"], "5")
            run.reset_mock()
            with patch.dict(os.environ, {"GATHM_ALLOWED_TOOLS": "weather"}):
                self.assertIn("denied", pilot.run_gathm_tool_raw("websearch pulsars"))
            run.assert_not_called()

    def test_uncertain_draft_is_not_streamed_before_search(self):
        from langchain_core.messages import AIMessage, HumanMessage
        class StreamingModel:
            def stream(self, messages):
                yield AIMessage(content="I don't know.")
        from unittest.mock import Mock
        sink = Mock()
        with patch.object(pilot, "_TOKEN_SINK", sink):
            result = pilot._invoke_spoken(StreamingModel(), [HumanMessage(content="Who discovered pulsars?")])
        self.assertEqual(result.content, "I don't know.")
        sink.feed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
