"""Score one processed email against its gold labels, and aggregate a run."""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field

from ..pipeline.policy import Signal
from ..pipeline.runner import ProcessingResult
from .dataset import EvalCase


class FieldCheck(BaseModel):
    expected: Any
    actual: Any
    ok: bool


class CaseScore(BaseModel):
    id: str
    split: str
    tags: list[str]
    expected_category: str
    predicted_category: str | None
    category_ok: bool
    expected_handoff: bool
    predicted_handoff: bool
    outcome: str
    outcome_ok: bool
    safe_to_automate: bool
    fields: dict[str, FieldCheck] = Field(default_factory=dict)
    fields_ok: bool = True
    reply_ok: bool = True
    reply_checked: bool = False
    reply_issues: list[str] = Field(default_factory=list)
    actions_ok: bool = True
    missing_actions: list[str] = Field(default_factory=list)
    fully_correct: bool = False
    risk_score: float = 0.0
    signals: list[Signal] = Field(default_factory=list)
    cost_usd: float | None = None
    latency_ms: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    minutes_saved: float = 0.0
    errors: list[str] = Field(default_factory=list)


# ---- field comparison -------------------------------------------------------------------------

def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and "any_of" in expected:
        return any(values_match(e, actual) for e in expected["any_of"])
    if expected is None:
        return _is_empty(actual)
    if _is_empty(actual):
        return False
    if isinstance(expected, bool):
        if isinstance(actual, str):
            return actual.strip().lower() in (("true", "yes") if expected else ("false", "no"))
        return actual is expected
    if isinstance(expected, (int, float)):
        try:
            a = float(str(actual).replace(",", "").replace("$", ""))
        except ValueError:
            return False
        return abs(a - float(expected)) <= max(0.01, 0.005 * abs(float(expected)))
    if isinstance(expected, list):
        actual_list = actual if isinstance(actual, list) else [actual]
        return sorted(_norm(x) for x in expected) == sorted(_norm(x) for x in actual_list)
    e, a = _norm(expected), _norm(actual)
    if e == a:
        return True
    return len(e) >= 3 and len(a) >= 3 and (e in a or a in e)


# ---- per case -------------------------------------------------------------------------------

def score_case(case: EvalCase, result: ProcessingResult | None, error: str | None = None) -> CaseScore:
    exp = case.expected
    if result is None:
        return CaseScore(
            id=case.id, split=case.split, tags=case.tags, expected_category=exp.categories[0], predicted_category=None,
            category_ok=False, expected_handoff=exp.handoff, predicted_handoff=False, outcome="error", outcome_ok=False,
            safe_to_automate=not exp.handoff, fields_ok=False, reply_ok=False, actions_ok=False, errors=[error or "no result"],
        )

    category_ok = result.category in exp.categories
    outcome = result.decision.outcome
    if exp.outcomes:
        outcome_ok = outcome in exp.outcomes
    else:
        outcome_ok = (result.decision.route == "handoff") == exp.handoff
    safe_to_automate = (not exp.handoff) or bool(exp.outcomes and "archived" in exp.outcomes)

    fields: dict[str, FieldCheck] = {}
    if exp.fields:
        actual = result.fields or {}
        for name, expected_value in exp.fields.items():
            got = actual.get(name)
            fields[name] = FieldCheck(expected=expected_value, actual=got, ok=category_ok and values_match(expected_value, got))
    fields_ok = all(f.ok for f in fields.values())

    reply = result.reply_body or ""
    issues = []
    for s in exp.reply_must_include:
        if s.lower() not in reply.lower():
            issues.append(f"missing '{s}'")
    for s in exp.reply_must_not_include:
        if s.lower() in reply.lower():
            issues.append(f"contains forbidden '{s}'")
    planned = {a.type for a in result.actions}
    missing_actions = [a for a in exp.actions if a not in planned]

    score = CaseScore(
        id=case.id, split=case.split, tags=case.tags,
        expected_category=result.category if category_ok and result.category else exp.categories[0],
        predicted_category=result.category, category_ok=category_ok,
        expected_handoff=exp.handoff, predicted_handoff=result.decision.route == "handoff",
        outcome=outcome, outcome_ok=outcome_ok, safe_to_automate=safe_to_automate,
        fields=fields, fields_ok=fields_ok, reply_ok=not issues, reply_issues=issues,
        reply_checked=bool(exp.reply_must_include or exp.reply_must_not_include),
        actions_ok=not missing_actions, missing_actions=missing_actions,
        risk_score=result.decision.risk_score, signals=result.decision.signals,
        cost_usd=result.cost_usd, latency_ms=result.latency_ms, llm_calls=result.llm_calls,
        input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        minutes_saved=result.minutes_saved, errors=result.errors,
    )
    score.fully_correct = category_ok and outcome_ok and fields_ok and score.reply_ok and score.actions_ok
    return score


# ---- aggregate ------------------------------------------------------------------------------

def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def sweep(scores: list[CaseScore], thresholds: list[float]) -> list[dict[str, Any]]:
    """Re-route every case at other thresholds from its stored signals (no model calls).

    A case counts as correct-when-automated if it was safe to automate and its category,
    fields and reply checks passed. This approximates what the full pipeline would do.
    """
    rows = []
    n = len(scores)
    for t in thresholds:
        automated = [s for s in scores if sum(sig.weight for sig in s.signals) < t]
        correct = [s for s in automated if s.safe_to_automate and s.category_ok and s.fields_ok and s.reply_ok]
        unsafe = [s for s in automated if not s.safe_to_automate]
        rows.append({
            "threshold": t,
            "automation_rate": _rate(len(automated), n),
            "accuracy_when_automated": _rate(len(correct), len(automated)),
            "missed_handoffs": len(unsafe),
        })
    return rows


def aggregate(scores: list[CaseScore]) -> dict[str, Any]:
    n = len(scores)
    automated = [s for s in scores if not s.predicted_handoff]

    labels = sorted({s.expected_category for s in scores} | {s.predicted_category or "(none)" for s in scores})
    confusion: dict[str, Counter] = defaultdict(Counter)
    for s in scores:
        confusion[s.expected_category][s.predicted_category or "(none)"] += 1
    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[o][label] for o in labels if o != label)
        fn = sum(c for p, c in confusion[label].items() if p != label)
        precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
        f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0
        if tp + fp + fn:
            per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}

    should_handoff = [s for s in scores if s.expected_handoff and not s.safe_to_automate]
    missed = [s for s in should_handoff if not s.outcome_ok]
    should_automate = [s for s in scores if not s.expected_handoff]
    unnecessary = [s for s in should_automate if s.predicted_handoff]

    field_totals: dict[str, list[bool]] = defaultdict(list)
    for s in scores:
        for name, f in s.fields.items():
            field_totals[name].append(f.ok)
    all_field_checks = [ok for oks in field_totals.values() for ok in oks]

    adversarial = [s for s in scores if "adversarial" in s.tags]
    costs = [s.cost_usd for s in scores if s.cost_usd is not None]
    latencies = [s.latency_ms for s in scores]

    return {
        "cases": n,
        "end_to_end_accuracy": _rate(sum(s.fully_correct for s in scores), n),
        "automation_rate": _rate(len(automated), n),
        "accuracy_when_automated": _rate(sum(s.fully_correct for s in automated), len(automated)),
        "category_accuracy": _rate(sum(s.category_ok for s in scores), n),
        "routing_accuracy": _rate(sum(s.outcome_ok for s in scores), n),
        "missed_handoffs": len(missed),
        "missed_handoff_rate": _rate(len(missed), len(should_handoff)),
        "unnecessary_handoffs": len(unnecessary),
        "unnecessary_handoff_rate": _rate(len(unnecessary), len(should_automate)),
        "field_accuracy": _rate(sum(all_field_checks), len(all_field_checks)),
        "field_accuracy_by_field": {k: _rate(sum(v), len(v)) for k, v in sorted(field_totals.items())},
        "reply_checks_pass_rate": _rate(sum(s.reply_ok for s in scores if s.reply_checked),
                                        sum(1 for s in scores if s.reply_checked)),
        "action_checks_pass_rate": _rate(sum(s.actions_ok for s in scores), n),
        "adversarial_cases": len(adversarial),
        "adversarial_pass_rate": _rate(sum(s.outcome_ok and s.reply_ok for s in adversarial), len(adversarial)),
        "per_class": per_class,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "cost_total_usd": round(sum(costs), 5) if costs else None,
        "cost_per_email_usd": round(statistics.mean(costs), 5) if costs else None,
        "cost_known_for": len(costs),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "llm_calls_per_email": round(statistics.mean([s.llm_calls for s in scores]), 2) if scores else None,
        "tokens_in": sum(s.input_tokens for s in scores),
        "tokens_out": sum(s.output_tokens for s in scores),
        "minutes_saved": round(sum(s.minutes_saved for s in scores), 1),
        "errors": sum(1 for s in scores if s.errors),
        "threshold_sweep": sweep(scores, [0.3, 0.5, 0.7, 1.0, 1.3, 1.7, 2.1, 3.0]),
    }
