"""Adapter for the Anthropic Messages API, written against HTTP to show the protocol differences."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .http import post_json_with_retry
from .pricing import Pricing
from .types import LLMClient, LLMError, LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage

API_VERSION = "2023-06-01"
_STOP = {"end_turn": "end_turn", "stop_sequence": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens"}


class AnthropicClient(LLMClient):
    def __init__(
        self,
        *,
        alias: str,
        model: str,
        api_key: str | None,
        base_url: str = "https://api.anthropic.com",
        pricing: Pricing | None = None,
        structured_output: str = "tool",
        extra_body: dict[str, Any] | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
    ):
        self.alias = alias
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.pricing = pricing
        self.structured_output = structured_output  # type: ignore[assignment]
        self.extra_body = extra_body or {}
        self.max_retries = max_retries
        self._http = http_client or httpx.Client(timeout=timeout)

    def build_payload(
        self,
        system: str,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None,
        tool_choice: ToolChoice | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": to_wire_messages(messages),
            **self.extra_body,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
            if tool_choice is not None:
                if isinstance(tool_choice, dict):
                    payload["tool_choice"] = {"type": "tool", "name": tool_choice["name"]}
                else:
                    payload["tool_choice"] = {"type": {"auto": "auto", "required": "any", "none": "none"}[tool_choice]}
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
        json_mode: bool = False,  # not supported by the API; structured output uses a forced tool
    ) -> LLMResponse:
        payload = self.build_payload(
            system, messages, tools=tools, tool_choice=tool_choice, max_tokens=max_tokens, temperature=temperature
        )
        headers = {"content-type": "application/json", "anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        started = time.perf_counter()
        body = post_json_with_retry(
            self._http, f"{self.base_url}/v1/messages", headers=headers, payload=payload, max_retries=self.max_retries
        )
        return self.parse_response(body, (time.perf_counter() - started) * 1000)

    def parse_response(self, body: dict[str, Any], latency_ms: float) -> LLMResponse:
        if "content" not in body:
            raise LLMError(f"unexpected response shape: {str(body)[:300]}")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in body["content"]:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}))
        raw = body.get("usage") or {}
        cache_read = raw.get("cache_read_input_tokens") or 0
        cache_write = raw.get("cache_creation_input_tokens") or 0
        # Anthropic's input_tokens excludes cached tokens; normalise to "total input" like OpenAI.
        usage = Usage(
            input_tokens=(raw.get("input_tokens") or 0) + cache_read + cache_write,
            output_tokens=raw.get("output_tokens") or 0,
            cached_input_tokens=cache_read,
        )
        return LLMResponse(
            message=Message(role="assistant", content="".join(texts), tool_calls=calls),
            stop_reason=_STOP.get(body.get("stop_reason") or "", "other"),  # type: ignore[arg-type]
            usage=usage,
            model=body.get("model", self.model),
            latency_ms=latency_ms,
            cost_usd=self.pricing.cost(usage) if self.pricing else None,
        )


def to_wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert neutral messages to Anthropic's alternating user/assistant content blocks.

    Tool results become `tool_result` blocks inside a *user* message, and consecutive same-role
    messages are merged so the conversation alternates.
    """
    out: list[dict[str, Any]] = []

    def append(role: str, *blocks: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m.role == "tool":
            block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if m.is_error:
                block["is_error"] = True
            append("user", block)
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for c in m.tool_calls:
                blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            append("assistant", *(blocks or [{"type": "text", "text": "(empty)"}]))
        else:
            append("user", {"type": "text", "text": m.content})
    return out
