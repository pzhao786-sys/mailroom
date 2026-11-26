"""Safe evaluation of business-rule expressions written in tenant YAML.

Example rules:
    fields.total > 25000
    category == "complaint" and fields.severity == "high"
    fields.subtotal is None or abs(fields.subtotal + fields.tax - fields.total) <= 0.01

The source is parsed with `ast` and walked by a small interpreter that knows only the node types
below. Names resolve from a context dict; `a.b` is a dictionary lookup, never Python attribute
access. Missing values are None, and arithmetic or ordering on None yields None/False instead of
raising, so a rule about an absent field simply does not fire.
"""

from __future__ import annotations

import ast
import operator
from functools import lru_cache
from typing import Any


class ExprError(ValueError):
    pass


def _lower(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None or needle is None:
        return False
    if isinstance(haystack, str):
        return str(needle).lower() in haystack.lower()
    try:
        return needle in haystack
    except TypeError:
        return False


FUNCTIONS = {
    "abs": abs, "len": len, "min": min, "max": max, "round": round, "lower": _lower, "contains": _contains,
}

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod,
}
_CMPOPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Attribute, ast.Call,
    ast.List, ast.Tuple, ast.Subscript, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    *_BINOPS, *_CMPOPS,
)


@lru_cache(maxsize=512)
def compile_expr(source: str) -> ast.Expression:
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"invalid expression {source!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"{type(node).__name__} is not allowed in rule {source!r}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExprError(f"private names are not allowed in rule {source!r}")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ExprError(f"private names are not allowed in rule {source!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise ExprError(f"only {sorted(FUNCTIONS)} may be called, positionally, in rule {source!r}")
    return tree


def evaluate(source: str, context: dict[str, Any]) -> Any:
    return _eval(compile_expr(source).body, context)


def holds(source: str, context: dict[str, Any]) -> bool:
    return bool(evaluate(source, context))


def _get(container: Any, key: Any) -> Any:
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, (list, tuple)) and isinstance(key, int) and -len(container) <= key < len(container):
        return container[key]
    return None


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in FUNCTIONS:
            raise ExprError(f"'{node.id}' must be called")
        return ctx.get(node.id)
    if isinstance(node, ast.Attribute):
        return _get(_eval(node.value, ctx), node.attr)
    if isinstance(node, ast.Subscript):
        return _get(_eval(node.value, ctx), _eval(node.slice, ctx))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = _eval(v, ctx)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, ctx)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not v
        if v is None:
            return None
        try:
            return -v if isinstance(node.op, ast.USub) else +v
        except TypeError:
            return None
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, ctx), _eval(node.right, ctx)
        if left is None or right is None:
            return None
        try:
            return _BINOPS[type(node.op)](left, right)
        except (TypeError, ZeroDivisionError, ValueError):
            return None
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        args = [_eval(a, ctx) for a in node.args]
        try:
            return FUNCTIONS[node.func.id](*args)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            return None
    raise ExprError(f"unsupported node {type(node).__name__}")


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    if isinstance(op, (ast.In, ast.NotIn)):
        found = _contains(right, left)
        return found if isinstance(op, ast.In) else not found
    if isinstance(op, (ast.Eq, ast.NotEq)):
        if isinstance(left, str) and isinstance(right, str):
            equal = left.strip().lower() == right.strip().lower()
        else:
            equal = left == right
        return equal if isinstance(op, ast.Eq) else not equal
    if left is None or right is None:
        return False
    try:
        return bool(_CMPOPS[type(op)](left, right))
    except TypeError:
        return False
