"""A small span tracer. Each email gets one trace: stages, LLM calls and tool calls with timing,
tokens and cost. P0 exports JSON; a Langfuse exporter maps the same spans in P1."""

from __future__ import annotations

import itertools
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Literal

from pydantic import BaseModel, Field

from .llm.types import LLMResponse

SpanKind = Literal["stage", "llm", "tool", "policy", "action"]


class Span(BaseModel):
    id: int
    parent_id: int | None
    name: str
    kind: SpanKind
    start_ms: float
    duration_ms: float = 0.0
    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Tracer:
    def __init__(self, trace_id: str, redactor: Callable[[Any], Any] | None = None):
        self.trace_id = trace_id
        self.redactor = redactor or (lambda x: x)
        self.spans: list[Span] = []
        self._stack: list[int] = []
        self._ids = itertools.count(1)
        self._t0 = time.perf_counter()

    def _now(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    @contextmanager
    def span(self, name: str, kind: SpanKind = "stage", **attributes: Any) -> Iterator[Span]:
        span = Span(
            id=next(self._ids), parent_id=self._stack[-1] if self._stack else None,
            name=name, kind=kind, start_ms=self._now(), attributes=dict(attributes),
        )
        self.spans.append(span)
        self._stack.append(span.id)
        try:
            yield span
        except Exception as exc:
            span.status, span.error = "error", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.duration_ms = self._now() - span.start_ms
            self._stack.pop()

    def llm_call(self, name: str, response: LLMResponse, **attributes: Any) -> None:
        self.spans.append(Span(
            id=next(self._ids), parent_id=self._stack[-1] if self._stack else None, name=name, kind="llm",
            start_ms=self._now() - response.latency_ms, duration_ms=response.latency_ms,
            attributes={
                "model": response.model, "input_tokens": response.usage.input_tokens,
                "cached_input_tokens": response.usage.cached_input_tokens,
                "output_tokens": response.usage.output_tokens, "cost_usd": response.cost_usd,
                "stop_reason": response.stop_reason, "tool_calls": [c.name for c in response.message.tool_calls],
                **attributes,
            },
        ))

    def export(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": [self.redactor(s.model_dump()) for s in self.spans],
        }
