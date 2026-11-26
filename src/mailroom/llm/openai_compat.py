"""Adapter for the OpenAI chat-completions wire protocol.

One adapter covers OpenAI, DeepSeek, Qwen, vLLM, Ollama, OpenRouter and the author's llm-gateway.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .http import post_json_with_retry
from .pricing import Pricing
from .types import LLMClient, LLMError, LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage

_FINISH = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens"}


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        *,
        alias: str,
        model: str,
        base_url: str,
        api_key: str | None,
        pricing: Pricing | None = None,
        structured_output: str = "json",
        extra_body: dict[str, Any] | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
    ):
        self.alias = alias
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.pricing = pricing
        self.structured_output = structured_output  # type: ignore[assignment]
        self.extra_body = extra_body or {}
        self.max_retries = max_retries
        self._http = http_client or httpx.Client(timeout=timeout)

    # ---- request ---------------------------------------------------------------------------

    def build_payload(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None,
        tool_choice: ToolChoice | None,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            wire.append(_to_wire(m))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": wire,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **self.extra_body,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
            if tool_choice is not None:
                payload["tool_choice"] = (
                    {"type": "function", "function": {"name": tool_choice["name"]}}
                    if isinstance(tool_choice, dict)
                    else tool_choice
                )
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def chat(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload = self.build_payload(
            system, messages, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.perf_counter()
        body = post_json_with_retry(
            self._http, f"{self.base_url}/chat/completions", headers=headers, payload=payload,
            max_retries=self.max_retries,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        return self.parse_response(body, latency_ms)

    # ---- response --------------------------------------------------------------------------

    def parse_response(self, body: dict[str, Any], latency_ms: float) -> LLMResponse:
        try:
            choice = body["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(body)[:300]}") from exc

        calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function", {})
            args_text = fn.get("arguments") or "{}"
            call = ToolCall(id=raw.get("id") or f"call_{len(calls)}", name=fn.get("name", ""))
            try:
                parsed = json.loads(args_text) if isinstance(args_text, str) else args_text
                if not isinstance(parsed, dict):
                    raise ValueError("arguments must be a JSON object")
                call.arguments = parsed
            except ValueError as exc:
                call.raw_arguments = args_text if isinstance(args_text, str) else json.dumps(args_text)
                call.parse_error = str(exc)
            calls.append(call)

        raw_usage = body.get("usage") or {}
        cached = raw_usage.get("prompt_cache_hit_tokens")  # DeepSeek
        if cached is None:
            cached = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)  # OpenAI
        usage = Usage(
            input_tokens=raw_usage.get("prompt_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0),
            cached_input_tokens=cached or 0,
        )
        stop = _FINISH.get(choice.get("finish_reason") or "", "other")
        if calls and stop == "end_turn":
            stop = "tool_use"  # some servers report "stop" alongside tool calls
        return LLMResponse(
            message=Message(role="assistant", content=msg.get("content") or "", tool_calls=calls),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=usage,
            model=body.get("model", self.model),
            latency_ms=latency_ms,
            cost_usd=self.pricing.cost(usage) if self.pricing else None,
        )


def _to_wire(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if m.role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": m.content or None}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        # echo exactly what the model sent, even if it was malformed
                        "arguments": c.raw_arguments if c.raw_arguments is not None else json.dumps(c.arguments),
                    },
                }
                for c in m.tool_calls
            ]
        return out
    return {"role": "user", "content": m.content}
