#!/usr/bin/env python3
"""
Gathm — a minimal LangChain chat model for OpenAI-compatible servers.

Why this exists rather than just using langchain-openai
------------------------------------------------------
``langchain_openai.ChatOpenAI`` is the right client for llama.cpp's server and
is what ``lib/llm.py`` reaches for first. But Gathm is installed by pulling a
git checkout as often as by pip, so the code can be newer than the virtualenv
it runs in: on such a machine the llama.cpp backend would import-error at the
first question and the user would be told to reinstall for what is, in the end,
one HTTP POST.

So this is the fallback. It speaks ``/v1/chat/completions`` over urllib —
nothing outside the standard library beyond ``langchain_core``, which Pilot
already requires — and implements exactly the two entry points Pilot uses:
``invoke()`` and ``stream()``.

It is deliberately not a general OpenAI client: no tool calling, no function
schemas, no async. If you need those, install langchain-openai.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Iterator

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


def _role_of(message: BaseMessage) -> str:
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, HumanMessage):
        return "user"
    # ToolMessage and friends have a .type that already matches, mostly.
    return getattr(message, "type", "user") or "user"


def _as_text(content: Any) -> str:
    """LangChain content can be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "".join(parts)
    return str(content)


class ChatOpenAICompatible(BaseChatModel):
    """Chat model for any server that speaks OpenAI's chat completions API."""

    base_url: str
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 300.0
    api_key: str = "gathm-local"

    @property
    def _llm_type(self) -> str:
        return "gathm-openai-compatible"

    # ------------------------------------------------------------------
    def _payload(self, messages: list[BaseMessage], stream: bool,
                 stop: list[str] | None) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": _role_of(m), "content": _as_text(m.content)}
                         for m in messages],
            "temperature": self.temperature,
            "stream": stream,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if stop:
            payload["stop"] = stop
        return payload

    def _request(self, payload: dict):
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.api_key},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=self.timeout)

    # ------------------------------------------------------------------
    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None,
                  **kwargs: Any) -> ChatResult:
        with self._request(self._payload(messages, False, stop)) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        text = ""
        try:
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        generation = ChatGeneration(message=AIMessage(content=text))
        return ChatResult(generations=[generation],
                          llm_output={"usage": data.get("usage", {}),
                                      "model": data.get("model", self.model)})

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                run_manager: CallbackManagerForLLMRun | None = None,
                **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        with self._request(self._payload(messages, True, stop)) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                    piece = event["choices"][0]["delta"].get("content") or ""
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if not piece:
                    continue
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
                if run_manager is not None:
                    run_manager.on_llm_new_token(piece, chunk=chunk)
                yield chunk
