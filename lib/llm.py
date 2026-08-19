#!/usr/bin/env python3
"""
Gathm Enterprise — Unified LLM Provider
Single source of truth for model/backend resolution and client construction.

Both Pilot (LangChain) and Engineer (AutoGen) import from here, keeping
model selection, API key lookup, and base-URL config in one place.

Environment variables (in priority order):
  GATHM_LLM_BACKEND   ollama | gemini | anthropic   (default: ollama)
  GATHM_OLLAMA_MODEL  or OLLAMA_MODEL               (default: gemma3:12b)
  GATHM_GEMINI_MODEL  or GEMINI_MODEL               (default: gemini-2.0-flash-lite)
  ANTHROPIC_API_KEY                                  (enables anthropic backend)
  GOOGLE_API_KEY      or GEMINI_API_KEY              (enables gemini backend)
  OLLAMA_BASE_URL                                    (default: http://localhost:11434/v1)

File-based overrides (set by install or 'gathm pilot --set-model'):
  ~/.gathm/model           model name override
  ~/.gathm/llm_backend     backend override
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    backend: str        # "ollama" | "gemini" | "anthropic"
    model: str
    api_key: str | None = None
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Resolve config from env vars and ~/.gathm/ config files."""
        backend = cls._resolve_backend()
        model = cls._resolve_model(backend)
        api_key: str | None = None
        base_url: str | None = None

        if backend == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
        elif backend == "gemini":
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        else:  # ollama
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

        return cls(backend=backend, model=model, api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_backend() -> str:
        # 1. Env var
        env = os.getenv("GATHM_LLM_BACKEND", "").lower().strip()
        if env in ("ollama", "gemini", "anthropic"):
            return env

        # 2. Implicit: if ANTHROPIC_API_KEY is set, use it
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"

        # 3. Implicit: if GOOGLE_API_KEY / GEMINI_API_KEY is set, use gemini
        if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
            return "gemini"

        # 4. ~/.gathm/llm_backend file
        backend_file = Path.home() / ".gathm" / "llm_backend"
        if backend_file.is_file():
            stored = backend_file.read_text().strip().lower()
            if stored in ("ollama", "gemini", "anthropic"):
                return stored

        return "ollama"

    @staticmethod
    def _resolve_model(backend: str) -> str:
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
        model_file = Path.home() / ".gathm" / "model"
        if model_file.is_file():
            stored = model_file.read_text().strip()
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
        return self._complete_ollama(messages, **kwargs)

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

    def _complete_ollama(self, messages: list[dict], **kwargs) -> str:
        import urllib.request, json as _json
        base = (self.config.base_url or "http://localhost:11434/v1").rstrip("/")
        payload = _json.dumps({"model": self.config.model, "messages": messages, "stream": False}).encode()
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
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

    # ------------------------------------------------------------------
    # AutoGen integration (used by Engineer)
    # ------------------------------------------------------------------

    def autogen_model_client(self) -> Any:
        """Return an AutoGen model client for the configured backend."""
        cfg = self.config
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
        return OpenAIChatCompletionClient(
            model=cfg.model,
            base_url=cfg.base_url or "http://localhost:11434/v1",
            api_key="NotRequired",
            model_info={"vision": False, "function_calling": True,
                        "json_output": True, "family": "unknown",
                        "structured_output": False, "multiple_system_messages": True},
        )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"LLMProvider(backend={self.config.backend!r}, model={self.config.model!r})"
