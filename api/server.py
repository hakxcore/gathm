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
    GET  /api/v1/health                 - System health check (public)
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
import json
import os
import platform
import secrets
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Response, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    class CORSMiddleware:
        def __init__(self, *args, **kwargs): pass
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

class GathmAPIHandler:
    """Compatibility stub for testing legacy API server imports."""
    @staticmethod
    def _check_auth():
        return True

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
DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"

# MIME types for static GUI files
MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# API Authentication
# Set GATHM_API_KEY environment variable to enable API key authentication.
# When set, all requests must include: Authorization: Bearer <key>
# Health and root endpoints are exempt.
GATHM_API_KEY = os.environ.get("GATHM_API_KEY", "")
# NOTE: PUBLIC_PATHS lives next to the auth logic below. It used to be defined
# here as well, and that earlier copy was silently shadowed by the later one --
# so entries added here (notably /api/v1/ping) never took effect.

import hashlib
import secrets

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
                token_map[tok.strip()] = role.strip()
    legacy = os.environ.get("GATHM_API_KEY", "")
    if legacy and legacy not in token_map:
        token_map[legacy] = "admin"
    return token_map

TOKEN_MAP: dict[str, str] = _build_token_map()
AUTH_ENABLED = bool(TOKEN_MAP)

# Public paths that skip auth entirely.
# GUI static files are also public (any path not starting with /api/).
PUBLIC_PATHS = {"/", "/api", "/api/v1", "/api/v1/health", "/api/v1/ping"}

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

def load_tool_manifest(tool_name: str) -> dict:
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

async def _run_subprocess(cmd: list[str], timeout: int, extra_env: dict | None = None) -> dict:
    env = {**os.environ, "GATHM_OUTPUT_MODE": "json"}
    if extra_env:
        env.update(extra_env)

    start_time = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"status": "error", "exit_code": -1, "output": "",
                    "error": f"Timed out after {timeout}s", "duration_ms": timeout * 1000}
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

# Words to remove when extracting a tool's argument from natural language.
_NL_FILLER = frozenset([
    "get", "show", "tell", "me", "what", "is", "are", "the", "a", "an",
    "give", "find", "look", "up", "lookup", "check", "please", "i", "want",
    "to", "know", "about", "can", "you", "run", "gathm", "use", "do",
    "for", "in", "at", "on", "from", "of", "and",
    # common query openers per domain
    "weather", "forecast", "temperature", "temp",
    "dns", "records", "record", "query", "lookup",
    "ip", "address",
    "define", "definition", "meaning", "word",
    "crypto", "cryptocurrency", "price", "cost", "value",
    "news", "latest", "current", "today",
    "whois", "info", "information",
    "movie", "film", "song", "lyrics",
])

def _extract_tool_args(query: str, tool_name: str) -> list:
    """Strip NL filler and the tool name from a query, returning bare args."""
    filler = _NL_FILLER | {tool_name.lower()}
    return [w for w in query.split() if w.lower() not in filler]

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
            env={**os.environ},
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
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        (JOBS_DIR / f"{job.id}.json").write_text(json.dumps(_job_to_dict(job), indent=2))
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

    env = {**os.environ, "GATHM_OUTPUT_MODE": "json"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        job._proc = proc

        stderr_lines: list[str] = []

        async def _drain_stderr() -> None:
            assert proc.stderr
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode(errors="replace").rstrip())

        async def _drain_stdout() -> None:
            assert proc.stdout
            deadline = asyncio.get_event_loop().time() + job.timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    proc.kill()
                    job.status = JobStatus.failed
                    job.error = f"Timed out after {job.timeout}s"
                    break
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    continue
                if not raw:
                    break
                text = raw.decode(errors="replace").rstrip("\n")
                job.output_lines.append(text)
                event_data = json.dumps({"event": "output", "line": text, "ts": time.time()})
                for q in list(job._subscribers):
                    try:
                        q.put_nowait(event_data)
                    except asyncio.QueueFull:
                        pass

        await asyncio.gather(_drain_stdout(), _drain_stderr())
        await proc.wait()
        if job.status == JobStatus.running:
            job.exit_code = proc.returncode
            job.error = "\n".join(stderr_lines) if proc.returncode != 0 else ""
            job.status = JobStatus.completed if proc.returncode == 0 else JobStatus.failed

    except asyncio.CancelledError:
        if job._proc:
            try:
                job._proc.kill()
            except Exception:
                pass
        job.status = JobStatus.cancelled
    except Exception as exc:
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

def _create_job(kind: str, tool: str, args: list[str], timeout: int) -> Job:
    job = Job(id=uuid.uuid4().hex, kind=kind, tool=tool, args=args, timeout=timeout)
    _job_store[job.id] = job
    job._task = asyncio.create_task(_run_job_task(job))
    return job

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Gathm Enterprise API",
    version=API_VERSION,
    description="Orchestrate security, networking, and data tools via REST.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ---------------------------------------------------------------------------
# Auth + rate-limit middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def auth_and_ratelimit(request: Request, call_next):
    path = request.url.path.rstrip("/")

    # Public paths and GUI static assets skip auth
    is_api = path.startswith("/api/")
    is_public = path in PUBLIC_PATHS or not is_api

    if is_public:
        return await call_next(request)

    role = resolve_role(request)
    if role is None:
        return JSONResponse(
            {"error": "Unauthorized", "detail": "Provide: Authorization: Bearer <token>"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Rate limit: use token as key when auth enabled, else IP
    auth_header = request.headers.get("Authorization", "")
    rl_key = auth_header[7:] if auth_header.startswith("Bearer ") else (
        request.client.host if request.client else "anonymous"
    )
    limit = role_rate_limit(role)
    if not check_rate_limit(rl_key, limit):
        policies = _get_policies()
        window = policies.get("rate_limiting", {}).get("window_seconds", 60)
        return JSONResponse(
            {"error": "rate_limit_exceeded", "role": role, "limit": limit,
             "window_seconds": window},
            status_code=429,
            headers={"X-RateLimit-Limit": str(limit), "Retry-After": str(window)},
        )

    # Attach role to request state for route handlers
    request.state.role = role
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    return response

# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    args: list[str] | str = []
    timeout: int = 120

class QueryRequest(BaseModel):
    query: str

class ChatRequest(BaseModel):
    """GUI chat turn. `history` is prior turns as {"role", "content"} dicts."""
    query: str
    history: list = []

class TaskRequest(BaseModel):
    task: str

class PipelineRequest(BaseModel):
    pipeline: str

class ParallelRequest(BaseModel):
    tools: str

class HealRequest(BaseModel):
    tool: str = "all"

class JobRequest(BaseModel):
    kind: str = "tool"       # "tool" | "ask" | "plan" | "chain" | "parallel" | "engineer"
    tool: str                # tool name or agent command argument
    args: list[str] | str = []
    timeout: int = 120

# ---------------------------------------------------------------------------
# Helper: enforce tool-level permissions
# ---------------------------------------------------------------------------

def _check_tool_access(role: str, tool_name: str) -> JSONResponse | None:
    """Returns a 403 response if the role cannot access the tool, else None."""
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
    return getattr(request.state, "role", "admin")

# ---------------------------------------------------------------------------
# Routes: tools
# ---------------------------------------------------------------------------

@app.get("/api/v1/tools", tags=["tools"])
async def get_tools():
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
# Routes: health (public)
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", tags=["health"])
async def health():
    return await run_agent_command("health", "all")

@app.get("/api/v1/health/{tool_name}", tags=["health"])
async def health_tool(tool_name: str, request: Request):
    role = _get_role(request)
    if not role_has_permission(role, "tool:healthcheck"):
        raise HTTPException(403, f"Role '{role}' lacks tool:healthcheck permission")
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
    if not role_has_permission(role, "tool:execute"):
        raise HTTPException(403, f"Role '{role}' lacks tool:execute permission")
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
    if not role_has_permission(role, "tool:execute") or role == "readonly":
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

    job = _create_job(kind=body.kind, tool=body.tool, args=args, timeout=body.timeout)
    return JSONResponse(_job_to_dict(job), status_code=202)

@app.get("/api/v1/jobs", tags=["jobs"])
async def list_jobs(request: Request, status: str | None = None):
    """List all jobs, optionally filtered by status."""
    role = _get_role(request)
    if not role_has_permission(role, "tool:discover"):
        raise HTTPException(403, f"Role '{role}' lacks tool:discover permission")
    jobs = list(_job_store.values())
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
    if not job:
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
    if not job:
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
    if not job:
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
            "GET /api/v1/health": "System health check (public)",
            "GET /api/v1/health/{tool}": "Tool health check",
            "POST /api/v1/agent/ask": "Natural language query",
            "POST /api/v1/agent/plan": "Create execution plan",
            "POST /api/v1/agent/engineer": "Engineering agent task",
            "POST /api/v1/agent/chain": "Execute tool pipeline",
            "POST /api/v1/agent/parallel": "Execute tools in parallel",
            "GET /api/v1/agent/status": "Agent status",
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

    uvicorn.run(
        "api.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
