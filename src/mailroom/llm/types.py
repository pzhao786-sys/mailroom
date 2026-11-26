"""Provider-neutral message and response types.

Every adapter converts to and from these, so the agent loop never sees a provider's wire format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set when the provider returned arguments that are not a JSON object; the loop reports it
    # back to the model instead of crashing.
    raw_arguments: str | None = None
    parse_error: str | None = None


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # role == "tool" only
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema, refs inlined


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0  # subset of input_tokens served from the provider's prompt cache

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


StopReason = Literal["end_turn", "tool_use", "max_tokens", "other"]


class LLMResponse(BaseModel):
    message: Message
    stop_reason: StopReason
    usage: Usage
    model: str
    latency_ms: float
    cost_usd: float | None = None  # None when the model has no price configured


class LLMError(RuntimeError):
    """A provider call failed after retries, or returned something unusable."""

    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


ToolChoice = Literal["auto", "required", "none"] | dict[str, str]


class LLMClient(ABC):
    """One configured model. Adapters implement `chat`; everything else builds on it."""

    alias: str
    model: str
    structured_output: Literal["json", "tool"] = "json"

    @abstractmethod
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
        """Send one request. `tool_choice={"name": X}` forces tool X."""
