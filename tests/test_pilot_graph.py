"""Exercise the compiled graph so edge mistakes cannot pass helper-only tests."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('graph_pilot', ROOT / 'pilot/main.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@unittest.skipUnless(pilot.LANGCHAIN_AVAILABLE, 'LangGraph required')
class PilotGraphTests(unittest.TestCase):
    def run_graph(self, replies, history=None, result='Mumbai 29C'):
        from langchain_core.messages import AIMessage, HumanMessage
        with patch.object(pilot, '_build_llm', return_value=object()), \
             patch.object(pilot, 'current_connectivity', return_value='online'), \
             patch.object(pilot, '_invoke_spoken', side_effect=[AIMessage(content=r) for r in replies]) as model, \
             patch.object(pilot, 'run_gathm_tool_raw', return_value=result) as tool, \
             patch.object(pilot, 'print_tool_exec'):
            events = list(pilot.app.stream({'messages': history or [HumanMessage(content='Weather in Mumbai?')]},
                                          config={'recursion_limit': pilot.AGENT_MAX_STEPS}))
        return events, model, tool

    def test_tool_returns_to_model_for_final_answer(self):
        events, model, tool = self.run_graph(['Action: gathm\nAction Input: weather Mumbai', 'It is 29C in Mumbai.'])
        self.assertEqual([list(e)[0] for e in events], ['agent', 'action', 'agent'])
        self.assertEqual(events[-1]['agent']['next_step'], 'end')
        self.assertEqual(model.call_count, 2)
        tool.assert_called_once_with('weather Mumbai')

    def test_repeat_ends_graph_with_existing_result(self):
        call = 'Action: gathm\nAction Input: weather Mumbai'
        events, model, tool = self.run_graph([call, call])
        self.assertEqual([list(e)[0] for e in events], ['agent', 'action', 'agent', 'action'])
        self.assertEqual(events[-1]['action']['next_step'], 'end')
        self.assertEqual(events[-1]['action']['messages'][0].content, 'Mumbai 29C')
        self.assertEqual(tool.call_count, 1)

    def test_a_new_turn_can_repeat_a_previous_turns_command(self):
        from langchain_core.messages import AIMessage, HumanMessage
        call = 'Action: gathm\nAction Input: weather Mumbai'
        history = [HumanMessage(content='Weather in Mumbai?'), AIMessage(content=call),
                   HumanMessage(content='Observation: Old weather'), AIMessage(content='Old weather'),
                   HumanMessage(content='Refresh the weather in Mumbai')]
        self.assertEqual(pilot._last_observation(history), '')
        events, model, tool = self.run_graph([call, 'Updated weather.'], history)
        tool.assert_called_once_with('weather Mumbai')
        self.assertEqual(events[-1]['agent']['messages'][0].content, 'Updated weather.')

    def test_repeat_uses_its_own_result_not_another_tools(self):
        from langchain_core.messages import AIMessage, HumanMessage
        messages = [HumanMessage(content='Compare two cities'),
                    AIMessage(content='Action: gathm\nAction Input: weather Mumbai'),
                    HumanMessage(content='Observation: Mumbai 29C'),
                    AIMessage(content='Action: gathm\nAction Input: weather Delhi'),
                    HumanMessage(content='Observation: Delhi 35C'),
                    AIMessage(content='Action: gathm\nAction Input: weather Mumbai')]
        answer = pilot.tool_node({'messages': messages})
        self.assertEqual(answer['messages'][0].content, 'Mumbai 29C')

    def test_forecast_question_requests_full_forecast(self):
        from langchain_core.messages import HumanMessage
        _, _, tool = self.run_graph(['Action: gathm\nAction Input: weather Mumbai i', 'Tomorrow will be rainy.'],
                                    [HumanMessage(content='Weather in Mumbai tomorrow in Fahrenheit?')])
        tool.assert_called_once_with('weather -f Mumbai i')

    def test_system_commands_retain_shell_expansion_and_operators(self):
        from langchain_core.messages import HumanMessage
        for command in ('system ls ~/Desktop', 'system printf hello | wc -c',
                        'system printf hello > /tmp/output.txt'):
            with self.subTest(command=command):
                _, _, tool = self.run_graph(['Action: gathm\nAction Input: ' + command, 'Done.'],
                                            [HumanMessage(content='Run this command on my system')])
                tool.assert_called_once_with(command)

    def test_context_errors_are_not_assumed_from_any_http_400(self):
        with patch.object(pilot, 'LLM_BACKEND', 'llamacpp'):
            self.assertIn('context window', pilot.describe_agent_failure(RuntimeError('context length exceeded')))
            self.assertEqual(pilot.describe_agent_failure(RuntimeError('HTTP 400 Bad Request')),
                             'agent error: HTTP 400 Bad Request')
            self.assertIn('step limit', pilot.describe_agent_failure(RuntimeError('recursion limit reached')))


if __name__ == '__main__':
    unittest.main()
