"""Mask personal data in logs and traces (and optionally in model input)."""

from __future__ import annotations

import re
from typing import Any

BUILTIN_PATTERNS: dict[str, str] = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "ssn": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "card": r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)",
    "bank_account": r"(?i)\b(?:account|acct|routing|aba)\s*(?:no\.?|number|#)?\s*[:#]?\s*\d{6,17}\b",
    "phone": r"(?<![\w])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)",
}
# Order matters: emails first (they contain digits), cards before phones.
ORDER = ["email", "ssn", "card", "bank_account", "phone"]


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class Redactor:
    def __init__(self, kinds: list[str], custom: dict[str, str] | None = None):
        unknown = [k for k in kinds if k not in BUILTIN_PATTERNS and k not in (custom or {})]
        if unknown:
            raise ValueError(f"unknown redaction kinds {unknown}")
        ordered = [k for k in ORDER if k in kinds] + [k for k in kinds if k not in ORDER]
        patterns = {**BUILTIN_PATTERNS, **(custom or {})}
        self.rules = [(k, re.compile(patterns[k])) for k in ordered]

    def text(self, s: str) -> str:
        for kind, rx in self.rules:
            if kind == "card":
                s = rx.sub(lambda m: "[CARD]" if _luhn_ok(re.sub(r"\D", "", m.group())) else m.group(), s)
            else:
                s = rx.sub(f"[{kind.upper()}]", s)
        return s

    def __call__(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self(v) for v in obj]
        return obj
