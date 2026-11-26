"""Domain objects shared by the pipeline, tools, executor and evals."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config.schema import ActionType


class Attachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    text: str | None = None
    readable: bool = True
    note: str | None = None  # why it is unreadable, or that it was truncated


class InboundEmail(BaseModel):
    message_id: str
    thread_id: str | None = None
    sender: str
    sender_name: str | None = None
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    received_at: datetime
    attachments: list[Attachment] = Field(default_factory=list)

    @property
    def sender_domain(self) -> str:
        return self.sender.rsplit("@", 1)[-1].lower() if "@" in self.sender else ""


ActionStatus = Literal["planned", "dry_run", "pending_approval", "executed", "skipped_duplicate", "rejected", "failed"]


class ProposedAction(BaseModel):
    id: str
    type: ActionType
    summary: str
    payload: dict[str, Any]
    mode: Literal["auto", "approval"]
    status: ActionStatus = "planned"
    idempotency_key: str
    result: dict[str, Any] | None = None
    error: str | None = None

    @staticmethod
    def key_for(tenant: str, message_id: str, action_type: str, payload: dict[str, Any]) -> str:
        blob = json.dumps([tenant, message_id, action_type, payload], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]
