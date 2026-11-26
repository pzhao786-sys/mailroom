"""Run planned actions against sinks, with an idempotency ledger and an approval queue.

P0 sinks are local files under var/<tenant>/ so the whole flow is visible without accounts:
records/<tab>.csv, outbox/*.eml, notifications.jsonl. P1 replaces them with MCP connectors
(Google Sheets, Gmail, Slack, Webhook) behind the same action types.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..domain import InboundEmail, ProposedAction
from ..tenant import Tenant
from ..tools.base import ToolContext

_LOCK = threading.Lock()


class Executor:
    def __init__(self, tenant: Tenant, *, dry_run: bool = True):
        self.tenant = tenant
        self.dry_run = dry_run
        self.var: Path = tenant.var_dir

    # ---- public --------------------------------------------------------------------------

    def run(self, actions: list[ProposedAction], email: InboundEmail) -> list[ProposedAction]:
        for action in actions:
            if action.mode == "approval":
                action.status = "pending_approval"
                if not self.dry_run:
                    self._queue(action, email)
            else:
                self.execute(action, email)
        return actions

    def execute(self, action: ProposedAction, email: InboundEmail) -> ProposedAction:
        if self.dry_run:
            action.status = "dry_run"
            return action
        with _LOCK:
            if action.idempotency_key in self._ledger_keys():
                action.status = "skipped_duplicate"
                return action
            try:
                action.result = self._dispatch(action, email)
                action.status = "executed"
                self._append_jsonl("ledger.jsonl", {"key": action.idempotency_key, "action_id": action.id, "type": action.type})
            except Exception as exc:
                action.status, action.error = "failed", f"{type(exc).__name__}: {exc}"
        return action

    def pending(self) -> list[tuple[ProposedAction, InboundEmail]]:
        done = self._ledger_keys() | {r["key"] for r in self._read_jsonl("rejected.jsonl")}
        items: dict[str, tuple[ProposedAction, InboundEmail]] = {}
        for row in self._read_jsonl("pending.jsonl"):
            action = ProposedAction.model_validate(row["action"])
            if action.idempotency_key not in done:
                items[action.id] = (action, InboundEmail.model_validate(row["email"]))
        return list(items.values())

    def approve(self, action_id: str, approver: str = "cli") -> ProposedAction:
        action, email = self._find_pending(action_id)
        self._append_jsonl("approvals.jsonl", {"action_id": action_id, "key": action.idempotency_key, "approver": approver})
        action.mode = "auto"
        return self.execute(action, email)

    def reject(self, action_id: str, reason: str = "", approver: str = "cli") -> ProposedAction:
        action, _ = self._find_pending(action_id)
        self._append_jsonl("rejected.jsonl", {"action_id": action_id, "key": action.idempotency_key, "reason": reason, "approver": approver})
        action.status = "rejected"
        return action

    # ---- sinks ---------------------------------------------------------------------------

    def _dispatch(self, action: ProposedAction, email: InboundEmail) -> dict[str, Any]:
        p = action.payload
        if action.type == "record_row":
            path = self.var / "records" / (re.sub(r"[^a-z0-9]+", "_", p["tab"].lower()).strip("_") + ".csv")
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(p["row"]))
                if new:
                    writer.writeheader()
                writer.writerow(p["row"])
            return {"file": str(path)}
        if action.type == "send_reply":
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = p["from"], p["to"], p["subject"]
            msg["In-Reply-To"] = f"<{p['in_reply_to']}>"
            msg.set_content(p["body"])
            path = self.var / "outbox" / f"{action.id}.eml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(msg))
            return {"file": str(path)}
        if action.type == "notify_team":
            self._append_jsonl("notifications.jsonl", {"action_id": action.id, **p})
            return {"channel": "log"}
        if action.type == "tool_call":
            tool = self.tenant.registry.get(p["tool"])
            if tool is None or not tool.side_effect:
                raise ValueError(f"{p['tool']} is not a side-effect tool of this tenant")
            ctx = ToolContext(tenant=self.tenant.slug, tenant_root=self.tenant.root, var_dir=self.var, email=email,
                              today=email.received_at.date())
            return {"output": tool.handler(tool.args_model.model_validate(p["arguments"]), ctx)}
        raise ValueError(f"unknown action type {action.type}")

    # ---- storage helpers -----------------------------------------------------------------

    def _queue(self, action: ProposedAction, email: InboundEmail) -> None:
        with _LOCK:
            self._append_jsonl("pending.jsonl", {"action": action.model_dump(mode="json"), "email": email.model_dump(mode="json")})

    def _find_pending(self, action_id: str) -> tuple[ProposedAction, InboundEmail]:
        for action, email in self.pending():
            if action.id == action_id:
                return action, email
        raise KeyError(f"no pending action {action_id}")

    def _ledger_keys(self) -> set[str]:
        return {r["key"] for r in self._read_jsonl("ledger.jsonl")}

    def _append_jsonl(self, name: str, row: dict[str, Any]) -> None:
        self.var.mkdir(parents=True, exist_ok=True)
        with (self.var / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def _read_jsonl(self, name: str) -> list[dict[str, Any]]:
        path = self.var / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
