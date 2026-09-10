#!/usr/bin/env python3
"""Tests for the llama.cpp runtime manager (lib/llamacpp.py) and the backend
selection in lib/llm.py.

The one thing worth stating up front: there is no llama-server on a CI runner
and there never will be, so every test here either exercises pure resolution
logic or runs against a stub server that answers /health and /v1/models the
way llama.cpp does. That is enough to cover what has actually broken in this
area — the wrong file picked out of a models directory, a start that was
merely slow reported as a failure, and a backend chosen because a leftover
config file said so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASS = 0
FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print("  ok   %s" % name)


def bad(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    print("  FAIL %s\n     %s" % (name, detail))


def check(name: str, got, want) -> None:
    if got == want:
        ok(name)
    else:
        bad(name, "got %r, wanted %r" % (got, want))


def truthy(name: str, value, detail: str = "") -> None:
    if value:
        ok(name)
    else:
        bad(name, detail or "expected a truthy value, got %r" % (value,))


def contains(name: str, haystack, needle) -> None:
    if needle in haystack:
        ok(name)
    else:
        bad(name, "expected %r in %r" % (needle, haystack))


# ---------------------------------------------------------------------------
# A stub llama-server: /health, /v1/models, /v1/chat/completions
# ---------------------------------------------------------------------------

# A raw literal: the SSE writes below contain \n escapes that belong to the
# stub's source, not to this file. Interpreting them here put real newlines
# mid-line and broke the dedent.
STUB = textwrap.dedent(r'''
    import json, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[1])
    # Seconds to spend answering /health with 503, the way llama-server does
    # while it is still reading the weights off disk.
    load_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    started = time.time()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            last = body["messages"][-1]["content"]
            reply = "echo: " + last
            if not body.get("stream"):
                self._send(200, {"choices": [{"message": {"role": "assistant",
                                                          "content": reply}}],
                                 "model": "stub-model"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in reply.split(" "):
                event = {"choices": [{"delta": {"content": piece + " "}}]}
                self.wfile.write(("data: %s\n\n" % json.dumps(event)).encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def do_GET(self):
            if self.path == "/health":
                if time.time() - started < load_seconds:
                    self._send(503, {"error": {"message": "loading model"}})
                else:
                    self._send(200, {"status": "ok"})
                return
            if self.path.startswith("/v1/models"):
                self._send(200, {"data": [{"id": "stub-model"}]})
                return
            self._send(404, {})

        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_binary(path: Path, stub_source: Path, load_seconds: float = 0.0) -> None:
    """A llama-server that is really the stub HTTP server.

    It has to read -m/--port off the command line exactly as llama-server does,
    because the point is to prove the manager passes them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import subprocess, sys
        args = sys.argv[1:]
        port = "8081"
        for i, value in enumerate(args):
            if value == "--port":
                port = args[i + 1]
        # An unknown flag is fatal to the real llama-server; mimic that so the
        # manager's "retry without tuning flags" path is exercised for real.
        if "--explode" in args:
            sys.stderr.write("error: unknown argument: --explode\\n")
            raise SystemExit(1)
        raise SystemExit(subprocess.call(
            [sys.executable, %r, port, %r]))
        ''' % (str(stub_source), str(load_seconds))))
    path.chmod(0o755)


def main() -> int:
    import lib.llamacpp as llamacpp
    import lib.llm as llm

    workspace = Path(tempfile.mkdtemp(prefix="gathm-llamacpp-test-"))
    stub_source = workspace / "stub_server.py"
    stub_source.write_text(STUB)

    saved_env = dict(os.environ)

    def reset_env(**overrides) -> None:
        for key in list(os.environ):
            if key.startswith(("GATHM_", "OLLAMA_", "ANTHROPIC_", "GOOGLE_", "GEMINI_")):
                del os.environ[key]
        os.environ["GATHM_CONFIG_DIR"] = str(workspace / "state")
        os.environ.update(overrides)
        (workspace / "state").mkdir(parents=True, exist_ok=True)

    try:
        # ---------------------------------------------------------------
        print("== resolving the weights ==")
        reset_env()
        models = workspace / "state" / "models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "small.gguf").write_bytes(b"x" * 100)
        (models / "big.gguf").write_bytes(b"x" * 5000)
        check("the largest GGUF wins", llamacpp.resolve_model().name, "big.gguf")

        # A split model is one model. Handing llama.cpp shard 2 is an error, so
        # shard 1 has to win even when it is not the largest file present.
        shards = workspace / "shards"
        shards.mkdir()
        (shards / "m-00002-of-00003.gguf").write_bytes(b"x" * 9000)
        (shards / "m-00001-of-00003.gguf").write_bytes(b"x" * 10)
        (shards / "m-00003-of-00003.gguf").write_bytes(b"x" * 9000)
        os.environ["GATHM_LLAMACPP_MODEL"] = str(shards)
        check("a split model resolves to shard 1",
              llamacpp.resolve_model().name, "m-00001-of-00003.gguf")
        check("and is labelled without the shard suffix",
              llamacpp.model_label(llamacpp.resolve_model()), "m")

        reset_env(GATHM_LLAMACPP_MODEL=str(models / "small.gguf"))
        check("an explicit file is used as given",
              llamacpp.resolve_model().name, "small.gguf")
        check("labelled without the extension",
              llamacpp.model_label(llamacpp.resolve_model()), "small")

        # Weights adopted from Ollama's blob store are named by content hash.
        # "sha256-ab34…" is the real file name and a useless thing to show a
        # user or to send as the model id, so the tag the installer recorded
        # stands in for it.
        reset_env()
        blob = workspace / "state" / "blobs"
        blob.mkdir(parents=True, exist_ok=True)
        blob_file = blob / ("sha256-" + "ab" * 32)
        blob_file.write_bytes(b"GGUF" + b"\x00" * 64)
        (workspace / "state" / "model").write_text("llama3.2:3b\n")
        check("an Ollama blob is labelled by its tag",
              llamacpp.model_label(blob_file), "llama3.2:3b")
        (workspace / "state" / "model").unlink()
        check("and falls back to something sane without one",
              llamacpp.model_label(blob_file), "local-gguf")

        reset_env()
        pointer = workspace / "state" / "llamacpp_model"
        pointer.write_text(str(models / "small.gguf"))
        check("~/.gathm/llamacpp_model is honoured",
              llamacpp.resolve_model().name, "small.gguf")
        pointer.unlink()

        # ---------------------------------------------------------------
        print("== the command line ==")
        reset_env(GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                  GATHM_LLAMACPP_BIN=str(workspace / "llama-server"),
                  GATHM_LLAMACPP_PORT="9099", GATHM_LLAMACPP_CTX="8192",
                  GATHM_LLAMACPP_THREADS="6", GATHM_LLAMACPP_NGL="33",
                  GATHM_LLAMACPP_ARGS="--flash-attn on")
        (workspace / "llama-server").write_text("#!/bin/sh\nexit 0\n")
        (workspace / "llama-server").chmod(0o755)
        cfg = llamacpp.LlamaCppConfig.from_env()
        cmd = llamacpp.build_command(cfg)
        check("the port is passed through", cmd[cmd.index("--port") + 1], "9099")
        check("the context size is passed through", cmd[cmd.index("-c") + 1], "8192")
        check("threads are passed through", cmd[cmd.index("-t") + 1], "6")
        check("GPU layers are passed through", cmd[cmd.index("-ngl") + 1], "33")
        contains("extra args are appended", cmd, "--flash-attn")
        contains("the chat template is used", cmd, "--jinja")
        check("the model is named for clients", cmd[cmd.index("-a") + 1], "big")
        check("the minimal command drops the tuning flags",
              llamacpp.build_command(cfg, minimal=True),
              [str(workspace / "llama-server"), "-m", str(models / "big.gguf"),
               "--host", "127.0.0.1", "--port", "9099"])
        check("the base URL follows the port",
              cfg.base_url, "http://127.0.0.1:9099/v1")

        reset_env(GATHM_LLAMACPP_HOST="0.0.0.0", GATHM_LLAMACPP_PORT="9100")
        check("a wildcard bind is advertised as loopback",
              llamacpp.base_url(), "http://127.0.0.1:9100/v1")
        reset_env(GATHM_LLAMACPP_BASE_URL="http://10.0.0.5:9999/v1")
        check("an explicit base URL wins",
              llamacpp.base_url(), "http://10.0.0.5:9999/v1")
        check("and host/port are read back out of it",
              llamacpp.LlamaCppConfig.from_env().port, 9999)

        # ---------------------------------------------------------------
        print("== hardware defaults ==")
        reset_env()
        truthy("thread count is at least 1", llamacpp.physical_cores() >= 1)
        truthy("thread count does not exceed logical CPUs",
               llamacpp.physical_cores() <= (os.cpu_count() or 4))
        reset_env(GATHM_LLAMACPP_NGL="7")
        check("GPU layers can be forced", llamacpp.gpu_layers(), 7)
        reset_env(GATHM_LLAMACPP_NGL="0")
        check("and forced off", llamacpp.gpu_layers(), 0)

        # ---------------------------------------------------------------
        print("== starting and stopping ==")
        port = free_port()
        binary = workspace / "bin" / "llama-server"
        fake_binary(binary, stub_source)
        reset_env(GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                  GATHM_LLAMACPP_PORT=str(port))
        cfg = llamacpp.LlamaCppConfig.from_env()
        check("nothing is running yet", llamacpp.is_running(cfg, timeout=1), False)
        started, message = llamacpp.start(cfg, timeout=30)
        truthy("the server starts", started, message)
        truthy("and answers", llamacpp.is_running(cfg, timeout=2))
        check("the served model is readable", llamacpp.served_model(cfg), "stub-model")
        started_again, message = llamacpp.start(cfg, timeout=5)
        truthy("starting twice is a no-op", started_again, message)
        contains("and says so", message, "already running")
        truthy("the pid file was written", llamacpp._read_pid() is not None)
        stopped, message = llamacpp.stop(cfg)
        truthy("the server stops", stopped, message)
        time.sleep(1)
        check("and stops answering", llamacpp.is_running(cfg, timeout=1), False)
        check("stopping again is harmless", llamacpp.stop(cfg)[0], True)

        # A model that takes its time must not be reported as a failure.
        port = free_port()
        slow_binary = workspace / "bin" / "llama-server-slow"
        fake_binary(slow_binary, stub_source, load_seconds=3.0)
        reset_env(GATHM_LLAMACPP_BIN=str(slow_binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                  GATHM_LLAMACPP_PORT=str(port))
        cfg = llamacpp.LlamaCppConfig.from_env()
        started, message = llamacpp.start(cfg, timeout=30)
        truthy("a slow model load still counts as started", started, message)
        llamacpp.stop(cfg)

        # A server someone else is part-way through starting: waiting for it is
        # right, starting a second one that cannot bind the port is not.
        port = free_port()
        other = subprocess.Popen([sys.executable, str(stub_source), str(port), "4"])
        try:
            reset_env(GATHM_LLAMACPP_BIN=str(workspace / "nope"),
                      GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                      GATHM_LLAMACPP_PORT=str(port))
            cfg = llamacpp.LlamaCppConfig.from_env()
            for _ in range(20):     # let it get as far as answering 503
                if llamacpp._health(cfg, timeout=1) == "loading":
                    break
                time.sleep(0.25)
            started, message = llamacpp.start(cfg, timeout=30)
            truthy("a server already loading is waited for, not duplicated",
                   started, message)
        finally:
            other.terminate()

        # Something else on the port is not a model server, however politely it
        # answers. Reporting it as running hides the collision until the first
        # question.
        port = free_port()
        squatter = workspace / "squatter.py"
        squatter.write_text(textwrap.dedent('''
            import sys
            from http.server import BaseHTTPRequestHandler, HTTPServer
            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(404); self.end_headers()
                def log_message(self, *a): pass
            HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
        '''))
        other = subprocess.Popen([sys.executable, str(squatter), str(port)])
        try:
            time.sleep(1.5)
            reset_env(GATHM_LLAMACPP_PORT=str(port))
            cfg = llamacpp.LlamaCppConfig.from_env()
            check("a stranger on the port is not a model server",
                  llamacpp.is_running(cfg, timeout=2), False)
        finally:
            other.terminate()

        # An unknown flag is what a year-old distro build does with a new
        # option. The retry has to rescue it.
        port = free_port()
        reset_env(GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                  GATHM_LLAMACPP_PORT=str(port),
                  GATHM_LLAMACPP_ARGS="--explode")
        cfg = llamacpp.LlamaCppConfig.from_env()
        started, message = llamacpp.start(cfg, timeout=30)
        truthy("an unsupported flag is retried without it", started, message)
        llamacpp.stop(cfg)

        # ---------------------------------------------------------------
        print("== when it is not installed ==")
        reset_env(GATHM_LLAMACPP_BIN=str(workspace / "nope"),
                  GATHM_LLAMACPP_MODEL=str(workspace / "nope.gguf"),
                  GATHM_LLAMACPP_MODEL_DIR=str(workspace / "empty"))
        cfg = llamacpp.LlamaCppConfig.from_env()
        check("no binary is found", cfg.binary, None)
        started, message = llamacpp.start(cfg, timeout=5)
        check("starting fails cleanly", started, False)
        contains("and says what to do", message, "install")

        # ---------------------------------------------------------------
        print("== backend selection ==")
        reset_env(GATHM_LLAMACPP_MODEL_DIR=str(workspace / "empty"))
        check("no llama.cpp means Ollama", llm.LLMConfig.from_env().backend, "ollama")

        reset_env(GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"))
        cfg = llm.LLMConfig.from_env()
        check("a usable llama.cpp is preferred", cfg.backend, "llamacpp")
        check("and the GGUF names the model", cfg.model, "big")
        check("with the weights recorded", cfg.model_path, str(models / "big.gguf"))
        contains("and an OpenAI-compatible URL", cfg.base_url, "/v1")

        # A stale Ollama tag in ~/.gathm/model must not be handed to a server
        # that loads GGUF files by path.
        (workspace / "state" / "model").write_text("gemma3:12b\n")
        check("a stale Ollama tag does not leak into llama.cpp",
              llm.LLMConfig.from_env().model, "big")
        (workspace / "state" / "model").unlink()

        reset_env(GATHM_LLM_BACKEND="ollama", GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"))
        check("an explicit backend still wins",
              llm.LLMConfig.from_env().backend, "ollama")

        reset_env(GATHM_LLM_BACKEND="llama.cpp",
                  GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"))
        check("'llama.cpp' is accepted as a spelling",
              llm.LLMConfig.from_env().backend, "llamacpp")

        reset_env(GATHM_LLAMACPP_BIN=str(binary),
                  GATHM_LLAMACPP_MODEL=str(models / "big.gguf"),
                  ANTHROPIC_API_KEY="sk-test")
        check("an API key still beats the local runtime",
              llm.LLMConfig.from_env().backend, "anthropic")

        # A backend written by the installer is respected without the env.
        reset_env(GATHM_LLAMACPP_MODEL_DIR=str(workspace / "empty"))
        (workspace / "state" / "llm_backend").write_text("llamacpp\n")
        check("~/.gathm/llm_backend is honoured",
              llm.LLMConfig.from_env().backend, "llamacpp")
        (workspace / "state" / "llm_backend").unlink()

        # ---------------------------------------------------------------
        print("== the CLI the launcher calls ==")
        port = free_port()
        env = dict(os.environ)
        env.update({"GATHM_CONFIG_DIR": str(workspace / "state"),
                    "GATHM_LLAMACPP_BIN": str(binary),
                    "GATHM_LLAMACPP_MODEL": str(models / "big.gguf"),
                    "GATHM_LLAMACPP_PORT": str(port)})

        def run(*args):
            return subprocess.run([sys.executable, str(REPO / "lib" / "llamacpp.py"), *args],
                                  capture_output=True, text=True, env=env, timeout=60)

        result = run("status")
        check("status exits 1 when the server is down", result.returncode, 1)
        contains("and names the URL", result.stdout, str(port))
        result = run("start")
        check("start exits 0", result.returncode, 0)
        result = run("status")
        check("status exits 0 when it is up", result.returncode, 0)
        contains("and names the model", result.stdout, "stub-model")
        check("stop exits 0", run("stop").returncode, 0)

        env_missing = dict(env)
        env_missing["GATHM_LLAMACPP_BIN"] = str(workspace / "nope")
        result = subprocess.run(
            [sys.executable, str(REPO / "lib" / "llamacpp.py"), "status"],
            capture_output=True, text=True, env=env_missing, timeout=60)
        check("status exits 2 when nothing is installed", result.returncode, 2)

        # ---------------------------------------------------------------
        print("== the LangChain client Pilot talks through ==")
        try:
            import lib.chat_openai_compat as compat
        except ImportError as exc:
            print("  skip langchain_core is not installed (%s)" % exc)
        else:
            port = free_port()
            server = subprocess.Popen([sys.executable, str(stub_source), str(port)])
            try:
                for _ in range(30):
                    if llamacpp._health(
                            llamacpp.LlamaCppConfig(binary=None, model=None,
                                                    port=port), timeout=1) == "ok":
                        break
                    time.sleep(0.5)
                model = compat.ChatOpenAICompatible(
                    base_url="http://127.0.0.1:%d/v1" % port, model="stub-model")
                from langchain_core.messages import HumanMessage, SystemMessage
                answer = model.invoke([SystemMessage(content="be brief"),
                                       HumanMessage(content="hello there")])
                check("invoke() returns the reply", answer.content, "echo: hello there")
                streamed = "".join(chunk.content for chunk in
                                   model.stream([HumanMessage(content="hello there")]))
                check("stream() reassembles to the same thing",
                      streamed.strip(), "echo: hello there")
            finally:
                server.terminate()

    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(workspace, ignore_errors=True)

    print("")
    print("passed=%d failed=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
