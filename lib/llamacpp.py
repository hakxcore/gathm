#!/usr/bin/env python3
"""
Gathm Enterprise — llama.cpp runtime manager (the fast local LLM path)

Why this exists
---------------
Gathm used to reach the local model only through Ollama. Ollama is a wrapper
around llama.cpp, and the wrapper costs real time on every question:

  * it re-reads the model into a fresh runner whenever the idle timer expires
    (5 minutes by default), so the first question after a coffee break pays the
    whole load again;
  * it decides context size, batch size and GPU offload for you, and its
    conservative defaults leave throughput on the table;
  * every request crosses an extra process boundary (client → ollama →
    runner) instead of going straight to the server that owns the weights.

llama.cpp's own ``llama-server`` is the layer underneath all of that. It speaks
the same OpenAI-compatible ``/v1/chat/completions`` API, so nothing above this
file has to change shape, and it gives us the knobs that actually matter for
latency: threads, context, GPU layers, and a model that stays resident for as
long as the server runs.

What this module does
---------------------
Finds the binary, finds the weights, starts the server, waits until it is
really answering, and stops it again — on macOS, Linux and Windows. It is both
a library (Pilot and Engineer import it through ``lib/llm.py``) and a CLI the
bash launcher shells out to, so the launcher and Python can never disagree
about where the model is::

    python3 lib/llamacpp.py status     # 0 running, 1 down, 2 not installed
    python3 lib/llamacpp.py start
    python3 lib/llamacpp.py stop
    python3 lib/llamacpp.py doctor

Environment (all optional)
--------------------------
  GATHM_LLAMACPP_BIN        path to llama-server
  GATHM_LLAMACPP_MODEL      path to a .gguf file (or a directory holding one)
  GATHM_LLAMACPP_MODEL_DIR  where to look for .gguf files (default ~/.gathm/models)
  GATHM_LLAMACPP_HOST       bind address              (default 127.0.0.1)
  GATHM_LLAMACPP_PORT       port                      (default 8081)
  GATHM_LLAMACPP_BASE_URL   full OpenAI base URL, overrides host/port
  GATHM_LLAMACPP_CTX        context window in tokens  (default 4096)
  GATHM_LLAMACPP_NGL        layers to offload to GPU  (default: auto)
  GATHM_LLAMACPP_THREADS    generation threads        (default: physical cores)
  GATHM_LLAMACPP_ARGS       extra llama-server flags, shell-quoted
  GATHM_LLAMACPP_AUTOSTART  0 to never start the server implicitly
  GATHM_LLAMACPP_START_TIMEOUT  seconds to wait for the model to load (default 180)
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# 8081, not 8080: the Gathm GUI owns 8080. A default that collides with our own
# web server would mean the first `gathm` of the day starts a model server that
# cannot bind, or a GUI that cannot bind, depending on which won the race.
DEFAULT_PORT = 8081
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CTX = 4096

# The model has to be read off disk before the server answers anything, and a
# 9 GB file on a spinning disk or a cold page cache is not fast. Killing the
# start at 30 seconds turned "still loading" into "failed to start".
DEFAULT_START_TIMEOUT = 180

# Both spellings, everywhere. Which one is right depends on the Python running
# this file rather than on the machine: under Git Bash a python.org interpreter
# reports "win32" while an MSYS one reports "msys", and a search that trusted
# either would miss llama-server.exe on the same Windows box.
BIN_NAMES = ("llama-server", "llama-server.exe")
BIN_NAME = "llama-server.exe" if os.name == "nt" else "llama-server"


def state_dir() -> Path:
    """Where Gathm keeps its per-user state (pid files, logs, config)."""
    return Path(os.environ.get("GATHM_CONFIG_DIR") or (Path.home() / ".gathm"))


def models_dir() -> Path:
    """Where Gathm keeps downloaded GGUF weights."""
    env = os.environ.get("GATHM_LLAMACPP_MODEL_DIR")
    if env:
        return Path(env).expanduser()
    return state_dir() / "models"


def _platform() -> str:
    """The platform in the words the rest of Gathm uses.

    Deliberately duplicated from lib/sysexec.py rather than imported: this
    module is run as a standalone script by the bash launcher (``python3
    lib/llamacpp.py status``), where ``lib`` is not an importable package and
    sys.path surgery to reach eight lines would be the larger sin.
    """
    if "com.termux" in (os.environ.get("PREFIX") or ""):
        return "termux"
    if os.path.isdir("/data/data/com.termux/files/usr"):
        return "termux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform or "unknown"


# ---------------------------------------------------------------------------
# Locating the binary
# ---------------------------------------------------------------------------

def _candidate_binaries() -> list[Path]:
    """Every place a llama-server could reasonably be, best first."""
    out: list[Path] = []

    env = os.environ.get("GATHM_LLAMACPP_BIN")
    if env:
        out.append(Path(env).expanduser())

    # Written by the installer when it downloads or builds the runtime. This is
    # the copy Gathm controls, so it wins over anything on PATH.
    pointer = state_dir() / "llamacpp_bin"
    if pointer.is_file():
        try:
            recorded = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded:
            out.append(Path(recorded).expanduser())

    for name in BIN_NAMES:
        out.append(state_dir() / "llamacpp" / "bin" / name)
        out.append(state_dir() / "llamacpp" / name)

    try:
        import shutil
        for name in BIN_NAMES:
            which = shutil.which(name)
            if which:
                out.append(Path(which))
    except Exception:
        pass

    # Homebrew's two prefixes (Apple silicon and Intel), the usual Unix
    # locations, and where the Windows builds unpack. Cheap enough to check
    # them all rather than branch on a platform string.
    for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        out.append(Path(base) / "llama-server")
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if base:
            for name in BIN_NAMES:
                out.append(Path(base) / "llama.cpp" / name)

    return out


def resolve_binary() -> Path | None:
    """The llama-server to use, or None when the runtime is not installed."""
    for candidate in _candidate_binaries():
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
            # Windows has no executable bit; existence is the whole test.
            if os.name == "nt" and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Locating the weights
# ---------------------------------------------------------------------------

def _gguf_in(directory: Path) -> Path | None:
    """The best .gguf inside a directory, or None.

    Largest wins, because a directory holding both a model and a small
    projector/draft model should resolve to the model. A split model
    (``…-00001-of-00003.gguf``) resolves to its first shard: llama.cpp reads
    the rest itself, and handing it shard 3 is an error.
    """
    try:
        files = [p for p in directory.glob("*.gguf") if p.is_file()]
    except OSError:
        return None
    if not files:
        return None

    shards = [p for p in files if "-of-" in p.name]
    if shards:
        firsts = [p for p in shards if "-00001-of-" in p.name]
        if firsts:
            return sorted(firsts)[0]

    return max(files, key=lambda p: p.stat().st_size)


def resolve_model() -> Path | None:
    """The GGUF weights to serve, or None when nothing is downloaded."""
    env = os.environ.get("GATHM_LLAMACPP_MODEL")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            found = _gguf_in(p)
            if found:
                return found
        elif p.is_file():
            return p

    pointer = state_dir() / "llamacpp_model"
    if pointer.is_file():
        try:
            recorded = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded:
            p = Path(recorded).expanduser()
            if p.is_file():
                return p
            if p.is_dir():
                found = _gguf_in(p)
                if found:
                    return found

    for directory in (models_dir(), state_dir() / "llamacpp" / "models"):
        found = _gguf_in(directory)
        if found:
            return found
    return None


def model_label(model: Path | None = None) -> str:
    """The name this model answers to over the API and shows in the TUI."""
    model = model or resolve_model()
    if model is None:
        return "no-model"
    stem = model.name
    for suffix in (".gguf",):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    # Split models are one model, not three.
    if "-of-" in stem:
        stem = stem.split("-00001-of-")[0].split("-of-")[0]
    return stem


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

def physical_cores() -> int:
    """Physical cores, which is what llama.cpp wants for ``-t``.

    Hyper-threads do not help token generation — it is memory-bandwidth bound,
    and oversubscribing makes it slower, not faster. os.cpu_count() reports
    logical CPUs, so using it directly is the classic way to leave 10-20% of
    the speed behind.
    """
    plat = _platform()
    try:
        if plat == "macos":
            # perflevel0 is the performance-core cluster on Apple silicon.
            # Efficiency cores drag the whole batch down when included.
            for key in ("hw.perflevel0.physicalcpu", "hw.physicalcpu"):
                out = subprocess.run(["sysctl", "-n", key], capture_output=True,
                                     text=True, timeout=3)
                value = out.stdout.strip()
                if out.returncode == 0 and value.isdigit() and int(value) > 0:
                    return int(value)
        elif plat in ("linux", "termux"):
            pairs = set()
            physical = core = None
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("physical id"):
                        physical = line.split(":", 1)[1].strip()
                    elif line.startswith("core id"):
                        core = line.split(":", 1)[1].strip()
                    elif not line.strip():
                        if physical is not None and core is not None:
                            pairs.add((physical, core))
                        physical = core = None
            if physical is not None and core is not None:
                pairs.add((physical, core))
            if pairs:
                return len(pairs)
    except Exception:
        pass

    logical = os.cpu_count() or 4
    # No topology to read (Windows, a container, an ARM board without the
    # physical id fields): assume SMT above two CPUs, which is right far more
    # often than it is wrong.
    return max(1, logical // 2) if logical > 2 else logical


def _has_gpu() -> bool:
    """True when this machine plausibly has a GPU llama.cpp can use."""
    import shutil
    if _platform() == "macos":
        return True  # Metal — every mainstream macOS build of llama.cpp has it
    if shutil.which("nvidia-smi"):
        return True
    if os.path.exists("/dev/kfd"):  # ROCm
        return True
    if shutil.which("rocminfo") or shutil.which("vulkaninfo"):
        return True
    return False


def gpu_layers() -> int:
    """How many layers to offload.

    999 means "all of them" — llama.cpp clamps to the layer count. A CPU-only
    build ignores the flag entirely, which is why asking for offload on a
    machine that turns out not to have it is safe rather than fatal.
    """
    env = os.environ.get("GATHM_LLAMACPP_NGL")
    if env and env.strip().lstrip("-").isdigit():
        return int(env.strip())
    return 999 if _has_gpu() else 0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LlamaCppConfig:
    binary: Path | None
    model: Path | None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    ctx: int = DEFAULT_CTX
    ngl: int = 0
    threads: int = 4
    extra_args: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "LlamaCppConfig":
        host, port = _host_port_from_env()
        try:
            ctx = int(os.environ.get("GATHM_LLAMACPP_CTX", "") or DEFAULT_CTX)
        except ValueError:
            ctx = DEFAULT_CTX
        try:
            threads = int(os.environ.get("GATHM_LLAMACPP_THREADS", "") or 0)
        except ValueError:
            threads = 0
        extra = tuple(shlex.split(os.environ.get("GATHM_LLAMACPP_ARGS", "") or ""))
        return cls(
            binary=resolve_binary(),
            model=resolve_model(),
            host=host,
            port=port,
            ctx=max(256, ctx),
            ngl=gpu_layers(),
            threads=threads if threads > 0 else physical_cores(),
            extra_args=extra,
        )

    # ------------------------------------------------------------------
    @property
    def server_url(self) -> str:
        """Root URL of the server (no /v1)."""
        return "http://%s:%d" % (_reachable_host(self.host), self.port)

    @property
    def base_url(self) -> str:
        """OpenAI-compatible base URL, the thing clients are configured with."""
        override = os.environ.get("GATHM_LLAMACPP_BASE_URL")
        if override:
            return override.rstrip("/")
        return self.server_url + "/v1"


def _reachable_host(host: str) -> str:
    """A bind address is not always somewhere a client can connect to."""
    if host in ("0.0.0.0", "::", "*", ""):
        return "127.0.0.1"
    return host


def _host_port_from_env() -> tuple[str, int]:
    """Host/port, honouring a full GATHM_LLAMACPP_BASE_URL if one is set."""
    base = os.environ.get("GATHM_LLAMACPP_BASE_URL")
    if base:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        if parsed.hostname:
            return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    host = os.environ.get("GATHM_LLAMACPP_HOST", DEFAULT_HOST) or DEFAULT_HOST
    try:
        port = int(os.environ.get("GATHM_LLAMACPP_PORT", "") or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return host, port


def base_url() -> str:
    """The OpenAI-compatible base URL, without building a whole config."""
    host, port = _host_port_from_env()
    override = os.environ.get("GATHM_LLAMACPP_BASE_URL")
    if override:
        return override.rstrip("/")
    return "http://%s:%d/v1" % (_reachable_host(host), port)


# ---------------------------------------------------------------------------
# Process state
# ---------------------------------------------------------------------------

def pid_file() -> Path:
    return state_dir() / "llamacpp.pid"


def log_file() -> Path:
    return state_dir() / "llamacpp.log"


def _read_pid() -> int | None:
    try:
        text = pid_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text.split()[0])
    except (ValueError, IndexError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _terminate(pid: int, force: bool = False) -> None:
    """Signal the server, and anything it spawned.

    The whole process group, not just the pid: _spawn() puts the server in a
    session of its own precisely so terminal signals cannot reach it, which
    also makes it the leader of a group we can take down in one call. A pid-only
    kill leaves helper processes holding the port, and the next start then fails
    to bind for reasons that look nothing like the cause.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []),
                       capture_output=True)
        return
    sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
    try:
        os.killpg(os.getpgid(pid), sig)
        return
    except (OSError, AttributeError):
        pass
    os.kill(pid, sig)


def is_running(cfg: LlamaCppConfig | None = None, timeout: float = 2.0) -> bool:
    """True when a server is answering on the configured port.

    Deliberately a network probe rather than a pid check: a server the user
    started by hand, or one left over from a previous Gathm run, is just as
    usable as one we started, and refusing to see it would mean starting a
    second one that cannot bind the port.
    """
    cfg = cfg or LlamaCppConfig.from_env()
    return _health(cfg, timeout) in ("ok", "loading")


def _health(cfg: LlamaCppConfig, timeout: float = 2.0) -> str:
    """"ok" | "loading" | "down".

    llama-server answers /health with 503 while the weights are still being
    read, and 200 once it can actually generate. Treating 503 as "down" is how
    a start that was merely slow got reported as a failure.
    """
    url = cfg.server_url + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return "ok" if resp.status == 200 else "down"
    except urllib.error.HTTPError as exc:
        # 503 is llama-server's "still reading the weights". Any other status
        # means something is on the port, but it is not a llama-server that is
        # ready — calling that "running" would hide a port collision behind a
        # tick, and then fail on the first question instead.
        return "loading" if exc.code == 503 else "down"
    except Exception:
        return "down"


def served_model(cfg: LlamaCppConfig | None = None, timeout: float = 2.0) -> str | None:
    """The model name a running server reports, or None."""
    cfg = cfg or LlamaCppConfig.from_env()
    try:
        with urllib.request.urlopen(cfg.base_url + "/models", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    try:
        entries = payload.get("data") or []
        if entries:
            name = entries[0].get("id") or ""
            return os.path.basename(name) or None
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Starting and stopping
# ---------------------------------------------------------------------------

def build_command(cfg: LlamaCppConfig, minimal: bool = False) -> list[str]:
    """The llama-server command line.

    ``minimal`` drops every tuning flag and keeps only what the server cannot
    run without. It exists because llama.cpp renames and retires flags between
    releases, and a user's distro build may be a year old: rather than refuse
    to start against an older binary, start() retries with this.
    """
    cmd = [str(cfg.binary), "-m", str(cfg.model),
           "--host", cfg.host, "--port", str(cfg.port)]
    if minimal:
        return cmd

    cmd += [
        "-c", str(cfg.ctx),
        "-t", str(cfg.threads),
        "-ngl", str(cfg.ngl),
        # The name clients see in /v1/models, and what they may send back as
        # "model". Without it the server answers with the full path to the
        # weights, which is neither stable nor pretty in a status bar.
        "-a", model_label(cfg.model),
        # Use the chat template baked into the GGUF, including its tool-call
        # grammar. Without --jinja a template-heavy model (Qwen, Llama 3.x)
        # falls back to a generic prompt and answers noticeably worse.
        "--jinja",
    ]
    cmd += list(cfg.extra_args)
    return cmd


def _spawn(cmd: list[str], log_handle) -> subprocess.Popen:
    """Start the server in its own process group.

    The same lesson the GUI taught this repo: a child sharing our process group
    receives the terminal's Ctrl+C, so quitting Pilot took the model server
    down with it and the next question paid a full model load. A new session
    (POSIX) / detached process group (Windows) keeps it out of the foreground
    group.
    """
    kwargs: dict = {
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def start(cfg: LlamaCppConfig | None = None,
          timeout: float | None = None) -> tuple[bool, str]:
    """Start the server and wait until it answers. Returns (ok, message)."""
    cfg = cfg or LlamaCppConfig.from_env()

    state = _health(cfg, timeout=2)
    if state == "ok":
        return True, "already running at %s" % cfg.base_url
    if state == "loading":
        # Another Gathm (or a server started by hand) is part-way through
        # loading the same weights. Waiting is right; starting a second server
        # would only fail to bind the port.
        return _wait_until_ready(cfg, None, timeout)
    if cfg.binary is None:
        return False, ("llama-server is not installed — run './install' or set "
                       "GATHM_LLAMACPP_BIN")
    if cfg.model is None:
        return False, ("no GGUF model found in %s — run './install' or set "
                       "GATHM_LLAMACPP_MODEL" % models_dir())

    if timeout is None:
        try:
            timeout = float(os.environ.get("GATHM_LLAMACPP_START_TIMEOUT", "")
                            or DEFAULT_START_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_START_TIMEOUT

    state_dir().mkdir(parents=True, exist_ok=True)

    proc: subprocess.Popen | None = None
    for minimal in (False, True):
        cmd = build_command(cfg, minimal=minimal)
        try:
            handle = open(log_file(), "ab")
        except OSError:
            handle = subprocess.DEVNULL  # type: ignore[assignment]
        try:
            if handle is not subprocess.DEVNULL:
                handle.write(("\n=== %s: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                  " ".join(cmd))).encode())
                handle.flush()
            proc = _spawn(cmd, handle)
        except OSError as exc:
            return False, "could not run %s: %s" % (cfg.binary, exc)

        # An unknown flag kills llama-server within milliseconds. Give it a beat
        # and, if it is already gone, drop the tuning flags and try once more
        # rather than reporting a startup failure the user cannot act on.
        time.sleep(1.0)
        if proc.poll() is None:
            break
        if minimal:
            return False, ("llama-server exited immediately — see %s" % log_file())

    if proc is None:  # pragma: no cover - defensive
        return False, "could not start llama-server"

    try:
        pid_file().write_text("%d\n" % proc.pid, encoding="utf-8")
    except OSError:
        pass

    return _wait_until_ready(cfg, proc, timeout)


def _wait_until_ready(cfg: LlamaCppConfig, proc: "subprocess.Popen | None",
                      timeout: float | None) -> tuple[bool, str]:
    """Block until /health says ok, the process dies, or we run out of patience."""
    if timeout is None:
        try:
            timeout = float(os.environ.get("GATHM_LLAMACPP_START_TIMEOUT", "")
                            or DEFAULT_START_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_START_TIMEOUT

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health(cfg, timeout=2) == "ok":
            return True, "listening at %s" % cfg.base_url
        if proc is not None and proc.poll() is not None:
            return False, ("llama-server exited while loading the model — see %s"
                           % log_file())
        time.sleep(1.0)

    name = cfg.model.name if cfg.model else "the model"
    return False, ("llama-server did not finish loading %s within %ds — see %s"
                   % (name, int(timeout), log_file()))


def ensure_running(cfg: LlamaCppConfig | None = None) -> tuple[bool, str]:
    """Start the server if it is not up, unless autostart is switched off."""
    cfg = cfg or LlamaCppConfig.from_env()
    if is_running(cfg, timeout=2):
        return True, "running"
    if (os.environ.get("GATHM_LLAMACPP_AUTOSTART", "1") or "1").strip() in ("0", "false", "no"):
        return False, "not running (autostart disabled)"
    return start(cfg)


def stop(cfg: LlamaCppConfig | None = None) -> tuple[bool, str]:
    """Stop the server Gathm started.

    A server someone else started is left alone, exactly like the launcher's
    rule for Ollama: quitting Gathm is not a reason to take down a process
    Gathm does not own.
    """
    cfg = cfg or LlamaCppConfig.from_env()
    pid = _read_pid()
    try:
        pid_file().unlink()
    except OSError:
        pass

    if pid is None or not _pid_alive(pid):
        if is_running(cfg, timeout=1):
            return False, ("a llama-server is running at %s but Gathm did not start it"
                           % cfg.base_url)
        return True, "not running"

    try:
        _terminate(pid)
    except OSError as exc:
        return False, "could not stop pid %d: %s" % (pid, exc)

    for _ in range(10):
        if not _pid_alive(pid):
            return True, "stopped (pid %d)" % pid
        time.sleep(1.0)

    try:
        _terminate(pid, force=True)
    except OSError:
        pass
    return True, "stopped (pid %d, forced)" % pid


# ---------------------------------------------------------------------------
# CLI — what the bash launcher and `gathm doctor` call
# ---------------------------------------------------------------------------

def _status_lines(cfg: LlamaCppConfig) -> list[str]:
    lines = []
    lines.append("binary   %s" % (cfg.binary or "not found"))
    lines.append("model    %s" % (cfg.model or "not found"))
    lines.append("url      %s" % cfg.base_url)
    lines.append("threads  %d   ctx %d   gpu-layers %d" % (cfg.threads, cfg.ctx, cfg.ngl))
    lines.append("log      %s" % log_file())
    return lines


def main(argv: list[str]) -> int:
    command = (argv[0] if argv else "status").lower()
    cfg = LlamaCppConfig.from_env()

    if command in ("status", "check"):
        # Exit codes are the interface here — the launcher branches on them.
        #   0 running   1 installed but down   2 not usable (no binary/model)
        if is_running(cfg, timeout=2):
            served = served_model(cfg) or model_label(cfg.model)
            print("running %s at %s" % (served, cfg.base_url))
            return 0
        if cfg.binary is None:
            print("llama-server is not installed")
            return 2
        if cfg.model is None:
            print("no GGUF model found in %s" % models_dir())
            return 2
        print("not running (%s)" % cfg.base_url)
        return 1

    if command == "start":
        ok, message = start(cfg)
        print(message)
        return 0 if ok else 1

    if command == "ensure":
        ok, message = ensure_running(cfg)
        print(message)
        return 0 if ok else 1

    if command == "stop":
        ok, message = stop(cfg)
        print(message)
        return 0 if ok else 1

    if command == "restart":
        stop(cfg)
        ok, message = start(cfg)
        print(message)
        return 0 if ok else 1

    if command in ("url", "base-url"):
        print(cfg.base_url)
        return 0

    if command == "model":
        print(model_label(cfg.model))
        return 0

    if command in ("doctor", "info"):
        for line in _status_lines(cfg):
            print(line)
        print("state    %s" % _health(cfg, timeout=2))
        return 0

    if command in ("command", "cmd"):
        if cfg.binary is None or cfg.model is None:
            print("llama-server or model missing", file=sys.stderr)
            return 2
        print(" ".join(shlex.quote(part) for part in build_command(cfg)))
        return 0

    print(__doc__.strip().splitlines()[1] if __doc__ else "", file=sys.stderr)
    print("usage: llamacpp.py {status|start|ensure|stop|restart|doctor|url|model|command}",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
