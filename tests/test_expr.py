import datetime as dt

import pytest

from mailroom.expr import ExprError, compile_expr, evaluate, holds


def test_comparisons_and_dict_access():
    ctx = {"fields": {"total": 30000, "severity": "High"}, "category": "invoice"}
    assert holds("fields.total > 25000", ctx)
    assert holds('fields.severity == "high"', ctx)  # string equality is case-insensitive
    assert holds('category == "invoice" and fields.total >= 30000', ctx)
    assert not holds("fields.total < 100", ctx)


def test_missing_values_do_not_fire():
    ctx = {"fields": {"subtotal": None, "tax": 5, "total": 10}}
    assert evaluate("fields.subtotal + fields.tax", ctx) is None
    assert not holds("fields.subtotal + fields.tax > 1", ctx)
    assert not holds("fields.nope > 3", ctx)
    assert holds("fields.subtotal is None or abs(fields.subtotal + fields.tax - fields.total) <= 0.01", ctx)


def test_functions_lists_and_dates():
    ctx = {"fields": {"accessorials": ["liftgate"], "a": dt.date(2026, 1, 2), "b": dt.date(2026, 1, 1)}}
    assert holds('"liftgate" in fields.accessorials', ctx)
    assert holds("len(fields.accessorials) == 1", ctx)
    assert holds("fields.a >= fields.b", ctx)
    assert holds('contains("Bank details CHANGED", "changed")', {})


@pytest.mark.parametrize("src", [
    "__import__('os').system('echo hi')",
    "fields.__class__",
    "open('x')",
    "lambda: 1",
    "[x for x in range(3)]",
    "abs(x=1)",
])
def test_rejects_unsafe_syntax(src):
    with pytest.raises(ExprError):
        compile_expr(src)


def test_syntax_error_is_reported():
    with pytest.raises(ExprError):
        compile_expr("fields.total >")
