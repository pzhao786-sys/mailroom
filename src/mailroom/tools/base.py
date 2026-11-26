from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from ..domain import InboundEmail
from ..llm.schema import model_to_tool_schema
from ..llm.types import ToolSpec


class ToolError(Exception):
    """A tool refused or failed in a way the model should read and react to."""


@dataclass
class ToolContext:
    tenant: str
    tenant_root: Path
    var_dir: Path
    email: InboundEmail | None = None
    today: date = field(default_factory=date.today)


Handler = Callable[[BaseModel, ToolContext], Any]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    # Side-effect tools are never run inside the agent loop: the loop records a proposal and the
    # executor runs `handler` after policy and approval.
    side_effect: bool = False

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=model_to_tool_schema(self.args_model))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name '{tool.name}'")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self, names: list[str]) -> list[ToolSpec]:
        return [self._tools[n].spec() for n in names]

    def __contains__(self, name: str) -> bool:
        return name in self._tools
