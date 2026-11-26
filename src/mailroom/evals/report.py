"""Markdown rendering of an eval run."""

from __future__ import annotations

from typing import Any


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _usd(v: float | None, digits: int = 4) -> str:
    return "n/a" if v is None else f"${v:.{digits}f}"


def render_markdown(run: dict[str, Any]) -> str:
    s = run["summary"]
    lines = [
        f"# Eval report: {run['tenant']} ({run['split']})",
        "",
        f"Model: `{run['model']}` | cases: {s['cases']} | run at {run['started_at']} | wall time {run['wall_seconds']} s",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Automation rate (no person needed) | {_pct(s['automation_rate'])} |",
        f"| Accuracy when automated | {_pct(s['accuracy_when_automated'])} |",
        f"| Missed handoffs (should have gone to a person) | {s['missed_handoffs']} ({_pct(s['missed_handoff_rate'])}) |",
        f"| Unnecessary handoffs | {s['unnecessary_handoffs']} ({_pct(s['unnecessary_handoff_rate'])}) |",
        f"| End-to-end accuracy (all checks pass) | {_pct(s['end_to_end_accuracy'])} |",
        f"| Category accuracy | {_pct(s['category_accuracy'])} |",
        f"| Routing accuracy | {_pct(s['routing_accuracy'])} |",
        f"| Field accuracy | {_pct(s['field_accuracy'])} |",
        f"| Reply checks passed | {_pct(s['reply_checks_pass_rate'])} |",
        f"| Planned actions correct | {_pct(s['action_checks_pass_rate'])} |",
        f"| Adversarial cases passed | {_pct(s['adversarial_pass_rate'])} of {s['adversarial_cases']} |",
        f"| Cost per email | {_usd(s['cost_per_email_usd'])} (total {_usd(s['cost_total_usd'])}) |",
        f"| Latency p50 / p95 | {s['latency_p50_ms']} ms / {s['latency_p95_ms']} ms |",
        f"| LLM calls per email | {s['llm_calls_per_email']} |",
        f"| Tokens in / out | {s['tokens_in']:,} / {s['tokens_out']:,} |",
        f"| Staff minutes saved (config estimate) | {s['minutes_saved']} |",
        f"| Cases with pipeline errors | {s['errors']} |",
        "",
        "## Threshold sweep",
        "",
        "Re-routes every case from its stored signals; no model calls. The configured threshold is 1.0.",
        "",
        "| Threshold | Automation rate | Accuracy when automated | Missed handoffs |",
        "|---|---|---|---|",
    ]
    for row in s["threshold_sweep"]:
        lines.append(f"| {row['threshold']} | {_pct(row['automation_rate'])} | {_pct(row['accuracy_when_automated'])} | {row['missed_handoffs']} |")

    lines += ["", "## Categories", "", "| Category | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"]
    for label, m in s["per_class"].items():
        lines.append(f"| {label} | {_pct(m['precision'])} | {_pct(m['recall'])} | {m['f1']:.2f} | {m['support']} |")
    labels = sorted({*s["confusion"].keys(), *(p for row in s["confusion"].values() for p in row)})
    lines += ["", "Confusion matrix (rows = expected, columns = predicted):", "", "| | " + " | ".join(labels) + " |",
              "|---" * (len(labels) + 1) + "|"]
    for exp in labels:
        row = s["confusion"].get(exp, {})
        if row:
            lines.append(f"| **{exp}** | " + " | ".join(str(row.get(p, "")) for p in labels) + " |")

    if s["field_accuracy_by_field"]:
        lines += ["", "## Fields", "", "| Field | Accuracy |", "|---|---|"]
        for name, acc in s["field_accuracy_by_field"].items():
            lines.append(f"| {name} | {_pct(acc)} |")

    failures = [c for c in run["cases"] if not c["fully_correct"]]
    lines += ["", f"## Cases that did not fully pass ({len(failures)})", ""]
    if not failures:
        lines.append("None.")
    for c in failures:
        problems = []
        if not c["category_ok"]:
            problems.append(f"category {c['predicted_category']} (expected {c['expected_category']})")
        if not c["outcome_ok"]:
            problems.append(f"outcome {c['outcome']} (expected {'handoff' if c['expected_handoff'] else 'automated'})")
        bad_fields = [f"{k}={v['actual']!r} (expected {v['expected']!r})" for k, v in c["fields"].items() if not v["ok"]]
        if bad_fields:
            problems.append("fields: " + "; ".join(bad_fields))
        if c["reply_issues"]:
            problems.append("reply: " + "; ".join(c["reply_issues"]))
        if c["missing_actions"]:
            problems.append("missing actions: " + ", ".join(c["missing_actions"]))
        if c["errors"]:
            problems.append("errors: " + "; ".join(e[:160] for e in c["errors"]))
        signals = ", ".join(f"{sig['name']}" for sig in c["signals"]) or "none"
        lines.append(f"- **{c['id']}** - " + " | ".join(problems) + f"  \n  signals: {signals} (risk {c['risk_score']})")
    lines.append("")
    return "\n".join(lines)
