"""Get a validated Pydantic object out of any model.

Two strategies, chosen per model in config/models.yaml:
- "json": JSON mode plus the schema in the system prompt (OpenAI-compatible servers);
- "tool": force a single tool call whose parameters are the schema (Anthropic; also works on OpenAI).
Either way the output is validated, and a failed validation is sent back once for repair.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .schema import model_to_tool_schema
from .types import LLMClient, LLMError, LLMResponse, Message, ToolSpec

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(LLMError):
    def __init__(self, message: str, responses: list[LLMResponse]):
        super().__init__(message)
        self.responses = responses


def format_validation_error(exc: ValidationError, limit: int = 8) -> str:
    parts = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    more = len(exc.errors()) - limit
    if more > 0:
        parts.append(f"... and {more} more")
    return "; ".join(parts)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict:
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply is JSON but not an object")
    return data


def complete_structured(
    llm: LLMClient,
    system: str,
    user_content: str,
    schema: type[T],
    *,
    max_tokens: int = 800,
    max_attempts: int = 2,
    tool_name: str = "respond",
) -> tuple[T, list[LLMResponse]]:
    json_schema = model_to_tool_schema(schema)
    messages = [Message(role="user", content=user_content)]
    responses: list[LLMResponse] = []
    last_error = ""

    if llm.structured_output == "tool":
        tool = ToolSpec(name=tool_name, description="Return the result in the required structure.", parameters=json_schema)
        for _ in range(max_attempts):
            r = llm.chat(system, messages, tools=[tool], tool_choice={"name": tool_name}, max_tokens=max_tokens)
            responses.append(r)
            call = next((c for c in r.message.tool_calls if c.name == tool_name), None)
            if call is None:
                last_error = "model did not call the tool"
                messages += [r.message, Message(role="user", content=f"Call the `{tool_name}` tool with the result.")]
                continue
            try:
                if call.parse_error:
                    raise ValueError(call.parse_error)
                return schema.model_validate(call.arguments), responses
            except (ValidationError, ValueError) as exc:
                last_error = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
                messages += [
                    r.message,
                    Message(
                        role="tool", tool_call_id=call.id, tool_name=tool_name, is_error=True,
                        content=f"Invalid arguments: {last_error}. Call `{tool_name}` again with corrected arguments.",
                    ),
                ]
    else:
        system_json = (
            f"{system}\n\nRespond with one JSON object and nothing else (no prose, no code fences). "
            f"It must match this JSON Schema:\n{json.dumps(json_schema, separators=(',', ':'))}"
        )
        for _ in range(max_attempts):
            r = llm.chat(system_json, messages, json_mode=True, max_tokens=max_tokens)
            responses.append(r)
            text = r.message.content
            try:
                return schema.model_validate(parse_json_object(text)), responses
            except (ValidationError, ValueError) as exc:
                last_error = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
                messages += [
                    Message(role="assistant", content=text),
                    Message(role="user", content=f"That JSON is invalid: {last_error}. Reply with the corrected JSON object only."),
                ]

    raise StructuredOutputError(f"no valid {schema.__name__} after {max_attempts} attempts: {last_error}", responses)
