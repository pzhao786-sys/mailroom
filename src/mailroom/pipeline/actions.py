"""Plan actions from a decision. The model's output is data; this turns it into side effects
that the executor may run, each marked auto or needs-approval by tenant policy."""

from __future__ import annotations

import re
from typing import Any

from ..agent.loop import AgentResult
from ..config.schema import TenantConfig
from ..domain import InboundEmail, ProposedAction
from .policy import Decision, jsonable


def _cell(value: Any) -> str:
    value = jsonable(value)
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


_VALEDICTION = re.compile(
    r"^(best|kind|warm|warmest)?\s*(regards|wishes)|^(many\s+)?thanks\b|^thank you\b|^sincerely|^cheers"
    r"|^respectfully|^all the best|^take care|^looking forward|^talk soon|^see you soon",
    re.IGNORECASE,
)


def strip_signoff(body: str, company: str) -> str:
    """Remove closing lines the model added itself ("Best regards", "Thanks for your patience,",
    "Larkspur Freight Co.") so the configured signature is the only one. Measured on real DeepSeek
    output: 26 of 43 dev-set replies ended with their own closing despite the prompt saying not to."""
    lines = body.rstrip().splitlines()
    removed = 0
    while lines and removed < 3:
        raw = lines[-1].strip()
        last = raw.rstrip(",.!")
        if not last:
            lines.pop()
            continue
        # a closing is short and does not end like a sentence ("Thanks for flagging this." stays)
        closing = len(last) <= 40 and not raw.endswith((".", "?")) and bool(_VALEDICTION.match(last))
        is_name = len(last) <= 60 and (company.lower().rstrip(".") in last.lower() or last.lower().endswith(" team"))
        if closing or is_name:
            lines.pop()
            removed += 1
        else:
            break
    return "\n".join(lines).rstrip()


def reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def plan_actions(
    config: TenantConfig,
    email: InboundEmail,
    category: str,
    triage: dict[str, Any] | None,
    agent: AgentResult | None,
    decision: Decision,
) -> list[ProposedAction]:
    if decision.outcome == "archived":
        return []
    spec = config.categories[category]
    approvals = config.policy.approvals

    def make(action_type: str, summary: str, payload: dict[str, Any], tool: str | None = None) -> ProposedAction:
        mode = approvals.get(f"tool:{tool}") if tool else None
        mode = mode or approvals.get(action_type, "approval")
        key = ProposedAction.key_for(config.tenant, email.message_id, action_type, payload)
        return ProposedAction(
            id=f"act_{key[:12]}", type=action_type, summary=summary, payload=payload, mode=mode,  # type: ignore[arg-type]
            idempotency_key=key,
        )

    sub = agent.final if agent is not None else None
    fields = jsonable(sub.fields.model_dump(mode="python")) if sub is not None else {}  # type: ignore[attr-defined]
    reply = getattr(sub, "reply_body", None) if sub is not None else None

    if decision.route == "handoff":
        return [make("notify_team", f"Hand off to a person: {category}", {
            "category": category,
            "sender": email.sender,
            "subject": email.subject,
            "summary": (triage or {}).get("summary"),
            "risk_score": decision.risk_score,
            "reasons": decision.reasons,
            "extracted_fields": fields,
            "suggested_reply": reply,  # a head start for the person, never sent automatically
        })]

    actions: list[ProposedAction] = []
    if spec.record and decision.outcome == "processed":
        meta = {
            "received_at": email.received_at.isoformat(), "message_id": email.message_id, "sender": email.sender,
            "sender_name": email.sender_name, "subject": email.subject, "category": category,
            "urgency": (triage or {}).get("urgency"), "summary": (triage or {}).get("summary"),
        }
        row = {col: _cell(fields[col] if col in spec.fields else meta.get(col)) for col in spec.record.columns}
        actions.append(make("record_row", f"Add a row to '{spec.record.tab}'", {"tab": spec.record.tab, "row": row}))
    if reply:
        actions.append(make("send_reply", f"Reply to {email.sender}", {
            "from": config.company.mailbox,
            "to": email.sender,  # bound by the host: the model cannot choose a recipient
            "subject": reply_subject(email.subject),
            "in_reply_to": email.message_id,
            "body": f"{strip_signoff(reply, config.company.name)}\n\n{config.company.signature.strip()}",
        }))
    for call in agent.intercepted if agent is not None else []:
        actions.append(make("tool_call", f"{call.tool}", {"tool": call.tool, "arguments": call.arguments}, tool=call.tool))
    return actions
