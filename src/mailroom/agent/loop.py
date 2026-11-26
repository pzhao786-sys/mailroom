"""The hand-written tool-calling loop.

One `run` drives a model through: think -> call tools -> read results -> ... -> call `submit`.

Guarantees, each of which is a common interview question:
- bounded: `max_steps` LLM turns and `max_cost_usd`; hitting either ends the run with a status,
  never an exception, so the pipeline can hand the email to a person;
- every tool call gets exactly one result (Anthropic rejects a conversation otherwise);
- bad arguments (invalid JSON, schema violations, unknown tool) are returned to the model as
  error results so it can correct itself;
- side-effect tools are intercepted: arguments are validated and recorded as proposals, and the
  model is told the action is queued; nothing irreversible happens inside the loop;
- a repeated identical lookup returns the cached result with a note, which breaks loops where a
  model keeps asking the same question;
- plain-text answers get a nudge to call `submit` (models sometimes "answer" in prose).
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.schema import model_to_tool_schema
from ..llm.structured import format_validation_error
from ..llm.types import LLMClient, LLMError, Message, ToolCall, ToolSpec, Usage
from ..tools.base import ToolContext, ToolError, ToolRegistry
from ..tracing import Tracer

RunStatus = Literal["completed", "max_steps", "budget_exceeded", "no_submit", "llm_error"]

NUDGE = (
    "You replied with text only. Do not answer in prose: call the `{finish}` tool now with the fields, "
    "the reply draft (if any) and needs_human."
)


class InterceptedCall(BaseModel):
    id: str
    tool: str
    arguments: dict[str, Any]


class ToolEvent(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    intercepted: bool = False
    cached: bool = False
    result_preview: str = ""
    duration_ms: float = 0.0


class AgentResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: RunStatus
    final: BaseModel | None = Field(default=None, exclude=True)
    intercepted: list[InterceptedCall] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    tool_results: dict[str, Any] = Field(default_factory=dict)  # last successful result per tool
    tool_errors: int = 0
    steps: int = 0
    llm_calls: int = 0
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = 0.0
    messages: list[Message] = Field(default_factory=list)
    error: str | None = None


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        max_steps: int = 8,
        max_cost_usd: float = 0.05,
        max_tokens: int = 1200,
        max_nudges: int = 2,
        result_char_limit: int = 4000,
        tracer: Tracer | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.max_cost_usd = max_cost_usd
        self.max_tokens = max_tokens
        self.max_nudges = max_nudges
        self.result_char_limit = result_char_limit
        self.tracer = tracer

    def run(
        self,
        *,
        system: str,
        user_content: str,
        tool_names: list[str],
        finish_model: type[BaseModel],
        ctx: ToolContext,
        finish_name: str = "submit",
        finish_description: str = "Finish: submit the extracted fields, the reply draft and whether a person must take over.",
    ) -> AgentResult:
        finish_spec = ToolSpec(name=finish_name, description=finish_description, parameters=model_to_tool_schema(finish_model))
        specs = self.registry.specs(tool_names) + [finish_spec]
        allowed = set(tool_names)
        result = AgentResult(status="max_steps", messages=[Message(role="user", content=user_content)])
        cache: dict[str, str] = {}
        nudges = 0

        for step in range(1, self.max_steps + 1):
            if result.cost_usd is not None and result.cost_usd >= self.max_cost_usd:
                result.status = "budget_exceeded"
                break
            result.steps = step
            try:
                response = self.llm.chat(system, result.messages, tools=specs, tool_choice="auto", max_tokens=self.max_tokens)
            except LLMError as exc:
                result.status, result.error = "llm_error", str(exc)
                break
            result.llm_calls += 1
            result.usage = result.usage + response.usage
            result.cost_usd = None if (result.cost_usd is None or response.cost_usd is None) else result.cost_usd + response.cost_usd
            if self.tracer:
                self.tracer.llm_call(f"handle.step{step}", response)
            result.messages.append(response.message)

            calls = response.message.tool_calls
            if not calls:
                if nudges >= self.max_nudges:
                    result.status = "no_submit"
                    break
                nudges += 1
                result.messages.append(Message(role="user", content=NUDGE.format(finish=finish_name)))
                continue

            finished: BaseModel | None = None
            for call in calls:
                tool_message, maybe_final = self._dispatch(call, step, allowed, finish_name, finish_model, ctx, cache, result)
                result.messages.append(tool_message)
                if maybe_final is not None and finished is None:
                    finished = maybe_final
            if finished is not None:
                result.final = finished
                result.status = "completed"
                break

        return result

    # ---- one tool call --------------------------------------------------------------------

    def _dispatch(
        self,
        call: ToolCall,
        step: int,
        allowed: set[str],
        finish_name: str,
        finish_model: type[BaseModel],
        ctx: ToolContext,
        cache: dict[str, str],
        result: AgentResult,
    ) -> tuple[Message, BaseModel | None]:
        def reply(content: str, *, error: bool = False) -> Message:
            return Message(role="tool", tool_call_id=call.id, tool_name=call.name, content=content, is_error=error)

        started = time.perf_counter()
        event = ToolEvent(step=step, tool=call.name, arguments=call.arguments, ok=False)
        result.tool_events.append(event)

        def done(msg: Message, final: BaseModel | None = None) -> tuple[Message, BaseModel | None]:
            event.duration_ms = (time.perf_counter() - started) * 1000
            event.result_preview = msg.content[:300]
            if msg.is_error:
                result.tool_errors += 1
            if self.tracer:
                with self.tracer.span(f"tool.{call.name}", "tool", arguments=call.arguments, ok=event.ok,
                                      intercepted=event.intercepted, cached=event.cached,
                                      tool_duration_ms=round(event.duration_ms, 2), preview=event.result_preview):
                    pass
            return msg, final

        if call.parse_error:
            return done(reply(f"Arguments were not valid JSON ({call.parse_error}). Call {call.name} again with a JSON object.", error=True))

        if call.name == finish_name:
            try:
                final = finish_model.model_validate(call.arguments)
            except ValidationError as exc:
                return done(reply(f"Invalid submission: {format_validation_error(exc)}. Fix these and call {finish_name} again.", error=True))
            event.ok = True
            return done(reply("Submitted."), final)

        tool = self.registry.get(call.name)
        if tool is None or call.name not in allowed:
            names = ", ".join(sorted(allowed | {finish_name}))
            return done(reply(f"Unknown tool '{call.name}'. Available tools: {names}.", error=True))

        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return done(reply(f"Invalid arguments for {call.name}: {format_validation_error(exc)}", error=True))

        if tool.side_effect:
            action_id = f"prop_{len(result.intercepted) + 1}"
            result.intercepted.append(InterceptedCall(id=action_id, tool=tool.name, arguments=args.model_dump(mode="json")))
            event.ok, event.intercepted = True, True
            return done(reply(json.dumps({
                "status": "queued_for_review", "action_id": action_id,
                "note": "Not executed yet. Staff will review it; tell the sender it is being processed, not that it is done.",
            })))

        key = f"{tool.name}:{args.model_dump_json()}"
        if key in cache:
            event.ok, event.cached = True, True
            return done(reply(cache[key] + "\n(Note: identical call already made; use this result and move on.)"))

        try:
            output = tool.handler(args, ctx)
        except ToolError as exc:
            return done(reply(f"{call.name} refused: {exc}", error=True))
        except Exception as exc:  # a tool bug must not kill the run
            return done(reply(f"{call.name} failed: {type(exc).__name__}: {exc}", error=True))

        text = json.dumps(output, default=str, ensure_ascii=False)
        if len(text) > self.result_char_limit:
            text = text[: self.result_char_limit] + "... (truncated)"
        cache[key] = text
        result.tool_results[tool.name] = output
        event.ok = True
        return done(reply(text))
