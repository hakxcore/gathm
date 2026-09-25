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

    def test_a_repeated_tool_call_is_refused(self):
        """The same command twice in one turn cannot produce new information.

        Observed on a phone: "weather in mumbai" ran the weather tool six
        times, five of them identical and argument-less, then failed with
        HTTP 400 nine minutes in — the context had overflowed because every
        repeat added another ~740 tokens of observation to it.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        already = [
            HumanMessage(content="weather in mumbai"),
            AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
            HumanMessage(content="Observation: Mumbai 29C patchy rain"),
        ]
        self.assertEqual(PILOT._invocations_already_run(
            already + [AIMessage(content="x")]), ["weather mumbai"])

    @unittest.skipUnless(PILOT.LANGCHAIN_AVAILABLE,
                         "tool_node builds LangChain messages")
    def test_a_repeat_is_refused_without_running_the_tool(self):
        """The refusal must not execute anything — that is the saving."""
        from langchain_core.messages import AIMessage, HumanMessage

        ran = []
        original = PILOT.run_gathm_tool_raw
        PILOT.run_gathm_tool_raw = lambda cmd: ran.append(cmd) or "should not run"
        try:
            state = {"messages": [
                HumanMessage(content="weather in mumbai"),
                AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
                HumanMessage(content="Observation: Mumbai 29C patchy rain"),
                AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
            ]}
            observation = PILOT.tool_node(state)["messages"][0].content
        finally:
            PILOT.run_gathm_tool_raw = original

        self.assertEqual(ran, [], "the repeated tool was executed anyway")
        # Earlier this returned a refusal telling the model to answer from what
        # it had. A 1B model ignored that and asked again until the recursion
        # limit threw, so the repeat now ends the turn with the observation
        # itself — the data was there the whole time.
        self.assertIn("29C", observation)

    def test_a_different_tool_call_still_runs(self):
        """The guard must not block a genuine second step."""
        from langchain_core.messages import AIMessage, HumanMessage

        history = [
            HumanMessage(content="weather in mumbai"),
            AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
            HumanMessage(content="Observation: Mumbai 29C"),
            AIMessage(content="Action: gathm\nAction Input: weather delhi"),
        ]
        self.assertNotIn("weather delhi",
                         PILOT._invocations_already_run(history))

    def test_the_loop_limit_is_reachable_in_human_time(self):
        """A ceiling nobody waits for is not a ceiling.

        Each round costs ~30 s on a phone and grows the context, so the old
        limit of 25 was twelve minutes — and the turn failed by overflowing
        the context long before reaching it.
        """
        self.assertLessEqual(PILOT.AGENT_MAX_STEPS, 8)
        self.assertGreaterEqual(PILOT.AGENT_MAX_STEPS, 3)

    @unittest.skipUnless(PILOT.LANGCHAIN_AVAILABLE,
                         "tool_node builds LangChain messages")
    def test_a_repeat_ends_the_turn_with_the_answer(self):
        """A refusal the model ignores must not become another round.

        Telling a 1B model "you already ran that" did not stop it: observed as
        six rounds ending in a LangGraph recursion stack trace, seven seconds
        in, with the weather sitting unused in the conversation the whole time.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        state = {"messages": [
            HumanMessage(content="weather in mumbai"),
            AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
            HumanMessage(content="Observation: Mumbai: Patchy rain, +29 C"),
            AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
        ]}
        result = PILOT.tool_node(state)
        self.assertEqual(result.get("next_step"), "end",
                         "the repeat did not end the turn")
        self.assertIn("29", result["messages"][-1].content,
                      "the observation was not handed back as the answer")

    def test_the_last_observation_is_recoverable(self):
        from langchain_core.messages import AIMessage, HumanMessage

        messages = [
            HumanMessage(content="weather in mumbai"),
            HumanMessage(content="Observation: Mumbai: Patchy rain, +29 C"),
            AIMessage(content="Action: gathm\nAction Input: weather mumbai"),
        ]
        self.assertEqual(PILOT._last_observation(messages),
                         "Mumbai: Patchy rain, +29 C")
        self.assertEqual(PILOT._last_observation([]), "")

    def test_a_recursion_limit_is_recognised_however_it_is_spelled(self):
        """The exception class has moved between langgraph versions."""
        self.assertTrue(PILOT._is_recursion_limit(
            Exception("Recursion limit of 6 reached without hitting a stop condition")))
        self.assertTrue(PILOT._is_recursion_limit(
            RuntimeError("GRAPH_RECURSION_LIMIT")))
        self.assertFalse(PILOT._is_recursion_limit(
            Exception("Connection refused")))

if __name__ == "__main__":
    unittest.main()
