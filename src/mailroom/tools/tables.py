"""Generic record lookup over a tenant's CSV tables, defined entirely in tenant.yaml.

With `owner_column`, a lookup only sees rows that belong to the sender (by email or by domain),
and "belongs to someone else" looks exactly like "not found", so the tool cannot be used to probe
other customers' records.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, create_model

from ..config.schema import TableSpec
from .base import Tool, ToolContext, ToolError


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def normalize_key(value: str) -> str:
    """'LF 2026-1187' == 'lf20261187'; 'PO 55120' == 'PO-55120' == '55120'."""
    key = re.sub(r"[\s\-_.#:]", "", str(value)).upper()
    if re.fullmatch(r"PO\d+", key):
        key = key[2:]
    return key


def owner_matches(owner_value: str, sender: str, mode: str) -> bool:
    owner_value, sender = owner_value.strip().lower(), sender.strip().lower()
    if mode == "email":
        return owner_value == sender
    domain = sender.rsplit("@", 1)[-1]
    return owner_value == domain or owner_value.lstrip("@") == domain


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def make_lookup_tool(table: str, spec: TableSpec, tenant_root: Path) -> Tool:
    rows = load_rows(tenant_root / spec.file)
    if rows:
        missing = [k for k in spec.keys if k not in rows[0]]
        if missing:
            raise ValueError(f"table '{table}' has no columns {missing}")

    fields = {k: (Optional[str], Field(default=None, description=f"exact {k.replace('_', ' ')}")) for k in spec.keys}
    args_model = create_model(f"Lookup{_camel(table)}Args", **fields)  # type: ignore[call-overload]
    hidden = set(spec.hidden_columns) | ({spec.owner_column} if spec.owner_column else set())

    def handler(args: BaseModel, ctx: ToolContext) -> dict:
        given = {k: v for k, v in args.model_dump().items() if v not in (None, "")}
        if not given:
            raise ToolError(f"provide at least one of: {', '.join(spec.keys)}")
        matches = [r for r in rows if all(normalize_key(r.get(k, "")) == normalize_key(v) for k, v in given.items())]
        if spec.owner_column:
            sender = ctx.email.sender if ctx.email else ""
            matches = [r for r in matches if owner_matches(r.get(spec.owner_column, ""), sender, spec.owner_match)]
        if not matches:
            return {"found": False, "message": "No matching record for this sender. Ask them to double-check the reference."}
        return {"found": True, "records": [{k: v for k, v in r.items() if k not in hidden} for r in matches[:5]]}

    return Tool(
        name=spec.tool_name or f"lookup_{table}",
        description=spec.description,
        args_model=args_model,
        handler=handler,
    )
