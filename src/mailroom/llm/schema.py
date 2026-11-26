"""JSON Schema helpers for tool definitions.

Pydantic emits `$defs` + `$ref` for nested models. Strong providers accept that, but several
OpenAI-compatible servers and small local models handle a flat schema better, so refs are inlined.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel


def model_to_tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return inline_refs(schema)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            raise ValueError("schema nesting too deep (recursive model?)")
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                target = resolve(defs[name], depth + 1)
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return {**target, **extra}
            # Drop "title" annotations (noise for the model) but keep a *property* named "title",
            # whose value is a schema dict rather than a string.
            return {k: resolve(v, depth) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [resolve(v, depth) for v in node]
        return node

    return resolve(schema)
