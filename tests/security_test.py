#!/usr/bin/env python3
"""Security boundaries; mocked tool work, no external services or model needed."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from api import server
from lib import sysexec
from pilot import browser


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.auth = patch.multiple(server, AUTH_ENABLED=False, TOKEN_MAP={})
        self.auth.start()
        server._rate_windows.clear()
        server._job_store.clear()

    def tearDown(self):
        self.auth.stop()
        server._job_store.clear()

    def request(self, method, path, *, peer="127.0.0.1", host="localhost", **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=server.app, client=(peer, 12345))
            async with httpx.AsyncClient(transport=transport, base_url="http://" + host) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def enable_auth(self):
        server.AUTH_ENABLED = True
        server.TOKEN_MAP = {"a": "user", "b": "user", "agent": "agent", "root": "admin", "read": "readonly"}

    def test_cross_origin_and_rebinding_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("GET", "/api/v1/ping", headers=headers).status_code, 403)
        self.assertEqual(self.request("GET", "/api/v1/tools", host="evil.example").status_code, 403)
        self.assertEqual(self.request("GET", "/api/v1/tools", peer="192.0.2.1").status_code, 403)
        response = self.request("GET", "/api/v1/ping", headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_authenticated_remote_and_health_permissions(self):
        self.enable_auth()
        self.assertEqual(self.request("GET", "/api/v1/health").status_code, 401)
        self.assertEqual(self.request("GET", "/api/v1/tools", peer="192.0.2.1",
                                     headers={"Authorization": "Bearer a"}).status_code, 200)
        self.assertEqual(self.request("GET", "/api/v1/health",
                                     headers={"Authorization": "Bearer a"}).status_code, 403)

    def test_empty_token_config_fails_closed(self):
        for value in (":admin", "a:", "admin"):
            with patch.dict(os.environ, {"GATHM_API_KEYS": value}):
                with self.assertRaises(ValueError):
                    server._build_token_map()

    def test_invalid_job_kind_and_timeouts(self):
        for body in ({"kind": "new-tool", "tool": "evil"},
                     {"tool": "weather", "timeout": -1}, {"tool": "weather", "timeout": 999999}):
            self.assertEqual(self.request("POST", "/api/v1/jobs", json=body).status_code, 422)

    def test_engineering_and_heal_need_admin(self):
        self.enable_auth()
        for path, body in (("/api/v1/agent/engineer", {"task": "x"}),
                           ("/api/v1/agent/heal", {"tool": "all"}),
                           ("/api/v1/jobs", {"kind": "engineer", "tool": "x"})):
            response = self.request("POST", path, json=body, headers={"Authorization": "Bearer a"})
            self.assertEqual(response.status_code, 403, response.text)

    def test_chat_and_chain_inherit_scoped_permissions(self):
        self.enable_auth()
        def chat(*args):
            env = server._child_env()
            allowed = env["GATHM_ALLOWED_TOOLS"].split(",")
            self.assertNotIn("crypt", allowed)
            self.assertNotIn("system", allowed)
            self.assertNotIn("browser", allowed)
            self.assertNotIn("GATHM_API_KEY", env)
            self.assertEqual(env["GATHM_ALLOW_SHELL"], "0")
            return {"reply": "checked"}
        async def run(*args, **kwargs):
            chat()
            return {"output": "{}"}
        with patch.object(server, "run_chat_agent", chat), patch.object(server, "_run_subprocess", run):
            for path, body in (("chat", {"query": "x"}), ("chain", {"pipeline": "crypt"}),
                               ("parallel", {"tools": "crypt"}), ("ask", {"query": "x"})):
                self.assertEqual(self.request("POST", "/api/v1/agent/" + path, json=body,
                    headers={"Authorization": "Bearer agent"}).status_code, 200)
        self.assertIsNone(server._EXECUTION_ENV.get())

    def test_job_ownership_all_routes(self):
        import hashlib
        self.enable_auth()
        owner = hashlib.sha256(b"Bearer a").hexdigest()
        job = server.Job("job1", "tool", "weather", [], 10, owner=owner,
                         status=server.JobStatus.completed, output_lines=["private"])
        server._job_store[job.id] = job
        for token in ("b", "read"):
            headers = {"Authorization": "Bearer " + token}
            response = self.request("GET", "/api/v1/jobs", headers=headers)
            self.assertEqual(response.json()["count"], 0)
            for suffix in ("", "/stream"):
                self.assertEqual(self.request("GET", "/api/v1/jobs/job1" + suffix, headers=headers).status_code, 404)
        self.assertEqual(self.request("DELETE", "/api/v1/jobs/job1", headers={"Authorization": "Bearer b"}).status_code, 404)
        for token in ("a", "root"):
            self.assertEqual(self.request("GET", "/api/v1/jobs/job1", headers={"Authorization": "Bearer " + token}).status_code, 200)

    def test_upload_limit_before_json_parser(self):
        response = self.request("POST", "/api/v1/agent/chat", content=b"x" * (1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)

    def test_shell_approval_bypasses(self):
        commands = ["python /tmp/payload.py", "node /tmp/payload.js", "find . -delete",
                    "awk 'BEGIN {system(\"true\")}'", "sed -i x file", "sort -o file file",
                    "sysctl kern.value=1", "env /tmp/ls", "nohup ls", "git -c alias.x=foo x",
                    "date 010100002025", "hostname changed", "printf -v PATH x"]
        for command in commands:
            self.assertNotEqual(sysexec.classify(command, "posix")[0], "safe", command)
        for command in ["Get-Evil", "Get-Process (Write-Output x)", "Get-Process {x}",
                        "sc query spooler", r"C:\tmp\ipconfig.exe /all", "ipconfig /release"]:
            self.assertNotEqual(sysexec.classify(command, "windows")[0], "safe", command)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"GATHM_ALLOW_SHELL": "1"}), \
             patch.object(sysexec, "CONFIG_DIR", Path(temp)), patch.object(sysexec, "AUDIT_LOG", Path(temp) / "log"), \
             patch.object(sysexec.subprocess, "run") as spawn:
            self.assertFalse(sysexec.run("python /tmp/payload.py")[0])
            spawn.assert_not_called()

    def test_fallback_and_path_checks_at_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "HOME": temp, "GATHM_ALLOWED_TOOLS": "weather"}
            code = '''source "$1/lib/recovery.bash"
log_error() { :; }; log_warn() { :; }; log_info() { :; }
cb_allow_request() { return 1; }; get_fallback_tool() { echo crypt; }
execute_with_recovery weather
'''
            result = subprocess.run(["bash", "-c", code, "test", str(ROOT)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tool_access_denied", result.stderr)
            for name in ("../crypt", "bad\"name", "crypt"):
                result = subprocess.run(["bash", "-c", 'source "$1/lib/recovery.bash"; gathm_tool_allowed "$2"',
                                         "test", str(ROOT), name], env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)

    def test_browser_rejects_local_and_executable_urls(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "https://x\nfoo"):
            for function in (browser.open_url, browser.navigate, browser.fetch_page, browser.screenshot):
                self.assertIn("Only HTTP", function(url))

    def test_pilot_dispatch_checks_policy_before_work(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pilot_security", ROOT / "pilot/main.py")
        pilot = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "pilot"))
        spec.loader.exec_module(pilot)
        with patch.dict(os.environ, {"GATHM_ALLOWED_TOOLS": "weather"}):
            for command in ("system echo blocked", "browser open https://example.com",
                            "crypt -v", "../weather -v"):
                self.assertIn("access denied", pilot.run_gathm_tool_raw(command))

    def test_readonly_cannot_run_speech(self):
        self.enable_auth()
        for endpoint, body in (("speech", {"text": "hello"}), ("transcribe", {})):
            self.assertEqual(self.request("POST", "/api/v1/" + endpoint, json=body,
                             headers={"Authorization": "Bearer read"}).status_code, 403)

    def test_job_captures_request_permissions(self):
        async def check():
            async def no_work(job):
                return None
            with patch.object(server, "_run_job_task", no_work):
                token = server._EXECUTION_ENV.set({"GATHM_ALLOWED_TOOLS": "weather"})
                try:
                    job = server._create_job("tool", "weather", [], 10, owner="principal")
                finally:
                    server._EXECUTION_ENV.reset(token)
                await job._task
                self.assertEqual(job.owner, "principal")
                self.assertEqual(job.execution_env["GATHM_ALLOWED_TOOLS"], "weather")
        asyncio.run(check())

    def test_explicit_denies_override_builtin_capabilities(self):
        policy = {"roles": {"custom": {"permissions": ["tool:execute", "system:execute", "browser:execute"],
                  "blocked_tools": ["system"], "requires_approval": ["browser"]}}}
        with patch.object(server, "_get_policies", return_value=policy):
            allowed = server._role_environment("custom")["GATHM_ALLOWED_TOOLS"].split(",")
            self.assertNotIn("system", allowed)
            self.assertNotIn("browser", allowed)
            self.assertEqual(server._check_tool_access("custom", "system").status_code, 403)

    def test_blank_job_output_counts_towards_limit(self):
        async def check(script, directory):
            with patch.object(server, "BASH_CMD", sys.executable), patch.object(server, "AGENT_SCRIPT", script), \
                 patch.object(server, "JOBS_DIR", directory), patch.object(server, "MAX_OUTPUT_BYTES", 1024):
                job = server.Job("blank", "tool", "weather", [], 5)
                await asyncio.wait_for(server._run_job_task(job), timeout=10)
                self.assertEqual(job.status, server.JobStatus.failed)
                self.assertIn("output limit", job.error)
                self.assertLessEqual(len(job.output_lines), 1024)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            script = directory / "writer.py"
            for stream in ("stdout", "stderr"):
                script.write_text("import sys; sys." + stream + ".write(' \\n' * 4096)")
                with self.subTest(stream=stream):
                    asyncio.run(check(script, directory / "jobs"))

    def test_subprocess_output_is_bounded(self):
        async def check():
            result = await asyncio.wait_for(server._run_subprocess(
                [sys.executable, "-c", "import sys; sys.stdout.write('x'*2000000)"], 5), timeout=10)
            self.assertEqual(result["status"], "error")
            self.assertIn("output limit", result["error"])
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main(verbosity=2)
