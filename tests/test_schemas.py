import datetime as dt

import pytest
from pydantic import ValidationError

from mailroom.config.schema import FieldSpec
from mailroom.llm.schema import model_to_tool_schema
from mailroom.pipeline.schemas import build_fields_model, build_triage_model


def test_fields_are_optional_and_typed():
    model = build_fields_model("invoice", {
        "total": FieldSpec(type="money", required=True, ge=0),
        "invoice_date": FieldSpec(type="date"),
        "status": FieldSpec(type="enum", values=["open", "paid"]),
        "state": FieldSpec(type="str", pattern="^[A-Z]{2}$"),
    })
    empty = model()
    assert empty.total is None  # the model may say "not stated"
    m = model(total="$1,234.50", invoice_date="2026-11-12", status="open", state="OH")
    assert m.total == 1234.5 and m.invoice_date == dt.date(2026, 11, 12)
    with pytest.raises(ValidationError):
        model(status="void")
    with pytest.raises(ValidationError):
        model(total=-5)
    with pytest.raises(ValidationError):
        model(state="Ohio")


def test_tool_schema_is_flat():
    model = build_fields_model("q", {"weight_lbs": FieldSpec(type="float", gt=0)})
    schema = model_to_tool_schema(model)
    assert "$defs" not in str(schema) and "$ref" not in str(schema)


def test_triage_model_limits_categories():
    triage = build_triage_model(["quote_request", "other"])
    ok = triage(summary="x", category="other", confidence=0.9)
    assert ok.urgency == "normal"
    with pytest.raises(ValidationError):
        triage(summary="x", category="pizza", confidence=0.9)
    with pytest.raises(ValidationError):
        triage(summary="x", category="other", confidence=1.4)
