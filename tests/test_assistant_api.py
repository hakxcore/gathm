"""Conversation failures never become keyword-triggered tool requests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:
    httpx = None

from api import server


@unittest.skipUnless(server.HAS_FASTAPI and httpx, "API test dependencies unavailable")
class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        auth = patch.multiple(server, AUTH_ENABLED=False, TOKEN_MAP={})
        auth.start()
        self.addCleanup(auth.stop)
        server._rate_windows.clear()

    def request(self, path, body):
        async def send():
            transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 12345))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.post(path, json=body)
        return asyncio.run(send())

    def test_conversation_and_history_reach_model_unchanged(self):
        body = {
            "query": "Help me write a story about the weather.",
            "history": [{"role": "user", "content": "Keep it gentle."},
                        {"role": "assistant", "content": "Let's begin."}],
        }
        reply = {"reply": "Rain tapped softly at the window.",
                 "backend": "llamacpp", "model": "local-model"}
        result = subprocess.CompletedProcess([], 0, json.dumps(reply), "")
        with patch.object(server, "_pilot_python", return_value="python"), \
             patch.object(server.subprocess, "run", return_value=result) as run, \
             patch.object(server, "run_agent_command", new_callable=AsyncMock) as router:
            response = self.request("/api/v1/agent/chat", body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), reply)
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), body)
        router.assert_not_called()

    def test_model_error_does_not_route_tool_keywords(self):
        error = {"error": "LLM runtime not available"}
        result = subprocess.CompletedProcess([], 2, json.dumps(error), "")
        with patch.object(server.subprocess, "run", return_value=result), \
             patch.object(server, "run_agent_command", new_callable=AsyncMock) as router, \
             patch.object(server, "execute_tool", new_callable=AsyncMock) as tool:
            response = self.request("/api/v1/agent/chat", {
                "query": "Write a story about a weather forecast and a password.",
            })
        self.assertEqual(response.json(), error)
        router.assert_not_called()
        tool.assert_not_called()

    def test_missing_assistant_remains_an_error(self):
        with patch.object(server, "CHAT_SCRIPT") as script, \
             patch.object(server.subprocess, "run") as run, \
             patch.object(server, "run_agent_command", new_callable=AsyncMock) as router:
            script.exists.return_value = False
            response = self.request("/api/v1/agent/chat", {"query": "Plan my morning."})
        self.assertIn("error", response.json())
        self.assertNotIn("reply", response.json())
        run.assert_not_called()
        router.assert_not_called()

    def test_timeout_does_not_retry_or_execute_tools(self):
        with patch.object(server.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(["python"], 600)) as run, \
             patch.object(server, "run_agent_command", new_callable=AsyncMock) as router, \
             patch.object(server, "execute_tool", new_callable=AsyncMock) as tool:
            response = self.request("/api/v1/agent/chat", {"query": "Explain how DNS works."})
        self.assertIn("did not finish", response.json()["error"])
        run.assert_called_once()
        router.assert_not_called()
        tool.assert_not_called()

    def test_explicit_tool_execution_remains_available(self):
        result = {"status": "success", "output": "example.com", "exit_code": 0}
        with patch.object(server, "execute_tool", new_callable=AsyncMock,
                          return_value=result) as tool, \
             patch.object(server, "run_chat_agent") as chat:
            response = self.request("/api/v1/tools/dns/execute", {"args": ["example.com"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        tool.assert_awaited_once_with("dns", ["example.com"], 120)
        chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
