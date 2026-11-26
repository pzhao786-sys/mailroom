"""Cheap deterministic screens for untrusted text, run before any model sees it.

These complement the triage model's `suspicious` flag; neither is trusted alone. A hit does not
block processing, it adds a policy signal so a person looks at the email.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, list[str]] = {
    "instruction_override": [
        r"\bignore\b.{0,40}\b(instructions|rules|guidelines|prompt)\b",
        r"\bdisregard\b.{0,40}\b(instructions|rules|guidelines|policy)\b",
        r"\b(system prompt|developer message|you are now|act as (an? )?(admin|system))\b",
        r"\b(ai|assistant|bot|automated system|language model)\b.{0,60}\b(must|should|need to|are instructed to)\b",
        r"\bnew instructions?\b",
    ],
    "payment_change": [
        r"\b(new|updated|changed|change of|different)\b.{0,30}\b(bank|banking|remit(tance)?|payment|wire|ach)\b.{0,30}\b(details|information|instructions|account)\b",
        r"\b(bank|banking|remittance|payment)\b.{0,20}\b(details|account)\b.{0,30}\b(have|has)\s+changed\b",
    ],
    "data_exfiltration": [
        r"\b(all|every|full list of|export)\b.{0,40}\b(customers?|shipments?|invoices?|patients?|records|accounts?)\b.{0,40}\b(send|forward|email|list|export)\b",
        r"\b(send|forward|email|list|export)\b.{0,40}\b(all|every)\b.{0,30}\b(customers?|shipments?|invoices?|patients?|records)\b",
    ],
}

_COMPILED = {kind: [re.compile(p, re.IGNORECASE | re.DOTALL) for p in ps] for kind, ps in PATTERNS.items()}


def scan(text: str) -> list[tuple[str, str]]:
    """Return (kind, matched snippet) pairs."""
    hits = []
    for kind, patterns in _COMPILED.items():
        for rx in patterns:
            m = rx.search(text)
            if m:
                hits.append((kind, m.group(0)[:120]))
                break
    return hits
