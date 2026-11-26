from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mailroom.domain import InboundEmail
from mailroom.tenant import load_tenant


@pytest.fixture
def freight(tmp_path):
    return load_tenant("larkspur_freight", var_root=tmp_path)


@pytest.fixture
def dental(tmp_path):
    return load_tenant("juniper_dental", var_root=tmp_path)


def make_email(**kw) -> InboundEmail:
    base = dict(
        message_id="msg-test-1",
        sender="dana@brightwater-supply.example",
        sender_name="Dana",
        subject="Rate request",
        body="Quote 1,800 lbs Columbus OH to Indianapolis IN, standard.",
        received_at=datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return InboundEmail(**base)


class StaticLLMs:
    """LLMSource that returns the same client for every alias."""

    def __init__(self, client):
        self.client = client

    def get(self, alias):
        return self.client
