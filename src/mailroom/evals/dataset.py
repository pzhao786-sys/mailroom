from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .. import REPO_ROOT
from ..domain import InboundEmail
from ..pipeline.ingest import from_fixture

EVALS_DIR = REPO_ROOT / "evals"


class Expected(BaseModel):
    category: str | list[str]
    handoff: bool
    outcomes: list[Literal["processed", "info_requested", "archived", "handoff"]] | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    reply_must_include: list[str] = Field(default_factory=list)
    reply_must_not_include: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return [self.category] if isinstance(self.category, str) else list(self.category)


class EvalCase(BaseModel):
    id: str
    split: Literal["dev", "test"]
    tags: list[str] = Field(default_factory=list)
    email: dict[str, Any]
    expected: Expected


def cases_file(tenant: str) -> Path:
    return EVALS_DIR / tenant / "cases.yaml"


def load_cases(
    tenant: str,
    *,
    split: str | None = "dev",
    ids: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
) -> list[EvalCase]:
    raw = yaml.safe_load(cases_file(tenant).read_text(encoding="utf-8")) or []
    cases = [EvalCase.model_validate(c) for c in raw]
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise ValueError(f"duplicate case id {c.id}")
        seen.add(c.id)
    if split and split != "all":
        cases = [c for c in cases if c.split == split]
    if ids:
        cases = [c for c in cases if c.id in ids]
    if tags:
        cases = [c for c in cases if set(tags) & set(c.tags)]
    return cases[:limit] if limit else cases


def case_email(tenant: str, case: EvalCase) -> InboundEmail:
    return from_fixture(case.email, EVALS_DIR / tenant / "attachments", fallback_id=case.id)
