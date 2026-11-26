import json

import httpx
import pytest

from mailroom.llm.anthropic import AnthropicClient, to_wire_messages
from mailroom.llm.http import post_json_with_retry
from mailroom.llm.openai_compat import OpenAICompatClient
from mailroom.llm.pricing import Pricing
from mailroom.llm.types import LLMError, Message, ToolCall, ToolSpec

TOOL = ToolSpec(name="lookup", description="d", parameters={"type": "object", "properties": {"q": {"type": "string"}}})


def history():
    return [
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": "x"}),
                                                         ToolCall(id="c2", name="lookup", arguments={"q": "y"})]),
        Message(role="tool", tool_call_id="c1", tool_name="lookup", content="r1"),
        Message(role="tool", tool_call_id="c2", tool_name="lookup", content="r2", is_error=True),
    ]


def test_openai_round_trip_with_tools_and_deepseek_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "c9", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "z"}'}},
                {"id": "c10", "type": "function", "function": {"name": "lookup", "arguments": "{not json"}},
            ]}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 400},
        })

    client = OpenAICompatClient(alias="t", model="deepseek-flash", base_url="https://x.example", api_key="k",
                                pricing=Pricing(input=0.30, cached_input=0.03, output=1.20),
                                extra_body={"thinking": {"type": "disabled"}},
                                http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = client.chat("sys", history(), tools=[TOOL], tool_choice={"name": "lookup"})

    body = seen["body"]
    assert seen["auth"] == "Bearer k"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"q": "x"}'  # arguments travel as a string
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "r1"}
    assert body["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    assert body["thinking"] == {"type": "disabled"}

    assert r.stop_reason == "tool_use"
    assert r.message.tool_calls[0].arguments == {"q": "z"}
    assert r.message.tool_calls[1].parse_error  # malformed arguments are surfaced, not raised
    assert r.usage.cached_input_tokens == 400
    assert r.cost_usd == pytest.approx((600 * 0.30 + 400 * 0.03 + 100 * 1.20) / 1e6)


def test_anthropic_merges_tool_results_into_one_user_turn():
    wire = to_wire_messages(history())
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert [b["type"] for b in wire[1]["content"]] == ["tool_use", "tool_use"]
    results = wire[2]["content"]
    assert [b["tool_use_id"] for b in results] == ["c1", "c2"]
    assert results[1]["is_error"] is True


def test_anthropic_round_trip():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(200, json={
            "model": "claude-haiku-5-5", "stop_reason": "tool_use",
            "content": [{"type": "text", "text": "checking"}, {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "a"}}],
            "usage": {"input_tokens": 50, "output_tokens": 20, "cache_read_input_tokens": 100},
        })

    client = AnthropicClient(alias="h", model="claude-haiku-5-5", api_key="k",
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = client.chat("sys", [Message(role="user", content="hi")], tools=[TOOL], tool_choice="required")
    assert seen["body"]["system"] == "sys"
    assert seen["body"]["tools"][0]["input_schema"] == TOOL.parameters
    assert seen["body"]["tool_choice"] == {"type": "any"}
    assert seen["headers"]["x-api-key"] == "k" and seen["headers"]["anthropic-version"]
    assert r.message.content == "checking" and r.message.tool_calls[0].arguments == {"q": "a"}
    assert r.usage.input_tokens == 150 and r.usage.cached_input_tokens == 100
    assert r.cost_usd is None  # no price configured


def test_retry_on_429_then_success_and_no_retry_on_400():
    calls = {"n": 0}
    slept = []

    def flaky(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0.5"}, json={"error": "rate"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    assert post_json_with_retry(client, "https://x.example", headers={}, payload={}, sleep=slept.append) == {"ok": True}
    assert calls["n"] == 3 and slept == [0.5, 0.5]

    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"})))
    with pytest.raises(LLMError) as exc:
        post_json_with_retry(bad, "https://x.example", headers={}, payload={}, sleep=slept.append)
    assert exc.value.status == 400
