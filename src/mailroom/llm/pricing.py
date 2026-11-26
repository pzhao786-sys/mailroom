from __future__ import annotations

from pydantic import BaseModel

from .types import Usage


class Pricing(BaseModel):
    """USD per 1M tokens."""

    input: float
    output: float
    cached_input: float | None = None

    def cost(self, usage: Usage) -> float:
        cached = min(usage.cached_input_tokens, usage.input_tokens)
        uncached = usage.input_tokens - cached
        cached_rate = self.cached_input if self.cached_input is not None else self.input
        return (uncached * self.input + cached * cached_rate + usage.output_tokens * self.output) / 1_000_000
