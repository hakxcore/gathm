#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gathm Enterprise - REST API Server (FastAPI)
Exposes all Gathm tools via HTTP endpoints for programmatic access.
Cross-platform: Linux (all distros), macOS, Termux, Windows (WSL/Git Bash/MSYS2)

Usage:
    python3 api/server.py [--port 8080] [--host 0.0.0.0]
    uvicorn api.server:app --port 8080

Endpoints:
    GET  /api/v1/tools                  - List all tools
    GET  /api/v1/tools/{name}           - Get tool metadata
    POST /api/v1/tools/{name}/execute   - Execute a tool
    GET  /api/v1/health                 - System health check (authorized)
    GET  /api/v1/health/{tool}          - Tool health check
    POST /api/v1/agent/ask              - Natural language query
    POST /api/v1/agent/plan             - Create execution plan
    POST /api/v1/agent/engineer         - Engineering agent task
    POST /api/v1/agent/chain            - Execute tool pipeline
    POST /api/v1/agent/parallel         - Execute tools in parallel
    GET  /api/v1/agent/status           - Agent status
    POST /api/v1/agent/heal             - Self-heal tools
    POST /api/v1/jobs                   - Submit async job (returns 202 + job_id)
    GET  /api/v1/jobs                   - List all jobs
    GET  /api/v1/jobs/{id}              - Poll job status + output
    GET  /api/v1/jobs/{id}/stream       - Stream live output via SSE
    DELETE /api/v1/jobs/{id}            - Cancel a job
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from contextvars import ContextVar
from urllib.parse import urlsplit
import json
import os
import platform
import secrets
import signal
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Literal, Union

try:
    from fastapi import FastAPI, HTTPException, Request, Response, status
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    def Field(default=None, **kwargs):
        return default
    class StaticFiles:
        def __init__(self, *args, **kwargs): pass
    status = None
    class FastAPI:
        def __init__(self, *args, **kwargs): pass
        def add_middleware(self, *args, **kwargs): pass
        def middleware(self, *args, **kwargs):
            return lambda f: f
        def get(self, *args, **kwargs):
            return lambda f: f
        def post(self, *args, **kwargs):
            return lambda f: f
        def delete(self, *args, **kwargs):
            return lambda f: f
        def mount(self, *args, **kwargs): pass
    class BaseModel:
        def __init__(self, *args, **kwargs): pass
    class Request: pass
    class Response: pass
    class JSONResponse: pass
    class FileResponse: pass
    class StreamingResponse: pass
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = None, headers: dict = None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GATHM_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = GATHM_ROOT / "tools"
GUI_DIR = GATHM_ROOT / "gui"
AGENT_SCRIPT = GATHM_ROOT / "agent" / "orchestrator.sh"
POLICIES_FILE = GATHM_ROOT / "config" / "policies.yaml"

DEFAULT_PORT = int(os.environ.get("GATHM_PORT", 8080))
DEFAULT_HOST = os.environ.get("GATHM_HOST", "127.0.0.1")
PILOT_DIR = GATHM_ROOT / "pilot"
CHAT_SCRIPT = PILOT_DIR / "chat_once.py"
API_VERSION = "3.0.0"

# ---------------------------------------------------------------------------
# Bash detection (cross-platform)
# ---------------------------------------------------------------------------

def _find_bash() -> str:
    bash = shutil.which("bash")
    if bash:
        return bash
    if platform.system() == "Windows":
        for candidate in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\msys64\usr\bin\bash.exe",
            r"C:\Windows\System32\bash.exe",
        ]:
            if os.path.isfile(candidate):
                return candidate
    return "bash"

BASH_CMD = _find_bash()

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        if HAS_YAML:
            return yaml.safe_load(f) or {}
        result: dict = {}
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                key, _, value = line.partition(":")
                value = value.strip().strip('"').strip("'")
                if value:
                    result[key.strip()] = value
        return result

# ---------------------------------------------------------------------------
# Policies / RBAC
# ---------------------------------------------------------------------------

_policies: dict = {}
_policies_mtime: float = 0.0

def _get_policies() -> dict:
    global _policies, _policies_mtime
    try:
        mtime = POLICIES_FILE.stat().st_mtime
    except OSError:
        return _policies
    if mtime != _policies_mtime:
        _policies = _load_yaml(POLICIES_FILE)
        _policies_mtime = mtime
    return _policies

# token → role mapping via GATHM_API_KEYS env var
# Format: "token1:role1,token2:role2"  e.g. "secret123:admin,readonly-key:readonly"
# If GATHM_API_KEY (legacy single key) is set alone, it maps to "admin" role.
def _build_token_map() -> dict[str, str]:
    token_map: dict[str, str] = {}
    multi = os.environ.get("GATHM_API_KEYS", "")
    if multi:
        for pair in multi.split(","):
            pair = pair.strip()
            if ":" in pair:
                tok, role = pair.split(":", 1)
                if not tok.strip() or not role.strip():
                    raise ValueError("GATHM_API_KEYS entries require a nonempty token and role")
                token_map[tok.strip()] = role.strip()
            else:
                raise ValueError("GATHM_API_KEYS entries must use token:role")
    legacy = os.environ.get("GATHM_API_KEY", "")
    if legacy and legacy not in token_map:
        token_map[legacy] = "admin"
    return token_map

TOKEN_MAP: dict[str, str] = _build_token_map()
AUTH_ENABLED = bool(TOKEN_MAP)
_EXECUTION_ENV: ContextVar = ContextVar("gathm_execution_env", default=None)

# Public paths that skip auth entirely.
# GUI static files are also public (any path not starting with /api/).
PUBLIC_PATHS = {"/", "/api", "/api/v1", "/api/v1/ping"}

def resolve_role(request: Request) -> str | None:
    """Return the role for the request's bearer token, or None if unauthenticated."""
    if not AUTH_ENABLED:
        return "admin"
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    for stored_token, role in TOKEN_MAP.items():
        if secrets.compare_digest(token, stored_token):
            return role
    return None

def role_has_permission(role: str, permission: str) -> bool:
    policies = _get_policies()
    roles_cfg = policies.get("roles", {})
    role_cfg = roles_cfg.get(role, {})
    perms = role_cfg.get("permissions", [])
    return "*" in perms or permission in perms

def role_rate_limit(role: str) -> int:
    """Returns requests-per-minute limit; 0 means unlimited."""
    policies = _get_policies()
    roles_cfg = policies.get("roles", {})
    role_cfg = roles_cfg.get(role, {})
    return int(role_cfg.get("rate_limit", 60))

def tool_rate_limit(tool_name: str) -> int | None:
    """Returns per-tool override limit if configured."""
    policies = _get_policies()
    per_tool = policies.get("rate_limiting", {}).get("per_tool_limits", {})
    val = per_tool.get(tool_name)
    return int(val) if val is not None else None

def role_blocked_tools(role: str) -> list[str]:
    policies = _get_policies()
    roles_cfg = policies.get("roles", {})
    return list(roles_cfg.get(role, {}).get("blocked_tools", []))

def role_requires_approval(role: str) -> list[str]:
    policies = _get_policies()
    roles_cfg = policies.get("roles", {})
    return list(roles_cfg.get(role, {}).get("requires_approval", []))

# ---------------------------------------------------------------------------
# In-process rate limiter (sliding window per token/IP)
# ---------------------------------------------------------------------------

_rate_windows: dict[str, list[float]] = {}

def check_rate_limit(key: str, limit: int) -> bool:
    """Returns True if allowed, False if rate-limited. limit=0 means unlimited."""
    if limit == 0:
        return True
    now = time.monotonic()
    window = _rate_windows.setdefault(key, [])
    # Evict entries older than 60 s
    cutoff = now - 60.0
    _rate_windows[key] = [t for t in window if t > cutoff]
    if len(_rate_windows[key]) >= limit:
        return False
    _rate_windows[key].append(now)
    return True

# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def valid_tool_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name))


def load_tool_manifest(tool_name: str) -> dict:
    if not valid_tool_name(tool_name):
        return {}
    return _load_yaml(TOOLS_DIR / tool_name / "tool.yaml")

def list_tools() -> list[dict]:
    tools = []
    for tool_dir in sorted(TOOLS_DIR.iterdir()):
        if tool_dir.is_dir() and (tool_dir / tool_dir.name).exists():
            m = load_tool_manifest(tool_dir.name)
            tools.append({
                "name": tool_dir.name,
                "description": m.get("description", "No description"),
                "version": m.get("version", "unknown"),
                "category": m.get("category", "unknown"),
                "tags": m.get("tags", []),
            })
    return tools

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mKJHABCDFG]')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _kill_process(proc) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


MAX_OUTPUT_BYTES = 1024 * 1024
MAX_OUTPUT_LINES = 10000


async def _bounded_read(stream) -> bytes:
    data = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(data)
        if len(data) + len(chunk) > MAX_OUTPUT_BYTES:
            raise ValueError("Process output limit exceeded")
        data.extend(chunk)


async def _run_subprocess(cmd: list[str], timeout: int, extra_env: dict | None = None) -> dict:
    env = _child_env()
    if extra_env:
        env.update(extra_env)

    start_time = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=(os.name == "posix"),
        )
        try:
            async def collect():
                readers = [asyncio.create_task(_bounded_read(proc.stdout)),
                           asyncio.create_task(_bounded_read(proc.stderr))]
                try:
                    stdout, stderr = await asyncio.gather(*readers)
                    await proc.wait()
                    return stdout, stderr
                finally:
                    for reader in readers:
                        reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
            stdout, stderr = await asyncio.wait_for(collect(), timeout=timeout)
        except (asyncio.TimeoutError, ValueError) as exc:
            _kill_process(proc)
            await proc.communicate()
            return {"status": "error", "exit_code": -1, "output": "",
                    "error": str(exc) or f"Timed out after {timeout}s", "duration_ms": timeout * 1000}
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "status": "success" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "output": _strip_ansi(stdout.decode(errors="replace").strip()),
            "error": _strip_ansi(stderr.decode(errors="replace").strip()) if proc.returncode != 0 else "",
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        return {"status": "error", "exit_code": -1, "output": "", "error": str(exc)}

async def execute_tool(tool_name: str, args: list[str], timeout: int = 120) -> dict:
    start = time.monotonic()
    cmd = [BASH_CMD, str(AGENT_SCRIPT), "run", tool_name] + args
    result = await _run_subprocess(cmd, timeout)
    result["tool"] = tool_name
    result.setdefault("duration_ms", int((time.monotonic() - start) * 1000))
    return result

def _pilot_python() -> str:
    """Return the Pilot venv's Python (which has langchain), else fall back."""
    candidates = [
        PILOT_DIR / "venv" / "bin" / "python3",
        PILOT_DIR / "venv" / "bin" / "python",
        PILOT_DIR / "venv" / "Scripts" / "python.exe",  # Windows
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable  # last resort (may lack langchain → handled gracefully)

# One agent turn on a phone is slow: CPU-only inference, a long tool-routing
# prompt, and LangGraph on top. 180s was not enough — the GUI reported "agent
# timed out" on a Termux device that was still working. This is only a ceiling,
# so a generous value costs nothing when the reply is fast.
CHAT_TIMEOUT = int(os.environ.get("GATHM_CHAT_TIMEOUT", "600"))

# Ceiling on an uploaded recording. 16 kHz mono 16-bit WAV is ~32 KB/s, so 25 MB
# is over ten minutes of speech — far more than a spoken query, and short of
# anything that would exhaust memory on a phone.
MAX_AUDIO_BYTES = int(os.environ.get("GATHM_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))


_SPEECH_MODULE: Any = None
_SPEECH_TRIED = False


def _speech():
    """Import lib/speech.py lazily, or None when it is unusable.

    Imported on demand rather than at module scope because `python3
    api/server.py` puts api/ on sys.path[0], not the repo root — the same
    reason the GUI's chat route needed the path fix in main().
    """
    global _SPEECH_MODULE, _SPEECH_TRIED
    if _SPEECH_TRIED:
        return _SPEECH_MODULE
    _SPEECH_TRIED = True
    root = str(GATHM_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from lib import speech as _mod  # noqa: PLC0415
        _SPEECH_MODULE = _mod
    except Exception:  # noqa: BLE001
        _SPEECH_MODULE = None
    return _SPEECH_MODULE


def run_chat_agent(query: str, history: list = None, timeout: int | None = None) -> dict:
    """Run the real Pilot LLM agent for one turn and return its reply.

    Shells out to pilot/chat_once.py using the Pilot venv's Python so the
    stdlib-only API server stays dependency-free. Returns {"reply": ...} on
    success, or {"error": ...} which the caller can fall back on.
    """
    if timeout is None:
        timeout = CHAT_TIMEOUT

    if not CHAT_SCRIPT.exists():
        return {"error": "chat agent not installed (pilot/chat_once.py missing)"}

    payload = json.dumps({"query": query, "history": history or []})
    try:
        result = subprocess.run(
            [_pilot_python(), str(CHAT_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PILOT_DIR),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"error": (
            f"the agent did not finish within {timeout}s. On a phone this "
            "usually means the model is just slow rather than stuck — try a "
            "shorter question, or raise the ceiling with "
            "GATHM_CHAT_TIMEOUT=<seconds> before starting the server."
        )}
    except Exception as e:
        return {"error": str(e)}

    out = (result.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        tail = (result.stderr or "").strip().splitlines()
        return {"error": "agent returned no parseable response",
                "detail": tail[-1] if tail else out[:200]}

async def run_agent_command(command: str, arg: str = "") -> dict:
    """Run an agent orchestrator command."""
    cmd = [BASH_CMD, str(AGENT_SCRIPT), command]
    if arg:
        cmd += arg.split()
    cmd.append("--json")
    result = await _run_subprocess(cmd, timeout=120)
    raw = result.get("output", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_output": raw, "exit_code": result.get("exit_code", -1)}

# ---------------------------------------------------------------------------
# Async Job Store
# ---------------------------------------------------------------------------

JOBS_DIR = Path.home() / ".gathm" / "jobs"

class JobStatus(str, Enum):
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"
    cancelled = "cancelled"

@dataclass
class Job:
    id: str
    kind: str           # "tool" | "ask" | "plan" | "chain" | "parallel" | "engineer"
    tool: str           # tool name for kind="tool"; agent sub-command otherwise
    args: list[str]
    timeout: int
    owner: str = ""
    execution_env: dict = field(default_factory=dict, repr=False)
    status: JobStatus = JobStatus.pending
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    output_lines: list[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    error: str = ""
    # async-only fields — not persisted
    _task: Any = field(default=None, repr=False)
    _proc: Any = field(default=None, repr=False)
    _subscribers: list = field(default_factory=list, repr=False)

_job_store: dict[str, Job] = {}

def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "tool": job.tool,
        "args": job.args,
        "timeout": job.timeout,
        "status": job.status.value,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "exit_code": job.exit_code,
        "error": job.error,
        "output": "\n".join(job.output_lines),
        "line_count": len(job.output_lines),
    }

async def _persist_job(job: Job) -> None:
    try:
        JOBS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        JOBS_DIR.chmod(0o700)
        path = JOBS_DIR / f"{job.id}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(_job_to_dict(job), handle, indent=2)
    except Exception:
        pass

async def _run_job_task(job: Job) -> None:
    job.status = JobStatus.running
    job.started_at = time.time()
    await _persist_job(job)

    if job.kind == "tool":
        cmd = [BASH_CMD, str(AGENT_SCRIPT), "run", job.tool] + job.args
    else:
        cmd = [BASH_CMD, str(AGENT_SCRIPT), job.kind] + job.args + ["--json"]

    env = _child_env(job.execution_env)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=(os.name == "posix"),
        )
        job._proc = proc

        stderr_lines: list[str] = []
        stderr_bytes = stdout_bytes = 0

        async def _drain_stderr() -> None:
            nonlocal stderr_bytes
            assert proc.stderr
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_bytes += len(line)
                if stderr_bytes > MAX_OUTPUT_BYTES or len(stderr_lines) >= MAX_OUTPUT_LINES:
                    raise ValueError("Process error output limit exceeded")
                stderr_lines.append(line.decode(errors="replace").rstrip())

        async def _drain_stdout() -> None:
            nonlocal stdout_bytes
            assert proc.stdout
            deadline = asyncio.get_event_loop().time() + job.timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    _kill_process(proc)
                    job.status = JobStatus.failed
                    job.error = f"Timed out after {job.timeout}s"
                    break
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    continue
                if not raw:
                    break
                stdout_bytes += len(raw)
                if stdout_bytes > MAX_OUTPUT_BYTES or len(job.output_lines) >= MAX_OUTPUT_LINES:
                    raise ValueError("Process output limit exceeded")
                text = raw.decode(errors="replace").rstrip("\n")
                job.output_lines.append(text)
                event_data = json.dumps({"event": "output", "line": text, "ts": time.time()})
                for q in list(job._subscribers):
                    try:
                        q.put_nowait(event_data)
                    except asyncio.QueueFull:
                        pass

        async def collect():
            readers = [asyncio.create_task(_drain_stdout()), asyncio.create_task(_drain_stderr())]
            try:
                await asyncio.gather(*readers)
                await proc.wait()
            finally:
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
        await asyncio.wait_for(collect(), timeout=job.timeout)
        if job.status == JobStatus.running:
            job.exit_code = proc.returncode
            job.error = "\n".join(stderr_lines) if proc.returncode != 0 else ""
            job.status = JobStatus.completed if proc.returncode == 0 else JobStatus.failed

    except asyncio.CancelledError:
        if job._proc:
            try:
                _kill_process(job._proc)
                await job._proc.communicate()
            except Exception:
                pass
        job.status = JobStatus.cancelled
    except Exception as exc:
        if job._proc:
            _kill_process(job._proc)
            await job._proc.communicate()
        job.status = JobStatus.failed
        job.error = str(exc)
    finally:
        job.completed_at = time.time()
        done_data = json.dumps({
            "event": "done",
            "status": job.status.value,
            "exit_code": job.exit_code,
        })
        for q in list(job._subscribers):
            try:
                q.put_nowait(done_data)
            except asyncio.QueueFull:
                pass
        job._subscribers.clear()
        await _persist_job(job)

def _create_job(kind: str, tool: str, args: list[str], timeout: int, owner: str = "") -> Job:
    if len(_job_store) >= 200:
        finished = [j for j in _job_store.values() if j.status in
                    (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)]
        if not finished:
            raise HTTPException(429, "Job queue is full")
        oldest = min(finished, key=lambda j: j.created_at).id
        del _job_store[oldest]
        (JOBS_DIR / f"{oldest}.json").unlink(missing_ok=True)
    if sum(j.status in (JobStatus.pending, JobStatus.running) for j in _job_store.values()) >= 4:
        raise HTTPException(429, "Too many active jobs")
    job = Job(id=uuid.uuid4().hex, kind=kind, tool=tool, args=args, timeout=timeout,
              owner=owner, execution_env=dict(_EXECUTION_ENV.get() or {}))
    _job_store[job.id] = job
    job._task = asyncio.create_task(_run_job_task(job))
    return job

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

class BodyLimitMiddleware:
    """Bound uploads before JSON parsing, including chunked requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = MAX_AUDIO_BYTES if scope["path"] == "/api/v1/transcribe" else 1024 * 1024
        data = bytearray()
        while True:
            try:
                event = await asyncio.wait_for(receive(), timeout=30)
            except asyncio.TimeoutError:
                return await JSONResponse({"error": "Upload timed out"}, status_code=408)(scope, receive, send)
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            if len(data) + len(chunk) > limit:
                return await JSONResponse({"error": "Request body too large"}, status_code=413)(scope, receive, send)
            data.extend(chunk)
            if not event.get("more_body", False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": bytes(data), "more_body": False}
        await self.app(scope, replay, send)


app = FastAPI(
    title="Gathm Enterprise API",
    version=API_VERSION,
    description="Orchestrate security, networking, and data tools via REST.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(BodyLimitMiddleware)

# ---------------------------------------------------------------------------
# Auth + rate-limit middleware
# ---------------------------------------------------------------------------

def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _role_environment(role: str) -> dict:
    allowed = {t["name"] for t in list_tools()}
    if role_has_permission(role, "system:execute"):
        allowed.add("system")
    if role_has_permission(role, "browser:execute"):
        allowed.add("browser")
    # Explicit denies take precedence over capabilities, including builtins.
    allowed -= set(role_blocked_tools(role)) | set(role_requires_approval(role))
    env = {"GATHM_ALLOWED_TOOLS": ",".join(sorted(allowed)),
           "GATHM_NON_INTERACTIVE": "1", "GATHM_AUTO_INSTALL": "0"}
    if "system" not in allowed:
        env["GATHM_ALLOW_SHELL"] = "0"
    return env


def _child_env(overrides: dict | None = None) -> dict:
    env = dict(os.environ)
    # Tool output must not disclose credentials that grant API access.
    env.pop("GATHM_API_KEY", None)
    env.pop("GATHM_API_KEYS", None)
    env.update(_EXECUTION_ENV.get() or {})
    env.update(overrides or {})
    env["GATHM_OUTPUT_MODE"] = "json"
    return env


def _principal(request: Request) -> str:
    return hashlib.sha256(request.headers.get("authorization", "local").encode()).hexdigest()


def _owns_job(request: Request, job: Job) -> bool:
    return role_has_permission(_get_role(request), "*") or job.owner == _principal(request)


@app.middleware("http")
async def auth_and_ratelimit(request: Request, call_next):
    path = request.url.path.rstrip("/")
    host = request.url.hostname or ""
    # Check both peer and Host: a hostile domain resolving to 127.0.0.1 must
    # not inherit the local GUI's authority (DNS rebinding).
    if not AUTH_ENABLED and (not _is_loopback(host) or not request.client or
                             not _is_loopback(request.client.host)):
        return JSONResponse({"error": "API keys are required for remote access"}, status_code=403)
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            same_origin = (parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
                           and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            same_origin = False
        if not same_origin:
            return JSONResponse({"error": "Cross-origin requests are not allowed"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)

    is_public = path in PUBLIC_PATHS or not path.startswith("/api/")
    role = resolve_role(request)
    if not is_public and role is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    role = role or "anonymous"
    request.state.role = role
    limit = role_rate_limit(role)
    if not is_public and not check_rate_limit(_principal(request), limit):
        return JSONResponse({"error": "rate_limit_exceeded"}, status_code=429,
                            headers={"Retry-After": "60"})
    token = _EXECUTION_ENV.set(_role_environment(role) if not is_public else {})
    try:
        response = await call_next(request)
    finally:
        _EXECUTION_ENV.reset(token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not path.startswith("/api"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return response

# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    args: Union[list[str], str] = []
    timeout: int = Field(default=120, ge=1, le=600)

class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)

class ChatRequest(BaseModel):
    """GUI chat turn. `history` is prior turns as {"role", "content"} dicts."""
    query: str = Field(min_length=1, max_length=16000)
    history: list = Field(default_factory=list, max_length=100)

class SpeechRequest(BaseModel):
    """Text to render as speech for the browser to play."""
    text: str = Field(min_length=1, max_length=16000)

class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=16000)

class PipelineRequest(BaseModel):
    pipeline: str = Field(min_length=1, max_length=16000)

class ParallelRequest(BaseModel):
    tools: str = Field(min_length=1, max_length=16000)

class HealRequest(BaseModel):
    tool: str = "all"

class JobRequest(BaseModel):
    kind: Literal["tool", "ask", "plan", "chain", "parallel", "engineer"] = "tool"
    tool: str                # tool name or agent command argument
    args: Union[list[str], str] = []
    timeout: int = Field(default=120, ge=1, le=600)

# ---------------------------------------------------------------------------
# Helper: enforce tool-level permissions
# ---------------------------------------------------------------------------

def _check_tool_access(role: str, tool_name: str) -> JSONResponse | None:
    """Returns a denial response if the role cannot access the tool."""
    if not valid_tool_name(tool_name):
        raise HTTPException(400, "Invalid tool name")
    if not role_has_permission(role, "tool:execute"):
        return JSONResponse(
            {"error": "forbidden", "detail": f"Role '{role}' lacks tool:execute permission"},
            status_code=403,
        )
    if tool_name in role_blocked_tools(role):
        return JSONResponse(
            {"error": "forbidden", "detail": f"Tool '{tool_name}' is blocked for role '{role}'"},
            status_code=403,
        )
    if tool_name in role_requires_approval(role):
        return JSONResponse(
            {"error": "approval_required",
             "detail": f"Tool '{tool_name}' requires explicit approval for role '{role}'"},
            status_code=403,
        )
    return None

def _get_role(request: Request) -> str:
    return getattr(request.state, "role", "anonymous")

# ---------------------------------------------------------------------------
# Routes: tools
# ---------------------------------------------------------------------------

@app.get("/api/v1/tools", tags=["tools"])
async def get_tools(request: Request):
    if not role_has_permission(_get_role(request), "tool:discover"):
        raise HTTPException(403, "Tool discovery is not allowed")
    tools = list_tools()
    return {"tools": tools, "count": len(tools)}

@app.get("/api/v1/tools/{tool_name}", tags=["tools"])
async def get_tool(tool_name: str, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    manifest = load_tool_manifest(tool_name)
    if not manifest:
        raise HTTPException(404, f"Tool '{tool_name}' not found")
    return manifest

@app.post("/api/v1/tools/{tool_name}/execute", tags=["tools"])
async def execute(tool_name: str, body: ExecuteRequest, request: Request):
    role = _get_role(request)
    denied = _check_tool_access(role, tool_name)
    if denied:
        return denied

    # Per-tool rate limit (if configured)
    tool_limit = tool_rate_limit(tool_name)
    if tool_limit is not None:
        rl_key = f"tool:{tool_name}:{request.client.host if request.client else 'anon'}"
        if not check_rate_limit(rl_key, tool_limit):
            return JSONResponse(
                {"error": "rate_limit_exceeded", "tool": tool_name, "limit": tool_limit},
                status_code=429,
                headers={"X-RateLimit-Limit": str(tool_limit)},
            )

    args = body.args.split() if isinstance(body.args, str) else body.args
    result = await execute_tool(tool_name, args, body.timeout)
    return JSONResponse(result, status_code=200 if result["status"] == "success" else 500)

# ---------------------------------------------------------------------------
# Routes: health (authorized)
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", tags=["health"])
async def health(request: Request):
    if not role_has_permission(_get_role(request), "tool:healthcheck"):
        raise HTTPException(403, "Health checks are not allowed")
    return await run_agent_command("health", "all")

@app.get("/api/v1/health/{tool_name}", tags=["health"])
async def health_tool(tool_name: str, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:healthcheck"):
        raise HTTPException(403, f"Role '{role}' lacks tool:healthcheck permission")
    if not valid_tool_name(tool_name):
        raise HTTPException(400, "Invalid tool name")
    return await run_agent_command("health", tool_name)

# ---------------------------------------------------------------------------
# Routes: agent
# ---------------------------------------------------------------------------

@app.post("/api/v1/agent/ask", tags=["agent"])
async def agent_ask(body: QueryRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    return await run_agent_command("ask", body.query)

@app.post("/api/v1/agent/chat", tags=["agent"])
async def agent_chat(body: ChatRequest, request: Request):
    """Talk to the real Pilot LLM agent for one turn, with history.

    run_chat_agent() and pilot/chat_once.py were both written for this route,
    but the route itself was never registered — so the GUI's POST fell through
    to the StaticFiles mount at "/", which only serves GET/HEAD, and came back
    405 Method Not Allowed instead of a reply.

    Runs in a thread: run_chat_agent uses blocking subprocess.run, and calling
    it directly here would stall the event loop for the whole turn.
    """
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    return await asyncio.to_thread(run_chat_agent, body.query, body.history)

@app.post("/api/v1/agent/plan", tags=["agent"])
async def agent_plan(body: TaskRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    return await run_agent_command("plan", body.task)

@app.post("/api/v1/agent/engineer", tags=["agent"])
async def agent_engineer(body: TaskRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "*"):
        raise HTTPException(403, f"Role '{role}' requires administrator access")
    return await run_agent_command("engineer", body.task)

@app.post("/api/v1/agent/chain", tags=["agent"])
async def agent_chain(body: PipelineRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    return await run_agent_command("chain", body.pipeline)

@app.post("/api/v1/agent/parallel", tags=["agent"])
async def agent_parallel(body: ParallelRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    return await run_agent_command("parallel", body.tools)

@app.get("/api/v1/agent/status", tags=["agent"])
async def agent_status(request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    return await run_agent_command("status")

@app.post("/api/v1/agent/heal", tags=["agent"])
async def agent_heal(body: HealRequest, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "*"):
        raise HTTPException(403, f"Role '{role}' cannot trigger self-healing")
    return await run_agent_command("heal", body.tool)

# ---------------------------------------------------------------------------
# Routes: async job queue
# ---------------------------------------------------------------------------

@app.post("/api/v1/jobs", tags=["jobs"], status_code=202)
async def submit_job(body: JobRequest, request: Request):
    """Submit a long-running job. Returns immediately with a job_id to poll or stream."""
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")

    args = body.args.split() if isinstance(body.args, str) else list(body.args)

    # Tool-level access check for tool kind
    if body.kind == "tool":
        denied = _check_tool_access(role, body.tool)
        if denied:
            return denied

    if body.kind == "engineer" and not role_has_permission(role, "*"):
        raise HTTPException(403, "Engineering jobs require administrator access")
    job = _create_job(kind=body.kind, tool=body.tool, args=args, timeout=body.timeout,
                      owner=_principal(request))
    return JSONResponse(_job_to_dict(job), status_code=202)

@app.get("/api/v1/jobs", tags=["jobs"])
async def list_jobs(request: Request, status: Optional[str] = None):
    """List all jobs, optionally filtered by status."""
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    jobs = [j for j in _job_store.values() if _owns_job(request, j)]
    if status:
        try:
            target = JobStatus(status)
            jobs = [j for j in jobs if j.status == target]
        except ValueError:
            raise HTTPException(400, f"Invalid status '{status}'. "
                                f"Must be one of: {[s.value for s in JobStatus]}")
    return {"jobs": [_job_to_dict(j) for j in jobs], "count": len(jobs)}

@app.get("/api/v1/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: str, request: Request):
    """Poll a job's current status and output."""
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    job = _job_store.get(job_id)
    if not job or not _owns_job(request, job):
        raise HTTPException(404, f"Job '{job_id}' not found")
    return _job_to_dict(job)

@app.get("/api/v1/jobs/{job_id}/stream", tags=["jobs"])
async def stream_job(job_id: str, request: Request):
    """
    Stream live output from a job via Server-Sent Events (SSE).

    Each event is a JSON object on a ``data:`` line:
    - ``{"event": "output", "line": "...", "ts": 1234567890.0}`` — a stdout line
    - ``{"event": "done",   "status": "completed", "exit_code": 0}`` — terminal event

    Replays all lines already emitted before delivering live output.
    """
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    job = _job_store.get(job_id)
    if not job or not _owns_job(request, job):
        raise HTTPException(404, f"Job '{job_id}' not found")

    async def event_stream():
        # Replay lines already captured
        for line in list(job.output_lines):
            yield f'data: {json.dumps({"event": "output", "line": line})}\n\n'

        # Already finished — emit done and close
        if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
            yield f'data: {json.dumps({"event": "done", "status": job.status.value, "exit_code": job.exit_code})}\n\n'
            return

        # Subscribe to live output
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        job._subscribers.append(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f'data: {raw}\n\n'
                    if json.loads(raw).get("event") == "done":
                        break
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'  # SSE comment keeps connection alive
        finally:
            try:
                job._subscribers.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.delete("/api/v1/jobs/{job_id}", tags=["jobs"])
async def cancel_job(job_id: str, request: Request):
    """Cancel a pending or running job."""
    role = _get_role(request)
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
    job = _job_store.get(job_id)
    if not job or not _owns_job(request, job):
        raise HTTPException(404, f"Job '{job_id}' not found")
    if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
        return JSONResponse({"id": job_id, "status": job.status.value,
                             "detail": "Job already in terminal state"}, status_code=200)
    if job._task and not job._task.done():
        job._task.cancel()
    job.status = JobStatus.cancelled
    job.completed_at = time.time()
    await _persist_job(job)
    return JSONResponse({"id": job_id, "status": "cancelled"})

# ---------------------------------------------------------------------------
# Routes: speech
# ---------------------------------------------------------------------------

@app.get("/api/v1/speech/status", tags=["speech"])
async def speech_status():
    """Whether replies can be spoken, and with what.

    The GUI checks this once on load so it only asks for audio when the voice
    runtime is actually installed (Termux) — elsewhere it stays text-only
    instead of firing requests that can never succeed.
    """
    mod = _speech()
    if mod is None:
        return {"available": False, "reason": "lib/speech.py not importable"}
    cfg = mod.resolve()
    # Not "is audio.cpp installed" but "can anything here produce a wav": on
    # macOS the OS voice can, and gating on audio.cpp alone left the browser
    # silent on a machine where Pilot speaks.
    available = mod.enabled() and mod.can_synthesize_file()
    reason = ""
    if mod.speech_disabled():
        reason = "speech disabled (GATHM_SPEAK=0)"
    elif not available:
        reason = ("no speech engine that can write a wav — install audio.cpp "
                  "(Termux) or a system voice such as say/espeak-ng")
    return {
        "available": available,
        "reason": reason,
        "engine": mod.engine(),
        "family": cfg["family"],
        "voice": cfg["voice"],
        "runtime": cfg["bin"] or mod.system_voice_to_file(),
        "model": cfg["model"],
    }

@app.get("/api/v1/transcribe/status", tags=["speech"])
async def transcribe_status():
    """Whether the browser's microphone can be turned into text here."""
    mod = _speech()
    if mod is None:
        return {"available": False, "reason": "lib/speech.py not importable"}
    cfg = mod.resolve_asr()
    available = mod.asr_enabled()
    return {
        "available": available,
        # Phrased for the platform: the Termux rebuild command is not advice a
        # macOS user can act on, and the GUI shows this text verbatim.
        "reason": mod.asr_unavailable_reason(),
        "family": cfg["family"],
        "model": cfg["model"],
    }

@app.post("/api/v1/transcribe", tags=["speech"])
async def transcribe_audio(request: Request):
    """Transcribe an uploaded recording. Body is the raw audio bytes.

    Raw body rather than multipart on purpose: multipart would pull in
    python-multipart, and the browser has a Blob to hand either way. The GUI
    records 16 kHz mono WAV, which is what the ASR models want, so no
    conversion is needed on this path.
    """
    if not role_has_permission(_get_role(request), "tool:execute"):
        raise HTTPException(403, "Speech execution is not allowed")
    mod = _speech()
    if mod is None:
        raise HTTPException(503, "speech runtime unavailable")
    if not mod.asr_enabled():
        raise HTTPException(503, mod.asr_unavailable_reason())

    audio = bytearray()
    async for chunk in request.stream():
        if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording too large")
        audio.extend(chunk)
    if not audio:
        raise HTTPException(400, "no audio in the request body")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"recording too large (max {MAX_AUDIO_BYTES // 1024 // 1024} MB)")

    suffix = ".wav"
    ctype = (request.headers.get("content-type") or "").lower()
    for mime, ext in (("webm", ".webm"), ("ogg", ".ogg"), ("mp4", ".m4a"),
                      ("mpeg", ".mp3")):
        if mime in ctype:
            suffix = ext
            break

    import tempfile  # noqa: PLC0415 - only this route needs it
    fd, path = tempfile.mkstemp(prefix="gathm-upload-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(audio)
        # Anything that is not already a WAV goes through ffmpeg first; the
        # models want 16 kHz mono and will not resample a webm container.
        if suffix != ".wav":
            ok, converted = await asyncio.to_thread(mod.to_wav16, path)
            if not ok:
                raise HTTPException(503, str(converted))
            work = converted
        else:
            work = path
        ok, text = await asyncio.to_thread(mod.transcribe, work)
        if work != path:
            try:
                os.unlink(work)
            except Exception:  # noqa: BLE001
                pass
        if not ok:
            raise HTTPException(422, str(text))
        return {"text": text}
    finally:
        try:
            os.unlink(path)
        except Exception:  # noqa: BLE001
            pass

@app.post("/api/v1/speech", tags=["speech"])
async def speech_synthesize(body: SpeechRequest, request: Request):
    """Render text to a WAV with audio.cpp and return the audio itself.

    Playback happens in the browser rather than on the server: the API process
    may have no audio device at all, and on Termux the browser is on the same
    phone anyway. Runs in a thread — synthesis is a blocking subprocess that
    takes seconds on a phone and would otherwise stall the event loop.
    """
    if not role_has_permission(_get_role(request), "tool:execute"):
        raise HTTPException(403, "Speech execution is not allowed")
    mod = _speech()
    if mod is None:
        raise HTTPException(503, "speech runtime unavailable")
    if mod.speech_disabled():
        raise HTTPException(503, "speech disabled (GATHM_SPEAK=0)")
    if not mod.can_synthesize_file():
        raise HTTPException(503, "no speech engine that can write a wav")

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")

    ok, result = await asyncio.to_thread(mod.synthesize_bytes, text)
    if not ok:
        raise HTTPException(500, str(result))
    return Response(content=result, media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})

# ---------------------------------------------------------------------------
# Routes: API info
# ---------------------------------------------------------------------------

@app.get("/api/v1/ping", tags=["meta"])
async def ping():
    """Cheap liveness probe.

    The GUI polls this every 30s for its Online/Offline badge and falls back to
    /api/v1/tools when it 404s. That fallback shells out to enumerate every
    tool, so a missing /ping meant a full tool scan twice a minute on a phone
    (plus a 404 in the log each time). Deliberately does no work.
    """
    return {"status": "ok", "service": "gathm-api", "version": API_VERSION}

@app.get("/api/v1", tags=["meta"])
@app.get("/api", tags=["meta"])
async def api_info():
    return {
        "name": "Gathm Enterprise API",
        "version": API_VERSION,
        "auth": "Set GATHM_API_KEYS=token:role,... or GATHM_API_KEY=token (admin) to enable",
        "docs": "/api/docs",
        "endpoints": {
            "GET /api/v1/tools": "List all tools",
            "GET /api/v1/tools/{name}": "Get tool metadata",
            "POST /api/v1/tools/{name}/execute": "Execute a tool (synchronous)",
            "GET /api/v1/ping": "Liveness probe (public)",
            "GET /api/v1/health": "System health check (authorized)",
            "GET /api/v1/health/{tool}": "Tool health check",
            "POST /api/v1/agent/ask": "Natural language query",
            "POST /api/v1/agent/plan": "Create execution plan",
            "POST /api/v1/agent/engineer": "Engineering agent task",
            "POST /api/v1/agent/chain": "Execute tool pipeline",
            "POST /api/v1/agent/parallel": "Execute tools in parallel",
            "GET /api/v1/agent/status": "Agent status",
            "GET /api/v1/speech/status": "Whether replies can be spoken",
            "POST /api/v1/speech": "Render text to speech (audio/wav)",
            "GET /api/v1/transcribe/status": "Whether audio can be transcribed",
            "POST /api/v1/transcribe": "Transcribe raw audio bytes to text",
            "POST /api/v1/agent/heal": "Self-heal tools",
            "POST /api/v1/jobs": "Submit async job — returns 202 + job_id immediately",
            "GET /api/v1/jobs": "List all jobs (filter: ?status=running)",
            "GET /api/v1/jobs/{id}": "Poll job status + captured output",
            "GET /api/v1/jobs/{id}/stream": "Stream live output via SSE",
            "DELETE /api/v1/jobs/{id}": "Cancel a running job",
        },
    }

# ---------------------------------------------------------------------------
# GUI static files (served last so API routes take precedence)
# ---------------------------------------------------------------------------

if GUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(GUI_DIR), html=True), name="gui")

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not HAS_FASTAPI:
        print(
            "ERROR: FastAPI and uvicorn are required.\n"
            "Install with: pip install fastapi uvicorn pydantic",
            file=sys.stderr,
        )
        sys.exit(1)

    port = DEFAULT_PORT
    host = DEFAULT_HOST

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            i += 1

    print(f"""
╔══════════════════════════════════════════════════╗
║       Gathm Enterprise API Server v{API_VERSION}       ║
╠══════════════════════════════════════════════════╣
║  Host: {host:<41s} ║
║  Port: {port:<41d} ║
║  GUI:  http://{host}:{port:<25d} ║
║  API:  http://{host}:{port}/api/v1{' ' * 16}║
║  Docs: http://{host}:{port}/api/docs{' ' * 14}║
╚══════════════════════════════════════════════════╝
""")

    # uvicorn imports the app by module path, so the repo root has to be on
    # sys.path. When this file is run as a script — `python3 api/server.py`,
    # which is how the installer and the gathm-api shortcut both launch it —
    # sys.path[0] is the api/ directory instead, and "api.server:app" fails
    # with ModuleNotFoundError: No module named 'api'.
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    # Check the port before uvicorn does. Its own failure is a one-line
    # "[Errno 48] address already in use" printed *after* "Application startup
    # complete", which reads like the server came up — and an older server left
    # running on the port is then the one the browser talks to, so a pull looks
    # like it changed nothing. Name that cause and how to clear it.
    import socket  # noqa: PLC0415 - only needed on this path
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR because uvicorn sets it too: without it this probe is the
    # stricter of the two and would refuse to start over a socket still in
    # TIME_WAIT from a server that has already exited — reporting a conflict
    # where uvicorn would have bound successfully.
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
    except OSError:
        if sys.platform == "win32":
            who = f"netstat -ano | findstr :{port}"
            kill_hint = "taskkill /PID <pid> /F"
        else:
            who = f"lsof -nP -iTCP:{port} -sTCP:LISTEN"
            kill_hint = (f"lsof -ti:{port} | xargs kill      "
                         f"(add -9 if it survives: lsof -ti:{port} | xargs kill -9)")
        print(
            f"ERROR: something is already listening on {host}:{port}.\n"
            "       If that is an older Gathm API server, the browser is still\n"
            "       talking to it and code changes will not take effect until it\n"
            f"       is stopped.\n\n"
            f"       See what holds it:  {who}\n"
            f"       Stop it:            {kill_hint}\n"
            f"       Or use another port: gathm-api --port {port + 1}",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        probe.close()

    uvicorn.run(
        "api.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
