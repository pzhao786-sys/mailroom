"""Plugin tools that change something outside the conversation.

The agent loop never calls these handlers. It validates the arguments, records a ProposedAction
and tells the model the action is queued; the executor runs the handler after policy and approval.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolError
from .tables import load_rows


def _append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


# ---- Larkspur Freight: damage / loss claims -----------------------------------------------------

class ClaimArgs(BaseModel):
    tracking_number: str = Field(min_length=4)
    claim_type: Literal["damaged", "lost", "shortage"]
    description: str = Field(min_length=5, max_length=600)
    claimed_amount: Optional[float] = Field(default=None, ge=0, description="USD, only if the customer states it")


def make_open_claim_tool(settings: dict, tenant_root: Path) -> Tool:
    def handler(args: ClaimArgs, ctx: ToolContext) -> dict:
        claim_id = "CLM-" + hashlib.sha1(f"{ctx.email.message_id if ctx.email else ''}|{args.tracking_number}".encode()).hexdigest()[:6].upper()
        _append_csv(ctx.var_dir / "claims.csv", {
            "claim_id": claim_id, "opened_at": datetime.now().isoformat(timespec="seconds"),
            "customer": ctx.email.sender if ctx.email else "", **args.model_dump(),
        })
        return {"claim_id": claim_id}

    return Tool(
        name="open_damage_claim",
        description=(
            "Open a freight claim for a damaged, lost or short shipment. Use only when the customer reports "
            "damage, loss or shortage on a specific tracking number. Queued for staff approval."
        ),
        args_model=ClaimArgs,
        handler=handler,  # type: ignore[arg-type]
        side_effect=True,
    )


# ---- Juniper Dental: appointment slots ----------------------------------------------------------

class SlotSearchArgs(BaseModel):
    appointment_type: str = Field(description="cleaning, exam, filling, crown, whitening, consult or emergency")
    earliest_date: Optional[date] = Field(default=None, description="YYYY-MM-DD; defaults to today")
    latest_date: Optional[date] = Field(default=None, description="YYYY-MM-DD")
    preferred_time: Literal["morning", "afternoon", "any"] = "any"
    limit: int = Field(default=3, ge=1, le=5)


def make_find_slots_tool(settings: dict, tenant_root: Path) -> Tool:
    slots_path = tenant_root / settings["slots_file"]

    def handler(args: SlotSearchArgs, ctx: ToolContext) -> dict:
        today = ctx.email.received_at.date() if ctx.email else ctx.today
        start = max(args.earliest_date or today, today)
        kind = args.appointment_type.strip().lower()
        found = []
        for row in load_rows(slots_path):
            d = date.fromisoformat(row["date"])
            if row["status"] != "open" or d < start or (args.latest_date and d > args.latest_date):
                continue
            if kind not in row["appointment_types"].split(";"):
                continue
            hour = int(row["start_time"].split(":")[0])
            if (args.preferred_time == "morning" and hour >= 12) or (args.preferred_time == "afternoon" and hour < 12):
                continue
            found.append({k: row.get(k, "") for k in ("slot_id", "day", "date", "start_time", "end_time", "provider")})
            if len(found) >= args.limit:
                break
        if not found:
            return {"slots": [], "note": "No open slots match. Offer to put the patient on the waitlist."}
        return {"slots": found}

    return Tool(
        name="find_open_slots",
        description="Find open appointment slots by type and date range. Offer only slots this tool returns.",
        args_model=SlotSearchArgs,
        handler=handler,  # type: ignore[arg-type]
    )


class HoldSlotArgs(BaseModel):
    slot_id: str
    patient_name: str
    appointment_type: str
    notes: str = Field(default="", max_length=300)


def make_hold_slot_tool(settings: dict, tenant_root: Path) -> Tool:
    slots_path = tenant_root / settings["slots_file"]

    def handler(args: HoldSlotArgs, ctx: ToolContext) -> dict:
        slots = {r["slot_id"]: r for r in load_rows(slots_path)}
        if args.slot_id not in slots or slots[args.slot_id]["status"] != "open":
            raise ToolError(f"slot {args.slot_id} is not open")
        _append_csv(ctx.var_dir / "holds.csv", {
            "slot_id": args.slot_id, "held_at": datetime.now().isoformat(timespec="seconds"),
            "patient_email": ctx.email.sender if ctx.email else "", **args.model_dump(exclude={"slot_id"}),
        })
        return {"held": args.slot_id}

    return Tool(
        name="hold_appointment_slot",
        description=(
            "Place a 24-hour hold on one slot returned by find_open_slots, for the patient who wrote in. "
            "Queued for front-desk approval; tell the patient the time is held pending confirmation."
        ),
        args_model=HoldSlotArgs,
        handler=handler,  # type: ignore[arg-type]
        side_effect=True,
    )
