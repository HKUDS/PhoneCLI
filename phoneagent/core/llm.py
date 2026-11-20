"""Minimal LLM client used by :class:`phoneagent.core.agent.PhoneAgent`.

Two implementations:

* :class:`OpenAICompatClient` — ``POST {base}/chat/completions`` over plain
  ``requests``, so no ``openai`` SDK is required (works against vLLM,
  DeepSeek, OpenRouter, Ollama, LM Studio, ...).
* :class:`MockLLM` — scripted responses for tests.

The agent only needs two verbs: ``text`` for routing/verification and
``vision`` for the screenshot-driven loop.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, runtime_checkable

import requests


@dataclass
class ToolCall:
    """One function call the model asked for."""

    name: str
    arguments: dict


@dataclass
class Choice:
    """A model turn: either a function call, or text (the answer)."""

    text: str = ""
    call: Optional[ToolCall] = None


class LLMError(RuntimeError):
    """Transport or protocol failure."""


@runtime_checkable
class LLMClient(Protocol):
    def text(self, system: str, user: str, temperature: float = 0.0) -> str:
        """One text completion."""

    def vision(
        self, system: str, user: str, image_png: bytes, temperature: float = 0.0
    ) -> str:
        """One completion with a PNG attached."""


class OpenAICompatClient:
    """Chat-completions client for any OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str = "EMPTY",
        api_base: str = "http://localhost:8002/v1",
        model: str = "Qwen/Qwen2.5-3B-Instruct",
        vision_model: Optional[str] = None,
        # One call that hangs holds up the whole round. 90 s was longer than
        # the server's stall watchdog allowed for a retry, so a slow model
        # looked exactly like a frozen agent.
        timeout: float = 60.0,
        max_tokens: int = 800,
        disable_reasoning: bool = False,
        usage_path: str = "",
        usage_sink=None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.vision_model = vision_model or model
        self.timeout = timeout
        self.max_tokens = max_tokens
        # OpenRouter-specific: Qwen3-style models spend the whole budget on
        # reasoning and return empty content unless this is turned off. Sending
        # the field to any other provider is at best ignored and at worst a 400,
        # so it only goes out when the base URL is OpenRouter's.
        self.disable_reasoning = disable_reasoning and "openrouter.ai" in api_base
        #: Where to append the token counts the provider reports.
        self.usage_path = usage_path
        #: Called with (prompt, completion) for every call, so one task can add
        #: up what it cost while the file keeps the long-term totals.
        self.usage_sink = usage_sink

    # -- public ----------------------------------------------------------

    # -- native tool calling ---------------------------------------------

    def choose(
        self,
        system: str,
        user: str,
        image_png: Optional[bytes] = None,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
    ) -> "Choice":
        """Ask for one tool call, falling back to text when the model answers directly.

        Native tool calling replaces parsing ``Action: tap(0.5, 0.3)`` out of
        prose: the arguments arrive as JSON, so quoting, truncation and
        formatting drift stop being failure modes.
        """
        user_content: object = user
        if image_png is not None:
            user_content = [
                {"type": "text", "text": user},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(image_png).decode()
                    },
                },
            ]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        payload = {
            "model": self.vision_model if image_png is not None else self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.disable_reasoning:
            payload["reasoning"] = {"enabled": False}

        data = self._post(payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc

        calls = message.get("tool_calls") or []
        if calls:
            function = calls[0].get("function", {})
            raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError as exc:
                raise LLMError(f"tool arguments were not JSON: {raw[:200]!r}") from exc
            return Choice(call=ToolCall(name=str(function.get("name", "")), arguments=arguments))
        return Choice(text=str(message.get("content") or ""))

    def text(self, system: str, user: str, temperature: float = 0.0) -> str:
        return self._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            self.model,
            temperature,
        )

    def vision(
        self, system: str, user: str, image_png: bytes, temperature: float = 0.0
    ) -> str:
        data_uri = "data:image/png;base64," + base64.b64encode(image_png).decode()
        return self._chat(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            self.vision_model,
            temperature,
        )

    # -- internals -------------------------------------------------------

    def _record_usage(self, payload: dict, data: dict) -> None:
        """Write down what the provider says this call cost.

        Not every endpoint reports usage, and a missing block is not an error:
        the call simply goes unrecorded rather than breaking the task.
        """
        usage = data.get("usage") or {}
        if not isinstance(usage, dict) or not usage:
            return
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        if not prompt and not completion:
            return
        # the per-task total does not depend on the lifetime log being on
        if self.usage_sink is not None:
            try:
                self.usage_sink(int(prompt), int(completion))
            except Exception as exc:  # noqa: BLE001 - accounting must not fail a task
                print(f"[usage] sink failed: {exc}")
        if not self.usage_path:
            return
        try:
            from ..usage import record

            record(
                self.usage_path,
                model=str(payload.get("model", "")),
                base=self.api_base,
                prompt=int(prompt),
                completion=int(completion),
                kind="vision" if payload.get("messages") and isinstance(
                    payload["messages"][-1].get("content"), list) else "text",
            )
        except Exception as exc:  # noqa: BLE001 - accounting must not fail a task
            print(f"[usage] {type(exc).__name__}: {exc}")

    def _post(self, payload: dict) -> dict:
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except (requests.RequestException, OSError) as exc:
            raise LLMError(f"request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"response was not JSON: {resp.text[:300]}") from exc
        try:
            self._record_usage(payload, data)
        except Exception as exc:  # noqa: BLE001 - accounting must not fail a task
            print(f"[usage] {type(exc).__name__}: {exc}")
        return data

    def _chat(self, messages: list[dict], model: str, temperature: float) -> str:
        url = f"{self.api_base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if self.disable_reasoning:
            payload["reasoning"] = {"enabled": False}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except (requests.RequestException, OSError) as exc:
            raise LLMError(f"request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError(f"unexpected response shape: {resp.text[:300]}") from exc
        # routing and answers go through here rather than _post; accounting has
        # to cover both or the cheap half of every task goes uncounted
        try:
            self._record_usage(payload, data)
        except Exception as exc:  # noqa: BLE001
            print(f"[usage] {type(exc).__name__}: {exc}")
        return content


class MockLLM:
    """Scripted LLM for tests.

    ``text_responses`` / ``vision_responses`` are consumed in order; the last
    one repeats when the queue runs dry, and an explicit :class:`LLMError` is
    raised when a queue is empty and no repeat was provided.
    """

    def __init__(
        self,
        text_responses: Iterable[str] = (),
        vision_responses: Iterable[str] = (),
        repeat_last: bool = True,
    ) -> None:
        self._text = list(text_responses)
        self._vision = list(vision_responses)
        self.repeat_last = repeat_last
        self.calls: list[tuple[str, str, str]] = []

    def text(self, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append(("text", system, user))
        return self._next(self._text, "text")

    def vision(
        self, system: str, user: str, image_png: bytes, temperature: float = 0.0
    ) -> str:
        self.calls.append(("vision", system, user))
        return self._next(self._vision, "vision")

    def _next(self, queue: list[str], kind: str) -> str:
        if not queue:
            raise LLMError(f"MockLLM has no {kind} response queued")
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0] if self.repeat_last else queue.pop(0)

    # -- assertions helpers ---------------------------------------------

    def text_calls(self) -> list[tuple[str, str]]:
        return [(s, u) for kind, s, u in self.calls if kind == "text"]

    def vision_calls(self) -> list[tuple[str, str]]:
        return [(s, u) for kind, s, u in self.calls if kind == "vision"]


class AnthropicClient:
    """Messages-API client for endpoints that speak Anthropic's protocol.

    DeepSeek serves one at ``https://api.deepseek.com/anthropic``: same models,
    same key, but ``/v1/messages`` with ``x-api-key`` instead of
    ``/chat/completions`` with a bearer token. The request and reply shapes
    differ enough (content blocks, tool_use, thinking) that translating on the
    fly would be guesswork, so it is its own client with the same interface.
    """

    #: The version header Anthropic requires; DeepSeek accepts it as is.
    VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.deepseek.com/anthropic",
        model: str = "deepseek-v4-flash",
        vision_model: Optional[str] = None,
        timeout: float = 60.0,
        # Reasoning blocks are part of the reply and count against this; 800 was
        # spent entirely on thinking and left no tool call behind.
        max_tokens: int = 2500,
        usage_path: str = "",
        usage_sink=None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.vision_model = vision_model or model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.usage_path = usage_path
        self.usage_sink = usage_sink

    # -- transport ---------------------------------------------------------

    def _url(self) -> str:
        base = self.api_base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def _post(self, payload: dict) -> dict:
        url = self._url()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.VERSION,
            "content-type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except (requests.RequestException, OSError) as exc:
            raise LLMError(f"request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"response was not JSON: {resp.text[:300]}") from exc
        try:
            self._record_usage(payload, data)
        except Exception as exc:  # noqa: BLE001 - accounting must not fail a task
            print(f"[usage] {type(exc).__name__}: {exc}")
        return data

    def _record_usage(self, payload: dict, data: dict) -> None:
        usage = data.get("usage") or {}
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        if self.usage_sink is not None:
            self.usage_sink(prompt, completion)
        if not self.usage_path:
            return
        from ..usage import record

        record(
            self.usage_path,
            model=str(payload.get("model", "")),
            base=self.api_base,
            prompt=prompt,
            completion=completion,
            kind="vision" if "image" in str(payload.get("messages", ""))[:4000] else "text",
        )

    # -- shapes ------------------------------------------------------------

    @staticmethod
    def _content(system: str, user: str, image_png: Optional[bytes]) -> list:
        blocks: list = [{"type": "text", "text": user}]
        if image_png is not None:
            blocks.insert(0, {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image_png).decode(),
                },
            })
        return blocks

    @staticmethod
    def _tools(tools: Optional[list[dict]]) -> list:
        """Our OpenAI-shaped specs, in Anthropic's shape."""
        out = []
        for spec in tools or []:
            fn = spec.get("function", spec)
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _text_of(data: dict) -> str:
        parts = [b.get("text", "") for b in data.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts).strip()

    def _ask(self, system: str, user: str, image_png: Optional[bytes],
             tools: Optional[list[dict]], model: str) -> dict:
        payload: dict = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": self._content(system, user, image_png)}],
        }
        if system:
            payload["system"] = system
        converted = self._tools(tools)
        if converted:
            payload["tools"] = converted
            payload["tool_choice"] = {"type": "auto"}
        return self._post(payload)

    # -- interface ---------------------------------------------------------

    def text(self, system: str, user: str, temperature: float = 0.0) -> str:
        return self._text_of(self._ask(system, user, None, None, self.model))

    def vision(self, system: str, user: str, image_png: bytes,
               temperature: float = 0.0) -> str:
        return self._text_of(self._ask(system, user, image_png, None, self.vision_model))

    def choose(
        self,
        system: str,
        user: str,
        image_png: Optional[bytes] = None,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
    ) -> Choice:
        model = self.vision_model if image_png is not None else self.model
        data = self._ask(system, user, image_png, tools, model)
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return Choice(call=ToolCall(name=str(block.get("name", "")),
                                            arguments=dict(block.get("input") or {})))
        return Choice(text=self._text_of(data))

