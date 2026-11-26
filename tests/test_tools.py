import pytest

from mailroom.tools.base import ToolContext, ToolError
from mailroom.tools.tables import normalize_key

from .conftest import make_email


def ctx_for(tenant, email):
    return ToolContext(tenant=tenant.slug, tenant_root=tenant.root, var_dir=tenant.var_dir, email=email,
                       today=email.received_at.date())


def call(tenant, name, email, **args):
    tool = tenant.registry.get(name)
    return tool.handler(tool.args_model.model_validate(args), ctx_for(tenant, email))


def test_freight_quote_is_deterministic(freight):
    email = make_email()
    q = call(freight, "calculate_freight_quote", email, origin_state="OH", destination_state="IN", weight_lbs=1800)
    assert q["quotable"] and q["total_formatted"] == "$393.59"
    assert q["valid_until"] == "2026-12-02"
    again = call(freight, "calculate_freight_quote", email, origin_state="oh", destination_state="in", weight_lbs=1800)
    assert again["total"] == q["total"]


def test_freight_quote_limits(freight):
    email = make_email()
    assert not call(freight, "calculate_freight_quote", email, origin_state="OH", destination_state="AZ", weight_lbs=900)["quotable"]
    heavy = call(freight, "calculate_freight_quote", email, origin_state="OH", destination_state="IL", weight_lbs=22000)
    assert not heavy["quotable"] and "truckload" in heavy["reason"]
    with pytest.raises(ToolError):
        call(freight, "calculate_freight_quote", email, origin_state="OH", destination_state="IL", weight_lbs=900, accessorials=["jetpack"])


def test_shipment_lookup_is_owner_scoped(freight):
    own = call(freight, "lookup_shipments", make_email(), tracking_number="LF 2026-1187")
    assert own["found"] and own["records"][0]["status"] == "in_transit"
    assert "customer_domain" not in own["records"][0]  # owner column hidden from the model
    other = call(freight, "lookup_shipments", make_email(sender="mike@blueline-auto.example"), tracking_number="LF20261187")
    assert other == {"found": False, "message": other["message"]}  # indistinguishable from "does not exist"
    by_po = call(freight, "lookup_shipments", make_email(sender="x@oakridge-tools.example"), po_number="PO 77812")
    assert by_po["records"][0]["tracking_number"] == "LF20261192"


def test_lookup_requires_a_key(freight):
    with pytest.raises(ToolError):
        call(freight, "lookup_shipments", make_email())


def test_normalize_key():
    assert normalize_key("LF 2026-1187") == normalize_key("lf20261187")
    assert normalize_key("PO-40211") == normalize_key("40211") == normalize_key("po 40211")


def test_knowledge_base_search(freight):
    hits = call(freight, "search_knowledge_base", make_email(), query="how long do I have to file a damage claim")
    assert hits["results"][0]["source"] == "claims"
    assert "9 months" in " ".join(r["text"] for r in hits["results"])


def test_dental_slots_and_appointments(dental):
    email = make_email(sender="olivia.grant@mailbox.example", received_at=make_email().received_at)
    slots = call(dental, "find_open_slots", email, appointment_type="cleaning", preferred_time="afternoon")
    assert slots["slots"] and all(int(s["start_time"][:2]) >= 12 for s in slots["slots"])
    mine = call(dental, "lookup_my_appointments", email, patient_name="Olivia Grant")
    assert mine["found"] and mine["records"][0]["date"] == "2026-11-24"
    snoop = call(dental, "lookup_my_appointments", make_email(sender="mike.lopez@mailbox.example"), patient_name="Maria Lopez")
    assert not snoop["found"]


def test_side_effect_tool_executes_only_via_handler(dental, tmp_path):
    tool = dental.registry.get("hold_appointment_slot")
    assert tool.side_effect
    email = make_email(sender="nora@mailbox.example")
    out = call(dental, "hold_appointment_slot", email, slot_id="S-1203-1400", patient_name="Nora", appointment_type="consult")
    assert out == {"held": "S-1203-1400"}
    assert (dental.var_dir / "holds.csv").exists()
    with pytest.raises(ToolError):
        call(dental, "hold_appointment_slot", email, slot_id="S-1130-0900", patient_name="Nora", appointment_type="cleaning")
