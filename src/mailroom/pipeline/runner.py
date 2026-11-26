"""process_email: ingest -> triage -> handle -> policy -> actions -> execute, for one email."""

from __future__ import annotations

import time
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..agent.loop import AgentLoop, AgentResult, ToolEvent
from ..domain import InboundEmail, ProposedAction
from ..llm.structured import StructuredOutputError, complete_structured
from ..llm.types import LLMClient, LLMError, Usage
from ..redact import Redactor
from ..tenant import Tenant
from ..tools.base import ToolContext
from ..tracing import Tracer
from . import injection
from .actions import plan_actions
from .executor import Executor
from .ingest import render_for_model
from .policy import Decision, decide, jsonable
from .prompts import handle_prompt, triage_prompt


class LLMSource(Protocol):
    def get(self, alias: str) -> LLMClient: ...


class ProcessingResult(BaseModel):
    tenant: str
    message_id: str
    category: str | None
    triage: dict[str, Any] | None
    fields: dict[str, Any] | None
    reply_body: str | None
    needs_human: bool | None
    agent_status: str | None
    decision: Decision
    actions: list[ProposedAction]
    tool_events: list[ToolEvent] = Field(default_factory=list)
    usage: Usage
    cost_usd: float | None
    latency_ms: float
    llm_calls: int
    minutes_saved: float
    errors: list[str] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)


def _add_cost(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a + b


def process_email(tenant: Tenant, email: InboundEmail, llms: LLMSource, *, dry_run: bool = True) -> ProcessingResult:
    cfg = tenant.config
    started = time.perf_counter()
    log_redactor = Redactor(cfg.privacy.redact_in_logs, cfg.privacy.custom_patterns)
    model_redactor = (
        Redactor(cfg.privacy.redact_model_input, cfg.privacy.custom_patterns).text if cfg.privacy.redact_model_input else None
    )
    tracer = Tracer(email.message_id, redactor=log_redactor)
    errors: list[str] = []
    usage, cost, calls = Usage(), 0.0, 0

    with tracer.span("ingest", attachments=[{"file": a.filename, "readable": a.readable} for a in email.attachments]):
        untrusted = "\n".join([email.subject, email.body, *[a.text or "" for a in email.attachments]])
        screen_hits = injection.scan(untrusted)
        rendered = render_for_model(email, model_redactor)

    # ---- triage ---------------------------------------------------------------------------
    triage: dict[str, Any] | None = None
    with tracer.span("triage", model=cfg.models.triage) as span:
        try:
            llm = llms.get(cfg.models.triage)
            obj, responses = complete_structured(llm, triage_prompt(tenant), rendered, tenant.triage_model, max_tokens=400)
            triage = obj.model_dump()
        except StructuredOutputError as exc:
            responses = exc.responses
            errors.append(f"triage: {exc}")
        except LLMError as exc:
            responses = []
            errors.append(f"triage: {exc}")
        for r in responses:
            tracer.llm_call("triage", r)
            usage, cost, calls = usage + r.usage, _add_cost(cost, r.cost_usd), calls + 1
        span.attributes["result"] = triage

    category = triage["category"] if triage else "other"
    spec = cfg.categories[category]

    # ---- handle (agent loop) ----------------------------------------------------------------
    agent: AgentResult | None = None
    if triage is not None and not spec.skip_handle:
        with tracer.span("handle", category=category, model=cfg.models.handle) as span:
            remaining = cfg.policy.max_cost_usd_per_email - (cost or 0.0)
            ctx = ToolContext(tenant=tenant.slug, tenant_root=tenant.root, var_dir=tenant.var_dir, email=email,
                              today=email.received_at.date())
            user = (
                f"{rendered}\n\nTriage notes from an earlier step: {triage['summary']} "
                f"(urgency: {triage['urgency']})"
            )
            try:
                loop = AgentLoop(llms.get(cfg.models.handle), tenant.registry, max_steps=cfg.policy.max_steps,
                                 max_cost_usd=max(remaining, 0.0), tracer=tracer)
                agent = loop.run(
                    system=handle_prompt(tenant, category, email.received_at.date()),
                    user_content=user,
                    tool_names=spec.tools,
                    finish_model=tenant.submission_models[category],
                    ctx=ctx,
                )
            except LLMError as exc:
                errors.append(f"handle: {exc}")
            if agent is not None:
                usage, cost, calls = usage + agent.usage, _add_cost(cost, agent.cost_usd), calls + agent.llm_calls
                if agent.error:
                    errors.append(f"handle: {agent.error}")
                span.attributes.update(status=agent.status, steps=agent.steps, intercepted=len(agent.intercepted))

    # ---- policy, actions, execution ----------------------------------------------------------
    with tracer.span("policy", "policy") as span:
        decision = decide(cfg, email, category, triage, agent, screen_hits=screen_hits, errors=errors)
        span.attributes.update(route=decision.route, risk=decision.risk_score, signals=decision.reasons)
    with tracer.span("actions", "action") as span:
        actions = plan_actions(cfg, email, category, triage, agent, decision)
        Executor(tenant, dry_run=dry_run).run(actions, email)
        span.attributes["actions"] = [{"type": a.type, "mode": a.mode, "status": a.status} for a in actions]

    final = agent.final if agent is not None else None
    return ProcessingResult(
        tenant=tenant.slug,
        message_id=email.message_id,
        category=triage["category"] if triage else None,
        triage=triage,
        fields=jsonable(final.fields.model_dump(mode="python")) if final is not None else None,  # type: ignore[attr-defined]
        reply_body=getattr(final, "reply_body", None) if final is not None else None,
        needs_human=getattr(final, "needs_human", None) if final is not None else None,
        agent_status=agent.status if agent is not None else None,
        decision=decision,
        actions=actions,
        tool_events=agent.tool_events if agent is not None else [],
        usage=usage,
        cost_usd=cost,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        llm_calls=calls,
        minutes_saved=spec.minutes_saved if decision.route == "automated" else 0.0,
        errors=errors,
        trace=tracer.export(),
    )
