"""A scripted model for tests: returns queued assistant messages in order and records each request."""

from __future__ import annotations

import itertools
import json
from typing import Any, Callable, Union

from .types import LLMClient, LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage

ScriptItem = Union[Message, Callable[[str, list[Message], "list[ToolSpec] | None"], Message]]

_ids = itertools.count(1)


def text(content: str) -> Message:
    return Message(role="assistant", content=content)


def json_reply(obj: dict[str, Any]) -> Message:
    return Message(role="assistant", content=json.dumps(obj))


def tool(name: str, args: dict[str, Any] | None = None, *, raw: str | None = None, content: str = "") -> Message:
    return Message(role="assistant", content=content, tool_calls=[call(name, args, raw=raw)])


def call(name: str, args: dict[str, Any] | None = None, *, raw: str | None = None) -> ToolCall:
    c = ToolCall(id=f"call_{next(_ids)}", name=name, arguments=args or {})
    if raw is not None:
        c.raw_arguments, c.parse_error, c.arguments = raw, "invalid JSON", {}
    return c


class ScriptedLLM(LLMClient):
    def __init__(
        self,
        script: list[ScriptItem],
        *,
        alias: str = "fake",
        structured_output: str = "json",
        usage: Usage | None = None,
        cost_per_call: float = 0.0001,
    ):
        self.alias = alias
        self.model = alias
        self.structured_output = structured_output  # type: ignore[assignment]
        self.script = list(script)
        self.usage = usage or Usage(input_tokens=500, output_tokens=50)
        self.cost_per_call = cost_per_call
        self.requests: list[dict[str, Any]] = []

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
        self.requests.append(
            {"system": system, "messages": [m.model_copy(deep=True) for m in messages], "tools": tools,
             "tool_choice": tool_choice, "json_mode": json_mode}
        )
        if not self.script:
            raise AssertionError("ScriptedLLM: script exhausted")
        item = self.script.pop(0)
        msg = item(system, messages, tools) if callable(item) else item
        return LLMResponse(
            message=msg,
            stop_reason="tool_use" if msg.tool_calls else "end_turn",
            usage=self.usage,
            model=self.model,
            latency_ms=5.0,
            cost_usd=self.cost_per_call,
        )
