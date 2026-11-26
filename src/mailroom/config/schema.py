"""The tenant configuration contract. A new client = a new tenant.yaml that validates against this."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..expr import ExprError, compile_expr

FieldType = Literal["str", "int", "float", "bool", "date", "enum", "email", "money", "list"]
ActionType = Literal["record_row", "send_reply", "notify_team", "tool_call"]

# Columns a record row may use besides the category's own fields.
META_COLUMNS = {
    "received_at", "message_id", "sender", "sender_name", "subject", "category", "urgency", "summary",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldSpec(Strict):
    type: FieldType = "str"
    description: str = ""
    required: bool = False
    values: list[str] | None = None
    gt: float | None = None
    ge: float | None = None
    lt: float | None = None
    le: float | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "FieldSpec":
        if self.type == "enum" and not self.values:
            raise ValueError("enum fields need `values`")
        if self.type != "enum" and self.values:
            raise ValueError("`values` is only valid for enum fields")
        if self.pattern:
            re.compile(self.pattern)
        return self


class RuleSpec(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    expr: str
    message: str = ""
    categories: list[str] = Field(default_factory=list)  # empty = applies to every category

    @field_validator("expr")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            compile_expr(v)
        except ExprError as exc:
            raise ValueError(str(exc)) from exc
        return v


class RecordSpec(Strict):
    tab: str
    columns: list[str]


class CategorySpec(Strict):
    description: str
    instructions: str = ""
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    record: RecordSpec | None = None
    reply: Literal["draft", "none"] = "draft"
    # When a required field is missing: ask the sender for it (automated) or hand off to a person.
    on_missing_required: Literal["request_info", "handoff"] = "handoff"
    always_handoff: bool = False
    skip_handle: bool = False  # no agent loop at all (spam, or mail a person handles anyway)
    archive: bool = False  # automated outcome "archived": no reply, no record (spam)
    document_expected: bool = False  # an unreadable attachment is then a strong handoff signal
    validations: list[RuleSpec] = Field(default_factory=list)
    minutes_saved: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> "CategorySpec":
        if self.archive and not self.skip_handle:
            raise ValueError("archive categories must also set skip_handle: true")
        if self.archive and self.always_handoff:
            raise ValueError("a category cannot both archive and always hand off")
        if self.record:
            unknown = [c for c in self.record.columns if c not in self.fields and c not in META_COLUMNS]
            if unknown:
                raise ValueError(f"record columns not defined as fields or meta columns: {unknown}")
        for name in self.fields:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"field name '{name}' must be snake_case")
        return self


class ModelsSpec(Strict):
    triage: str
    handle: str


class CompanySpec(Strict):
    name: str
    description: str
    mailbox: str  # the address replies are sent from
    timezone: str = "America/Chicago"
    signature: str
    reply_style: str = "friendly, concise and professional"


class TableSpec(Strict):
    file: str
    keys: list[str] = Field(min_length=1)
    description: str
    tool_name: str | None = None
    # Owner scoping: only rows whose owner_column matches the sender are visible.
    owner_column: str | None = None
    owner_match: Literal["email", "domain"] = "domain"
    hidden_columns: list[str] = Field(default_factory=list)


class KnowledgeSpec(Strict):
    docs_dir: str | None = None
    tables: dict[str, TableSpec] = Field(default_factory=dict)


class PolicySpec(Strict):
    handoff_threshold: float = 1.0
    low_confidence_below: float = 0.7
    signal_weights: dict[str, float] = Field(default_factory=dict)
    force_handoff_if: list[RuleSpec] = Field(default_factory=list)
    # Action type (or "tool:<name>") -> whether it runs automatically or waits for approval.
    approvals: dict[str, Literal["auto", "approval"]] = Field(
        default_factory=lambda: {"record_row": "auto", "send_reply": "approval", "notify_team": "auto", "tool_call": "approval"}
    )
    max_steps: int = Field(default=8, ge=1, le=30)
    max_cost_usd_per_email: float = Field(default=0.05, gt=0)


class PrivacySpec(Strict):
    redact_in_logs: list[str] = Field(default_factory=lambda: ["email", "phone", "ssn", "card", "bank_account"])
    redact_model_input: list[str] = Field(default_factory=list)
    custom_patterns: dict[str, str] = Field(default_factory=dict)

    @field_validator("custom_patterns")
    @classmethod
    def _regexes(cls, v: dict[str, str]) -> dict[str, str]:
        for name, pattern in v.items():
            re.compile(pattern)
        return v


class ConnectorsSpec(Strict):
    inbox: Literal["fixture", "imap", "gmail"] = "fixture"
    records: Literal["local_csv", "google_sheets"] = "local_csv"
    outbox: Literal["local", "smtp", "gmail"] = "local"
    notify: Literal["log", "slack", "webhook"] = "log"


class TenantConfig(Strict):
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    display_name: str
    locale: str = "en-US"
    secrets_prefix: str = Field(pattern=r"^[A-Z][A-Z0-9_]*_$")
    company: CompanySpec
    models: ModelsSpec
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)  # plugin tools and their settings
    knowledge: KnowledgeSpec = Field(default_factory=KnowledgeSpec)
    categories: dict[str, CategorySpec]
    policy: PolicySpec = Field(default_factory=PolicySpec)
    privacy: PrivacySpec = Field(default_factory=PrivacySpec)
    connectors: ConnectorsSpec = Field(default_factory=ConnectorsSpec)
    prompts: dict[Literal["triage", "handle"], str] = Field(default_factory=dict)  # optional overrides (file paths)

    @model_validator(mode="after")
    def _check(self) -> "TenantConfig":
        if "other" not in self.categories:
            raise ValueError("every tenant needs an 'other' category as the fallback")
        for name in self.categories:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"category name '{name}' must be snake_case")
        for rule in self.policy.force_handoff_if:
            unknown = [c for c in rule.categories if c not in self.categories]
            if unknown:
                raise ValueError(f"rule '{rule.name}' names unknown categories {unknown}")
        return self
