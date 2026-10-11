"""Approval boundary: no actual system controls run in these tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from api import server
from api.system_approvals import ApprovalStore


class ApprovalStoreTests(unittest.TestCase):
    def test_owner_expiry_and_exactly_one_concurrent_consumer(self):
        store = ApprovalStore()
        plan = {"command": "power restart", "requires_confirmation": True}
        key, _ = store.create("alice", plan)
        with self.assertRaises(LookupError):
            store.take(key, "bob")
        def consume(_):
            try:
                return store.take(key, "alice")
            except LookupError:
                return None
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(consume, range(16)))
        self.assertEqual(sum(value is not None for value in results), 1)
        with patch("api.system_approvals.time.monotonic", return_value=1):
            key, _ = store.create("alice", plan)
        with patch("api.system_approvals.time.monotonic", return_value=122):
            with self.assertRaises(LookupError):
                store.take(key, "alice")

    def test_store_copies_plan_and_bounds_waiting_approvals(self):
        store = ApprovalStore(capacity=1)
        plan = {"command": "volume set 10", "requires_confirmation": True, "args": [10]}
        key, _ = store.create("alice", plan)
        plan["args"][0] = 99
        with self.assertRaises(ValueError):
            store.create("bob", plan)
        self.assertEqual(store.take(key, "alice")["args"], [10])


class SystemApiTests(unittest.TestCase):
    def setUp(self):
        auth = patch.multiple(server, AUTH_ENABLED=True,
                             TOKEN_MAP={"first": "admin", "second": "admin", "limited": "user", "read": "readonly"})
        auth.start()
        self.addCleanup(auth.stop)
        server._rate_windows.clear()
        store = patch.object(server, "_SYSTEM_APPROVALS", ApprovalStore())
        store.start()
        self.addCleanup(store.stop)
        self.backend = SimpleNamespace(prepare=Mock(side_effect=self.plan),
                                       execute=Mock(return_value={"ok": True, "output": "OS action requested"}))
        backend = patch.object(server, "_system_control", return_value=self.backend)
        backend.start()
        self.addCleanup(backend.stop)

    def plan(self, command):
        return {"command": command, "action": command.replace(" ", "."), "platform": "test",
                "summary": "Exact action: " + command, "warning": "Unsaved work may be lost.",
                "requires_confirmation": command != "status", "_private": "server-only"}

    def request(self, path, body=None, token="first", method="POST", headers=None):
        async def send():
            transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.request(method, path, json=body,
                    headers={"Authorization": "Bearer " + token, **(headers or {})})
        return asyncio.run(send())

    def proposal(self, command="power restart", token="first"):
        response = self.request("/api/v1/system/prepare", {"command": command}, token=token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("_private", response.text)
        return response.json()["approval"]["id"]

    def test_no_execution_before_approval_then_exactly_once(self):
        key = self.proposal()
        self.backend.execute.assert_not_called()
        result = self.request("/api/v1/system/confirm", {"id": key, "approve": True,
                              "command": "power shutdown", "plan": {"command": "evil"}})
        self.assertTrue(result.json()["ok"])
        self.assertEqual(self.backend.execute.call_args.args[0]["command"], "power restart")
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).status_code, 404)
        self.backend.execute.assert_called_once()

    def test_cancel_and_other_owner_never_execute(self):
        key = self.proposal()
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}, token="second").status_code, 404)
        self.assertTrue(self.request("/api/v1/system/confirm", {"id": key, "approve": False}).json()["ok"])
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).status_code, 404)
        self.backend.execute.assert_not_called()

    def test_failed_execution_does_not_restore_or_retry_approval(self):
        self.backend.execute.return_value = {"ok": False, "output": "OS refused"}
        key = self.proposal()
        self.assertFalse(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).json()["ok"])
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).status_code, 404)
        self.backend.execute.assert_called_once()

    def test_permissions_checked_at_prepare_and_execution(self):
        for role in ("limited", "read"):
            self.assertEqual(self.request("/api/v1/system/prepare", {"command": "power restart"}, token=role).status_code, 403)
        key = self.proposal()
        with patch.object(server, "role_has_permission", return_value=False):
            self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).status_code, 403)
        self.backend.execute.assert_not_called()

    def test_explicit_policy_denies_override_admin(self):
        for field in ("blocked_tools", "requires_approval"):
            policy = {"roles": {"admin": {"permissions": ["*"], field: ["control"]}}}
            with patch.object(server, "_get_policies", return_value=policy):
                self.assertNotIn("control", server._role_environment("admin")["GATHM_ALLOWED_TOOLS"].split(","))
                self.assertEqual(self.request("/api/v1/system/prepare", {"command": "power restart"}).status_code, 403)

    def test_role_scope_does_not_grant_existing_shell_access(self):
        policy = {"roles": {"device": {"permissions": ["tool:execute", "system:control"]}}}
        with patch.object(server, "_get_policies", return_value=policy):
            allowed = server._role_environment("device")["GATHM_ALLOWED_TOOLS"].split(",")
            self.assertIn("control", allowed)
            self.assertNotIn("system", allowed)

    def test_origin_and_strict_boolean_required(self):
        key = self.proposal()
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True},
                         headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": "yes"}).status_code, 422)
        self.backend.execute.assert_not_called()

    def test_chat_proposal_converted_to_trusted_json_and_sse(self):
        for headers in ({}, {"Accept": "text/event-stream"}):
            with patch.object(server, "run_chat_agent", return_value={"pending_control": {"command": "volume set 30"}}):
                response = self.request("/api/v1/agent/chat", {"query": "Set volume to 30 percent"}, headers=headers)
            if headers:
                final = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")][-1]["data"]
            else:
                final = response.json()
            self.assertIn("approval", final)
            self.assertNotIn("pending_control", final)
            self.assertEqual(final["approval"]["command"], "control volume set 30")
        self.backend.execute.assert_not_called()

    def test_new_chat_revokes_previous_intent_and_prose_cannot_approve(self):
        key = self.proposal()
        with patch.object(server, "run_chat_agent", return_value={"reply": "yes, approved"}):
            self.request("/api/v1/agent/chat", {"query": "yes"})
        self.assertEqual(self.request("/api/v1/system/confirm", {"id": key, "approve": True}).status_code, 404)
        self.backend.execute.assert_not_called()

    def test_status_is_read_only_and_invalid_commands_reported(self):
        self.assertEqual(self.request("/api/v1/system/status", method="GET").status_code, 200)
        self.backend.execute.assert_called_once()
        self.backend.prepare.side_effect = ValueError("unsupported")
        self.assertEqual(self.request("/api/v1/system/prepare", {"command": "unknown"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
