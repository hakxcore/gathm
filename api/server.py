#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gathm Enterprise - REST API Server
Exposes all Gathm tools via HTTP endpoints for programmatic access.
Cross-platform: Linux (all distros), macOS, Termux, Windows (WSL/Git Bash/MSYS2/native)

Usage:
    python3 api/server.py [--port 8080] [--host 0.0.0.0]

Endpoints:
    GET  /api/v1/tools                  - List all tools
    GET  /api/v1/tools/{name}           - Get tool metadata
    POST /api/v1/tools/{name}/execute   - Execute a tool
    GET  /api/v1/health                 - System health check
    GET  /api/v1/health/{tool}          - Tool health check
    POST /api/v1/agent/ask              - Natural language query
    POST /api/v1/agent/plan             - Create execution plan
    POST /api/v1/agent/engineer         - Engineering agent task
    POST /api/v1/agent/chain            - Execute tool pipeline
    GET  /api/v1/agent/status           - Agent status
    POST /api/v1/agent/heal             - Self-heal tools
"""

import http.server
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
import time
from pathlib import Path

# PyYAML is optional - fall back to basic parsing if not available
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Configuration
GATHM_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = GATHM_ROOT / "tools"
GUI_DIR = GATHM_ROOT / "gui"
AGENT_SCRIPT = GATHM_ROOT / "agent" / "orchestrator.sh"
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
PUBLIC_PATHS = {"", "/", "/api", "/api/v1", "/api/v1/health", "/api/v1/ping"}
# GUI static files are also public (any path not starting with /api/)

import hashlib
import secrets


def _find_bash() -> str:
    """Find bash executable cross-platform (Linux/macOS/Termux/Windows)."""
    # Direct lookup
    bash = shutil.which("bash")
    if bash:
        return bash
    # Windows-specific paths
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\msys64\usr\bin\bash.exe",
            r"C:\Windows\System32\bash.exe",  # WSL
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    return "bash"  # Last resort - hope it's on PATH


BASH_CMD = _find_bash()


def load_tool_manifest(tool_name: str) -> dict:
    """Load a tool's YAML manifest (works with or without PyYAML)."""
    manifest_path = TOOLS_DIR / tool_name / "tool.yaml"
    if not manifest_path.exists():
        return {}
    with open(manifest_path) as f:
        if HAS_YAML:
            return yaml.safe_load(f) or {}
        # Basic YAML fallback parser for simple key: value manifests
        result = {}
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                key, _, value = line.partition(":")
                value = value.strip().strip('"').strip("'")
                if value:
                    result[key.strip()] = value
        return result


def list_tools() -> list:
    """List all available tools with their metadata."""
    tools = []
    for tool_dir in sorted(TOOLS_DIR.iterdir()):
        if tool_dir.is_dir():
            tool_name = tool_dir.name
            tool_exec = tool_dir / tool_name
            if tool_exec.exists():
                manifest = load_tool_manifest(tool_name)
                tools.append({
                    "name": tool_name,
                    "description": manifest.get("description", "No description"),
                    "version": manifest.get("version", "unknown"),
                    "category": manifest.get("category", "unknown"),
                    "tags": manifest.get("tags", []),
                })
    return tools


def execute_tool(tool_name: str, args: list = None, timeout: int = 120) -> dict:
    """Execute a tool via the agent orchestrator."""
    args = args or []
    cmd = [BASH_CMD, str(AGENT_SCRIPT), "run", tool_name] + args

    start_time = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GATHM_OUTPUT_MODE": "json"}
        )
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "tool": tool_name,
            "status": "success" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.returncode != 0 else "",
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        return {
            "tool": tool_name,
            "status": "error",
            "exit_code": -1,
            "output": "",
            "error": f"Tool execution timed out after {timeout}s",
            "duration_ms": timeout * 1000,
        }
    except Exception as e:
        return {
            "tool": tool_name,
            "status": "error",
            "exit_code": -1,
            "output": "",
            "error": str(e),
            "duration_ms": 0,
        }


def run_agent_command(command: str, args: str = "") -> dict:
    """Run an agent orchestrator command."""
    cmd = [BASH_CMD, str(AGENT_SCRIPT), command]
    if args:
        cmd.extend(args.split())
    cmd.append("--json")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "GATHM_OUTPUT_MODE": "json"}
        )
        output = result.stdout.strip()
        # Try to parse as JSON
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw_output": output, "exit_code": result.returncode}
    except Exception as e:
        return {"error": str(e)}


class GathmAPIHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the Gathm API."""

    def _send_json(self, data: dict, status: int = 200):
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def _serve_gui_file(self, file_path: str):
        """Serve a static file from the gui/ directory."""
        if file_path in ("", "/"):
            file_path = "/index.html"
        # Prevent path traversal
        safe_path = Path(os.path.normpath(file_path.lstrip("/")))
        if ".." in safe_path.parts:
            self._send_json({"error": "Forbidden"}, 403)
            return
        full_path = GUI_DIR / safe_path
        if not full_path.is_file():
            self._send_json({"error": "Not found"}, 404)
            return
        mime = MIME_TYPES.get(full_path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(full_path.read_bytes())

    def _read_body(self) -> dict:
        """Read and parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def _check_auth(self) -> bool:
        """Verify API key if GATHM_API_KEY is configured."""
        if not GATHM_API_KEY:
            return True  # No auth required

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in PUBLIC_PATHS or not path.startswith("/api/"):
            return True  # Public endpoints and GUI static files

        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            # Constant-time comparison
            return secrets.compare_digest(token, GATHM_API_KEY)
        return False

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self._send_json({})

    def do_GET(self):
        """Handle GET requests."""
        if not self._check_auth():
            self._send_json({"error": "Unauthorized. Provide: Authorization: Bearer <api_key>"}, 401)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # GET /api/v1/tools
        if path == "/api/v1/tools":
            tools = list_tools()
            self._send_json({"tools": tools, "count": len(tools)})

        # GET /api/v1/tools/{name}
        elif path.startswith("/api/v1/tools/"):
            tool_name = path.split("/")[-1]
            manifest = load_tool_manifest(tool_name)
            if manifest:
                self._send_json(manifest)
            else:
                self._send_json({"error": f"Tool '{tool_name}' not found"}, 404)

        # GET /api/v1/ping  — lightweight liveness probe used by the GUI.
        # Unlike /health it does NOT shell out to the orchestrator, so it
        # returns instantly and never makes the UI look "offline".
        elif path == "/api/v1/ping":
            self._send_json({"status": "ok", "service": "gathm-api"})

        # GET /api/v1/health  (full system health — checks every tool, slow)
        elif path == "/api/v1/health":
            result = run_agent_command("health", "all")
            self._send_json(result)

        # GET /api/v1/health/{tool}
        elif path.startswith("/api/v1/health/"):
            tool_name = path.split("/")[-1]
            result = run_agent_command("health", tool_name)
            self._send_json(result)

        # GET /api/v1/agent/status
        elif path == "/api/v1/agent/status":
            result = run_agent_command("status")
            self._send_json(result)

        # API documentation
        elif path in ("/api", "/api/v1"):
            self._send_json({
                "name": "Gathm Enterprise API",
                "version": "2.0.0",
                "auth": "Set GATHM_API_KEY env var to enable Bearer token auth",
                "endpoints": {
                    "GET /api/v1/tools": "List all tools",
                    "GET /api/v1/tools/{name}": "Get tool metadata",
                    "POST /api/v1/tools/{name}/execute": "Execute a tool",
                    "GET /api/v1/health": "System health check (public)",
                    "GET /api/v1/health/{tool}": "Tool health check",
                    "POST /api/v1/agent/ask": "Natural language query",
                    "POST /api/v1/agent/plan": "Create execution plan",
                    "POST /api/v1/agent/engineer": "Engineering agent task",
                    "POST /api/v1/agent/chain": "Execute tool pipeline",
                    "POST /api/v1/agent/parallel": "Execute tools in parallel",
                    "GET /api/v1/agent/status": "Agent status",
                    "POST /api/v1/agent/heal": "Self-heal tools",
                }
            })

        # GUI static files (root and any non-API path)
        else:
            self._serve_gui_file(parsed.path)

    def do_POST(self):
        """Handle POST requests."""
        if not self._check_auth():
            self._send_json({"error": "Unauthorized. Provide: Authorization: Bearer <api_key>"}, 401)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        # POST /api/v1/tools/{name}/execute
        if path.startswith("/api/v1/tools/") and path.endswith("/execute"):
            parts = path.split("/")
            tool_name = parts[4]  # /api/v1/tools/{name}/execute
            args = body.get("args", [])
            timeout = body.get("timeout", 120)

            if isinstance(args, str):
                args = args.split()

            result = execute_tool(tool_name, args, timeout)
            status = 200 if result["status"] == "success" else 500
            self._send_json(result, status)

        # POST /api/v1/agent/ask
        elif path == "/api/v1/agent/ask":
            query = body.get("query", "")
            if not query:
                self._send_json({"error": "Missing 'query' field"}, 400)
                return
            result = run_agent_command("ask", query)
            self._send_json(result)

        # POST /api/v1/agent/plan
        elif path == "/api/v1/agent/plan":
            task = body.get("task", "")
            if not task:
                self._send_json({"error": "Missing 'task' field"}, 400)
                return
            result = run_agent_command("plan", task)
            self._send_json(result)

        # POST /api/v1/agent/engineer
        elif path == "/api/v1/agent/engineer":
            task = body.get("task", "")
            if not task:
                self._send_json({"error": "Missing 'task' field"}, 400)
                return
            result = run_agent_command("engineer", task)
            self._send_json(result)

        # POST /api/v1/agent/chain
        elif path == "/api/v1/agent/chain":
            pipeline = body.get("pipeline", "")
            if not pipeline:
                self._send_json({"error": "Missing 'pipeline' field"}, 400)
                return
            result = run_agent_command("chain", pipeline)
            self._send_json(result)

        # POST /api/v1/agent/parallel
        elif path == "/api/v1/agent/parallel":
            tools = body.get("tools", "")
            if not tools:
                self._send_json({"error": "Missing 'tools' field"}, 400)
                return
            result = run_agent_command("parallel", tools)
            self._send_json(result)

        # POST /api/v1/agent/heal
        elif path == "/api/v1/agent/heal":
            tool = body.get("tool", "all")
            result = run_agent_command("heal", tool)
            self._send_json(result)

        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        """Custom log format."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        sys.stderr.write(f"[{timestamp}] {args[0]} {args[1]} {args[2]}\n")


def main():
    """Start the API server."""
    port = DEFAULT_PORT
    host = DEFAULT_HOST

    # Parse command line arguments
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

    # ThreadingHTTPServer so a slow request (e.g. the full /health sweep
    # across all tools) never blocks the GUI from loading or other
    # requests from being served concurrently.
    server = http.server.ThreadingHTTPServer((host, port), GathmAPIHandler)
    print(f"""
╔══════════════════════════════════════════════════╗
║           Gathm Enterprise API Server            ║
╠══════════════════════════════════════════════════╣
║  Host: {host:<41s} ║
║  Port: {port:<41d} ║
║  GUI:  http://{host}:{port:<25d} ║
║  API:  http://{host}:{port}/api/v1{' ' * 16}║
╚══════════════════════════════════════════════════╝
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
