#!/usr/bin/env python3
"""
Gathm Enterprise — Unified LLM Provider
Single source of truth for model/backend resolution and client construction.

Both Pilot (LangChain) and Engineer (AutoGen) import from here, keeping
model selection, API key lookup, and base-URL config in one place.

Backends
--------
  llamacpp   llama.cpp's own llama-server — the fast local path, and the
             default on macOS, Linux and Windows once './install' has put a
             binary and a GGUF in place. See lib/llamacpp.py.
  ollama     the older local path. Still supported everywhere, and still the
             default on Termux, where llama.cpp is not built.
  gemini     Google's hosted models (free tier)
  anthropic  Claude

Environment variables (in priority order):
  GATHM_LLM_BACKEND   llamacpp | ollama | gemini | anthropic
  GATHM_LLAMACPP_*    see lib/llamacpp.py (model, port, threads, ctx, ...)
  GATHM_OLLAMA_MODEL  or OLLAMA_MODEL               (default: gemma3:12b)
  GATHM_GEMINI_MODEL  or GEMINI_MODEL               (default: gemini-2.0-flash-lite)
  ANTHROPIC_API_KEY                                  (enables anthropic backend)
  GOOGLE_API_KEY      or GEMINI_API_KEY              (enables gemini backend)
  OLLAMA_BASE_URL                                    (default: http://localhost:11434/v1)

File-based overrides (set by install or 'gathm pilot --set-model'):
  ~/.gathm/model           model name override
  ~/.gathm/llm_backend     backend override
  ~/.gathm/llamacpp_model  path to the GGUF weights llama-server should load
  ~/.gathm/llamacpp_bin    path to llama-server itself
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

BACKENDS = ("llamacpp", "ollama", "gemini", "anthropic")


# ---------------------------------------------------------------------------
# lib/llamacpp.py, however this file happens to have been imported
# ---------------------------------------------------------------------------

_LLAMACPP_MODULE: Any = None


def llamacpp_module() -> Any:
    """Import lib/llamacpp.py, or return None if it is not there.

    Three ways in, because this module is imported as ``lib.llm`` from Pilot
    and Engineer, as a bare ``llm`` when lib/ itself is on sys.path, and from
    tests that add neither. Falling back to a path import off __file__ is the
    only spelling that is right in all three.

    Cached, because the last of those three re-executes the module on every
    call — sys.modules does not catch a load by path.
    """
    global _LLAMACPP_MODULE
    if _LLAMACPP_MODULE is not None:
        return _LLAMACPP_MODULE
    try:
        from lib import llamacpp  # type: ignore[import]
        _LLAMACPP_MODULE = llamacpp
        return llamacpp
    except Exception:
        pass
    try:
        import llamacpp  # type: ignore[import]
        _LLAMACPP_MODULE = llamacpp
        return llamacpp
    except Exception:
        pass
    try:
        import importlib.util
        path = Path(__file__).resolve().parent / "llamacpp.py"
        spec = importlib.util.spec_from_file_location("gathm_llamacpp", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _LLAMACPP_MODULE = module
            return module
    except Exception:
        pass
    return None


def _state_dir() -> Path:
    """Where Gathm keeps its config, honouring GATHM_CONFIG_DIR."""
    return Path(os.environ.get("GATHM_CONFIG_DIR") or (Path.home() / ".gathm"))


def _llamacpp_usable() -> bool:
    """True when llama.cpp could serve a question right now.

    Either the server is already answering, or we have both a binary and
    weights and could start it.
    """
    llamacpp = llamacpp_module()
    if llamacpp is None:
        return False
    try:
        if llamacpp.resolve_binary() is not None and llamacpp.resolve_model() is not None:
            return True
        return llamacpp.is_running(timeout=1)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    backend: str        # "llamacpp" | "ollama" | "gemini" | "anthropic"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    # llamacpp only: the GGUF on disk. The server needs the path; everything
    # above this file wants the friendly name in ``model``.
    model_path: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Resolve config from env vars and ~/.gathm/ config files."""
        backend = cls._resolve_backend()
        model = cls._resolve_model(backend)
        api_key: str | None = None
        base_url: str | None = None
        model_path: str | None = None

        if backend == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
        elif backend == "gemini":
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        elif backend == "llamacpp":
            llamacpp = llamacpp_module()
            base_url = llamacpp.base_url() if llamacpp else "http://127.0.0.1:8081/v1"
            if llamacpp:
                weights = llamacpp.resolve_model()
                model_path = str(weights) if weights else None
        else:  # ollama
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

        return cls(backend=backend, model=model, api_key=api_key,
                   base_url=base_url, model_path=model_path)

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_backend() -> str:
        # 1. Env var
        env = os.getenv("GATHM_LLM_BACKEND", "").lower().strip()
        # "llama.cpp" and "llama-cpp" are what people actually type.
        env = {"llama.cpp": "llamacpp", "llama-cpp": "llamacpp",
               "llama_cpp": "llamacpp", "llama-server": "llamacpp"}.get(env, env)
        if env in BACKENDS:
            return env

        # 2. Implicit: if ANTHROPIC_API_KEY is set, use it
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"

        # 3. Implicit: if GOOGLE_API_KEY / GEMINI_API_KEY is set, use gemini
        if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
            return "gemini"

        # 4. ~/.gathm/llm_backend file
        backend_file = _state_dir() / "llm_backend"
        if backend_file.is_file():
            try:
                stored = backend_file.read_text().strip().lower()
            except OSError:
                stored = ""
            if stored in BACKENDS:
                return stored

        # 5. Nothing said otherwise: prefer llama.cpp when it is actually
        # usable. It is the same weights as Ollama would run without the
        # supervisor in front of them, so when both are installed there is no
        # reason to take the slower path — but a half-installed llama.cpp (a
        # binary and no GGUF, say) must fall through rather than strand Pilot
        # on a backend that cannot answer.
        if _llamacpp_usable():
            return "llamacpp"

        return "ollama"

    @staticmethod
    def _resolve_model(backend: str) -> str:
        # llama.cpp is the odd one out: the weights on disk ARE the model, so
        # the file name is ground truth and a stale tag in ~/.gathm/model
        # (left behind by an Ollama install) must not win over it.
        if backend == "llamacpp":
            llamacpp = llamacpp_module()
            if llamacpp is not None:
                weights = llamacpp.resolve_model()
                if weights is not None:
                    return llamacpp.model_label(weights)
            return os.getenv("GATHM_MODEL") or "local-gguf"

        # 1. Backend-specific env vars
        if backend == "gemini":
            m = os.getenv("GATHM_GEMINI_MODEL") or os.getenv("GEMINI_MODEL")
            if m:
                return m
        elif backend == "anthropic":
            m = os.getenv("GATHM_ANTHROPIC_MODEL") or os.getenv("ANTHROPIC_MODEL")
            if m:
                return m
        else:
            m = os.getenv("GATHM_OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL")
            if m:
                return m

        # 2. Generic model env var
        m = os.getenv("GATHM_MODEL")
        if m:
            return m

        # 3. ~/.gathm/model file
        model_file = _state_dir() / "model"
        if model_file.is_file():
            try:
                stored = model_file.read_text().strip()
            except OSError:
                stored = ""
            if stored:
                return stored

        # 4. Hardcoded defaults per backend
        defaults = {
            "gemini": "gemini-2.0-flash-lite",
            "anthropic": "claude-sonnet-4-6",
            "ollama": "gemma3:12b",
        }
        return defaults.get(backend, "gemma3:12b")


# ---------------------------------------------------------------------------
# Provider — thin wrapper that builds framework-specific clients on demand
# ---------------------------------------------------------------------------

class LLMProvider:
    """
    Unified LLM provider. Build one with ``LLMProvider.from_env()`` then call:

    - ``complete(messages)``             — simple one-shot completion
    - ``langchain_chat_model()``         — returns a LangChain BaseChatModel
    - ``autogen_model_client()``         — returns an AutoGen model client
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> "LLMProvider":
        return cls(LLMConfig.from_env())

    # ------------------------------------------------------------------
    # Simple completion (no framework dependency)
    # ------------------------------------------------------------------

    def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        """
        One-shot completion.  ``messages`` is a list of OpenAI-style dicts:
        ``[{"role": "user"|"system"|"assistant", "content": "..."}]``

        Returns the assistant reply as a plain string.
        """
        cfg = self.config
        if cfg.backend == "anthropic":
            return self._complete_anthropic(messages, **kwargs)
        if cfg.backend == "gemini":
            return self._complete_gemini(messages, **kwargs)
        if cfg.backend == "llamacpp":
            self.ensure_ready()
            return self._complete_openai(cfg.base_url or "http://127.0.0.1:8081/v1",
                                         messages, **kwargs)
        return self._complete_openai(cfg.base_url or "http://localhost:11434/v1",
                                     messages, **kwargs)

    # ------------------------------------------------------------------
    def ensure_ready(self) -> tuple[bool, str]:
        """Make the backend answerable, starting a local server if needed.

        Only llama.cpp is Gathm's to start: Ollama runs as a system service
        and the hosted backends are somebody else's uptime. Callers can ignore
        the result — a failure here surfaces as a normal connection error with
        the same advice attached.
        """
        cfg = self.config
        if cfg.backend != "llamacpp":
            return True, "nothing to start"
        llamacpp = llamacpp_module()
        if llamacpp is None:
            return False, "lib/llamacpp.py is missing"
        try:
            return llamacpp.ensure_running()
        except Exception as exc:            # never let a start attempt raise
            return False, "could not start llama-server: %s" % exc

    def _complete_anthropic(self, messages: list[dict], **kwargs) -> str:
        import anthropic  # type: ignore[import]
        client = anthropic.Anthropic(api_key=self.config.api_key)
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        chat_msgs = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model=self.config.model,
            max_tokens=kwargs.get("max_tokens", 4096),
            system=system or anthropic.NOT_GIVEN,
            messages=chat_msgs,
        )
        return resp.content[0].text

    def _complete_gemini(self, messages: list[dict], **kwargs) -> str:
        import google.generativeai as genai  # type: ignore[import]
        genai.configure(api_key=self.config.api_key)
        model = genai.GenerativeModel(self.config.model)
        prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
        resp = model.generate_content(prompt)
        return resp.text

    def _complete_openai(self, base_url: str, messages: list[dict], **kwargs) -> str:
        """One POST to /chat/completions.

        Shared by Ollama and llama.cpp: both speak the OpenAI wire format, and
        the only difference between them is which port answers.
        """
        import urllib.request, json as _json
        base = base_url.rstrip("/")
        body: dict = {"model": self.config.model, "messages": messages, "stream": False}
        if kwargs.get("max_tokens"):
            body["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("temperature") is not None:
            body["temperature"] = kwargs["temperature"]
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer gathm-local"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 300)) as resp:
            data = _json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # LangChain integration (used by Pilot)
    # ------------------------------------------------------------------

    def langchain_chat_model(self) -> Any:
        """Return a LangChain BaseChatModel for the configured backend."""
        cfg = self.config
        if cfg.backend == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
            return ChatGoogleGenerativeAI(model=cfg.model, google_api_key=cfg.api_key)
        if cfg.backend == "anthropic":
            from langchain_anthropic import ChatAnthropic  # type: ignore[import]
            return ChatAnthropic(model=cfg.model, api_key=cfg.api_key)
        if cfg.backend == "llamacpp":
            return self._langchain_llamacpp()
        # Default: Ollama
        from langchain_ollama import ChatOllama  # type: ignore[import]
        # keep_alive: Ollama unloads an idle model after 5 minutes by default,
        # and reloading gemma3 on a phone costs seconds of dead air on the next
        # question. Holding it resident is the single cheapest latency win; the
        # cost is RAM, so it is tunable (Ollama accepts "30m", "1h", "-1" to
        # keep it forever, "0" to unload immediately).
        kwargs: dict = {"model": cfg.model,
                        "keep_alive": os.environ.get("GATHM_OLLAMA_KEEP_ALIVE", "30m")}
        # A runaway generation is worse than a truncated one on CPU-only
        # hardware, but leave both unset unless asked so we do not silently
        # change output length or context handling.
        if os.environ.get("GATHM_OLLAMA_NUM_PREDICT"):
            kwargs["num_predict"] = int(os.environ["GATHM_OLLAMA_NUM_PREDICT"])
        if os.environ.get("GATHM_OLLAMA_NUM_CTX"):
            kwargs["num_ctx"] = int(os.environ["GATHM_OLLAMA_NUM_CTX"])
        return ChatOllama(**kwargs)

    def _langchain_llamacpp(self) -> Any:
        """A LangChain chat model pointed at the local llama-server.

        Starting the server here rather than at import time is deliberate:
        Pilot builds a chat model when it is about to ask something, so this is
        the last moment before the question — and the first moment we know the
        user actually wants the model loaded. `gathm doctor` never gets here.
        """
        cfg = self.config
        self.ensure_ready()
        base = cfg.base_url or "http://127.0.0.1:8081/v1"
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import]
            return ChatOpenAI(
                model=cfg.model,
                base_url=base,
                # llama-server does not check it, but the OpenAI client refuses
                # to send a request without one.
                api_key="gathm-local",
                temperature=float(os.environ.get("GATHM_LLM_TEMPERATURE", "0.7")),
                timeout=float(os.environ.get("GATHM_LLM_TIMEOUT", "300")),
                max_retries=1,
            )
        except ImportError:
            # A checkout newer than its virtualenv. One HTTP POST is not worth
            # refusing to answer over — see lib/chat_openai_compat.py.
            from importlib import import_module
            for name in ("lib.chat_openai_compat", "chat_openai_compat"):
                try:
                    module = import_module(name)
                    break
                except ImportError:
                    module = None
            if module is None:
                import importlib.util
                path = Path(__file__).resolve().parent / "chat_openai_compat.py"
                spec = importlib.util.spec_from_file_location("gathm_chat_compat", path)
                module = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
                spec.loader.exec_module(module)                  # type: ignore[union-attr]
            return module.ChatOpenAICompatible(
                base_url=base,
                model=cfg.model,
                temperature=float(os.environ.get("GATHM_LLM_TEMPERATURE", "0.7")),
                timeout=float(os.environ.get("GATHM_LLM_TIMEOUT", "300")),
            )

    # ------------------------------------------------------------------
    # AutoGen integration (used by Engineer)
    # ------------------------------------------------------------------

    def autogen_model_client(self) -> Any:
        """Return an AutoGen model client for the configured backend."""
        cfg = self.config
        if cfg.backend == "llamacpp":
            self.ensure_ready()
        if cfg.backend == "anthropic":
            from autogen_ext.models.anthropic import AnthropicChatCompletionClient  # type: ignore[import]
            return AnthropicChatCompletionClient(model=cfg.model, api_key=cfg.api_key)
        # Gemini and Ollama both speak the OpenAI-compatible API
        from autogen_ext.models.openai import OpenAIChatCompletionClient  # type: ignore[import]
        if cfg.backend == "gemini":
            return OpenAIChatCompletionClient(
                model=cfg.model,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=cfg.api_key or "NOT_REQUIRED",
                model_info={"vision": False, "function_calling": True,
                            "json_output": True, "family": "gemini",
                            "structured_output": False, "multiple_system_messages": True},
            )
        # llama.cpp and Ollama are both OpenAI-compatible; only the port and
        # the family label differ.
        return OpenAIChatCompletionClient(
            model=cfg.model,
            base_url=cfg.base_url or ("http://127.0.0.1:8081/v1"
                                      if cfg.backend == "llamacpp"
                                      else "http://localhost:11434/v1"),
            api_key="NotRequired",
            model_info={"vision": False, "function_calling": True,
                        "json_output": True, "family": "unknown",
                        "structured_output": False, "multiple_system_messages": True},
        )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"LLMProvider(backend={self.config.backend!r}, model={self.config.model!r})"
