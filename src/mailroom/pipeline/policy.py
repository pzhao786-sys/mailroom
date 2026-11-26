"""Deterministic policy: turn everything we know about one email into a route.

The model's own confidence is one weak signal among several. Each signal has a weight (defaults
below, overridable per tenant); the sum is the risk score and the email goes to a person when it
reaches the threshold. Signals are stored with the result so evals can re-run this decision at any
threshold without calling a model again.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..agent.loop import AgentResult
from ..config.schema import CategorySpec, TenantConfig
from ..domain import InboundEmail
from ..expr import holds

DEFAULT_WEIGHTS: dict[str, float] = {
    "pipeline_error": 100.0,
    "forced": 100.0,
    "category_requires_human": 1.0,
    "agent_incomplete": 1.0,
    "agent_escalated": 1.0,
    "suspected_injection": 1.0,
    "screen": 1.0,
    "missing_required": 1.0,
    "validation_failed": 1.0,
    "unreadable_attachment": 1.0,
    "no_reply_draft": 1.0,
    "uncertain_archive": 1.0,
    "low_triage_confidence": 0.6,
    "tool_errors": 0.3,
}

Route = Literal["automated", "handoff"]
Outcome = Literal["processed", "info_requested", "archived", "handoff"]


class Signal(BaseModel):
    name: str
    weight: float
    detail: str = ""


class Decision(BaseModel):
    route: Route
    outcome: Outcome
    risk_score: float
    threshold: float
    signals: list[Signal] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    info_requested: bool = False

    @property
    def reasons(self) -> list[str]:
        return [f"{s.name}{': ' + s.detail if s.detail else ''}" for s in self.signals]


def rule_context(
    email: InboundEmail, category: str, triage: dict[str, Any] | None, fields: dict[str, Any], tool_results: dict[str, Any]
) -> dict[str, Any]:
    return {
        "category": category,
        "fields": fields,
        "triage": triage or {},
        "tools": tool_results,
        "email": {
            "sender": email.sender,
            "sender_domain": email.sender_domain,
            "subject": email.subject,
            "body": email.body,
            "attachment_count": len(email.attachments),
        },
        "today": email.received_at.date(),
    }


def decide_route(signals: list[Signal], threshold: float) -> tuple[Route, float]:
    risk = round(sum(s.weight for s in signals), 4)
    return ("handoff" if risk >= threshold else "automated"), risk


def decide(
    config: TenantConfig,
    email: InboundEmail,
    category: str,
    triage: dict[str, Any] | None,
    agent: AgentResult | None,
    *,
    screen_hits: list[tuple[str, str]],
    errors: list[str],
) -> Decision:
    policy = config.policy
    spec: CategorySpec = config.categories[category]
    weights = {**DEFAULT_WEIGHTS, **policy.signal_weights}
    signals: list[Signal] = []

    def add(name: str, detail: str = "") -> None:
        weight = weights.get(name, weights.get(name.split(":")[0], 1.0))
        signals.append(Signal(name=name, weight=weight, detail=detail[:200]))

    for e in errors:
        add("pipeline_error", e)
    if triage is not None and triage.get("confidence", 1.0) < policy.low_confidence_below:
        add("low_triage_confidence", f"{triage['confidence']:.2f}")
        if spec.archive:
            # Archiving means nobody ever sees the email, so doubt about that label alone is enough.
            add("uncertain_archive", "would be archived unseen")

    fields: dict[str, Any] = {}
    missing: list[str] = []
    info_requested = False

    if not spec.archive:  # junk is archived even if it is also phishing; only doubt about the label matters
        if triage and triage.get("suspicious"):
            add("suspected_injection", triage.get("suspicious_reason") or "flagged by triage")
        for kind, snippet in screen_hits:
            add(f"screen:{kind}", snippet)
        if spec.always_handoff:
            add("category_requires_human", category)
        if spec.document_expected:
            unreadable = [a.filename for a in email.attachments if not a.readable]
            if unreadable:
                add("unreadable_attachment", ", ".join(unreadable))

    if agent is not None:
        if agent.status != "completed" or agent.final is None:
            add("agent_incomplete", agent.status + (f": {agent.error}" if agent.error else ""))
        else:
            sub = agent.final
            fields = sub.fields.model_dump(mode="python")  # type: ignore[attr-defined]
            reply = getattr(sub, "reply_body", None)
            if sub.needs_human:  # type: ignore[attr-defined]
                add("agent_escalated", sub.needs_human_reason or "")  # type: ignore[attr-defined]
            missing = [n for n, f in spec.fields.items() if f.required and fields.get(n) in (None, "", [])]
            if missing:
                if spec.on_missing_required == "request_info" and reply:
                    info_requested = True
                else:
                    add("missing_required", ", ".join(missing))
            if spec.reply == "draft" and not reply and not sub.needs_human:  # type: ignore[attr-defined]
                add("no_reply_draft")
            if not info_requested:
                ctx = rule_context(email, category, triage, fields, agent.tool_results)
                for rule in spec.validations:
                    if not holds(rule.expr, ctx):
                        add(f"validation_failed:{rule.name}", rule.message)
            if agent.tool_errors >= 2:
                add("tool_errors", str(agent.tool_errors))

    ctx = rule_context(email, category, triage, fields, agent.tool_results if agent else {})
    for rule in policy.force_handoff_if:
        if rule.categories and category not in rule.categories:
            continue
        if holds(rule.expr, ctx):
            add(f"forced:{rule.name}", rule.message)

    route, risk = decide_route(signals, policy.handoff_threshold)
    if route == "handoff":
        outcome: Outcome = "handoff"
    elif spec.archive:
        outcome = "archived"
    elif info_requested:
        outcome = "info_requested"
    else:
        outcome = "processed"
    return Decision(
        route=route, outcome=outcome, risk_score=risk, threshold=policy.handoff_threshold,
        signals=signals, missing_required=missing, info_requested=info_requested,
    )


def jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value
