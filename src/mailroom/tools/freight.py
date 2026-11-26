"""LTL freight quote plugin. All numbers come from the tenant's rate card, so another carrier reuses
this tool with its own `rate_card.yaml`."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolError


class ServiceLevel(BaseModel):
    multiplier: float
    transit_days: str


class WeightBreak(BaseModel):
    min_lbs: float
    multiplier: float


class RateCard(BaseModel):
    currency: str = "USD"
    regions: dict[str, list[str]]
    lane_rate_per_lb: dict[str, dict[str, float]]
    weight_breaks: list[WeightBreak]
    minimum_charge: float
    fuel_surcharge_pct: float
    services: dict[str, ServiceLevel]
    accessorials: dict[str, float]
    max_weight_lbs: float
    quote_valid_days: int = 14

    def region_of(self, state: str) -> str | None:
        state = state.strip().upper()
        return next((r for r, states in self.regions.items() if state in states), None)

    def break_multiplier(self, weight: float) -> float:
        mult = 1.0
        for wb in sorted(self.weight_breaks, key=lambda w: w.min_lbs):
            if weight >= wb.min_lbs:
                mult = wb.multiplier
        return mult


class QuoteArgs(BaseModel):
    origin_state: str = Field(pattern=r"^[A-Za-z]{2}$", description="Two-letter US state code of the pickup location")
    destination_state: str = Field(pattern=r"^[A-Za-z]{2}$", description="Two-letter US state code of the delivery location")
    weight_lbs: float = Field(gt=0, description="Total shipment weight in pounds (convert kg x 2.20462)")
    service: Literal["standard", "expedited"] = "standard"
    accessorials: list[str] = Field(default_factory=list, description="Extra services, e.g. liftgate, residential, inside_delivery")


def quote(card: RateCard, args: QuoteArgs, *, quote_date, seed: str) -> dict:
    origin_region = card.region_of(args.origin_state)
    dest_region = card.region_of(args.destination_state)
    if origin_region is None or dest_region is None:
        bad = args.origin_state if origin_region is None else args.destination_state
        return {"quotable": False, "reason": f"{bad.upper()} is outside our service area. A team member must review."}
    if args.weight_lbs > card.max_weight_lbs:
        return {
            "quotable": False,
            "reason": f"Shipments over {card.max_weight_lbs:,.0f} lbs need a full-truckload quote from the sales team.",
        }
    unknown = [a for a in args.accessorials if a not in card.accessorials]
    if unknown:
        raise ToolError(f"unknown accessorials {unknown}; valid: {sorted(card.accessorials)}")

    rate = card.lane_rate_per_lb[origin_region][dest_region] * card.break_multiplier(args.weight_lbs)
    linehaul = round(max(args.weight_lbs * rate, card.minimum_charge), 2)
    level = card.services[args.service]
    service_adj = round(linehaul * (level.multiplier - 1), 2)
    fuel = round((linehaul + service_adj) * card.fuel_surcharge_pct / 100, 2)
    extras = [{"item": a, "amount": card.accessorials[a]} for a in args.accessorials]
    total = round(linehaul + service_adj + fuel + sum(e["amount"] for e in extras), 2)
    quote_id = "Q-" + hashlib.sha1(f"{seed}|{args.model_dump_json()}".encode()).hexdigest()[:8].upper()
    return {
        "quotable": True,
        "quote_id": quote_id,
        "lane": f"{args.origin_state.upper()} ({origin_region}) -> {args.destination_state.upper()} ({dest_region})",
        "service": args.service,
        "transit_days": level.transit_days,
        "lines": [
            {"item": "linehaul", "amount": linehaul},
            *([{"item": f"{args.service} service", "amount": service_adj}] if service_adj else []),
            {"item": f"fuel surcharge {card.fuel_surcharge_pct}%", "amount": fuel},
            *extras,
        ],
        "total": total,
        "total_formatted": f"${total:,.2f}",
        "currency": card.currency,
        "valid_until": (quote_date + timedelta(days=card.quote_valid_days)).isoformat(),
    }


def make_freight_quote_tool(settings: dict, tenant_root: Path) -> Tool:
    card = RateCard.model_validate(yaml.safe_load((tenant_root / settings["rate_card"]).read_text(encoding="utf-8")))

    def handler(args: QuoteArgs, ctx: ToolContext) -> dict:
        quote_date = ctx.email.received_at.date() if ctx.email else ctx.today
        seed = ctx.email.message_id if ctx.email else "manual"
        return quote(card, args, quote_date=quote_date, seed=seed)

    return Tool(
        name="calculate_freight_quote",
        description=(
            "Price an LTL shipment from the official rate card. Returns the total and line items. "
            "Always use this for prices; never estimate a price yourself."
        ),
        args_model=QuoteArgs,
        handler=handler,  # type: ignore[arg-type]
    )
