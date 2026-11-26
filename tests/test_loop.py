from pathlib import Path

from pydantic import BaseModel, Field

from mailroom.agent.loop import AgentLoop
from mailroom.llm import fake
from mailroom.llm.fake import ScriptedLLM
from mailroom.tools.base import Tool, ToolContext, ToolError, ToolRegistry


class Args(BaseModel):
    n: int = Field(gt=0)


class Final(BaseModel):
    answer: int


calls = {"lookup": 0}


def lookup(args: Args, ctx: ToolContext):
    calls["lookup"] += 1
    if args.n == 13:
        raise ToolError("unlucky number")
    return {"double": args.n * 2}


def registry():
    reg = ToolRegistry()
    reg.register(Tool(name="lookup", description="double n", args_model=Args, handler=lookup))
    reg.register(Tool(name="send_money", description="side effect", args_model=Args,
                      handler=lambda a, c: (_ for _ in ()).throw(AssertionError("must not run in the loop")), side_effect=True))
    return reg


def ctx():
    return ToolContext(tenant="t", tenant_root=Path("."), var_dir=Path("."))


def run(script, **kw):
    llm = ScriptedLLM(script)
    loop = AgentLoop(llm, registry(), **kw)
    result = loop.run(system="s", user_content="u", tool_names=["lookup", "send_money"], finish_model=Final, ctx=ctx())
    return result, llm


def test_tool_then_submit():
    result, llm = run([fake.tool("lookup", {"n": 4}), fake.tool("submit", {"answer": 8})])
    assert result.status == "completed" and result.final.answer == 8
    assert result.tool_results["lookup"] == {"double": 8}
    assert result.llm_calls == 2
    # the second request carried the tool result back to the model
    last = llm.requests[1]["messages"][-1]
    assert last.role == "tool" and '"double": 8' in last.content


def test_invalid_arguments_are_returned_for_self_correction():
    result, llm = run([fake.tool("lookup", {"n": -1}), fake.tool("lookup", {"n": 2}), fake.tool("submit", {"answer": 4})])
    assert result.status == "completed"
    error_msg = llm.requests[1]["messages"][-1]
    assert error_msg.is_error and "greater than 0" in error_msg.content
    assert result.tool_errors == 1


def test_malformed_json_unknown_tool_and_tool_error():
    result, llm = run([
        fake.tool("lookup", raw="{oops"),
        fake.tool("teleport", {"n": 1}),
        fake.tool("lookup", {"n": 13}),
        fake.tool("submit", {"answer": 0}),
    ])
    assert result.status == "completed"
    contents = [r["messages"][-1].content for r in llm.requests[1:]]
    assert "not valid JSON" in contents[0]
    assert "Unknown tool 'teleport'" in contents[1]
    assert "refused: unlucky number" in contents[2]
    assert result.tool_errors == 3


def test_side_effects_are_intercepted_not_executed():
    result, _ = run([fake.tool("send_money", {"n": 5}), fake.tool("submit", {"answer": 1})])
    assert result.status == "completed"
    assert [(c.tool, c.arguments) for c in result.intercepted] == [("send_money", {"n": 5})]
    assert result.tool_events[0].intercepted


def test_plain_text_gets_nudged_then_gives_up():
    result, llm = run([fake.text("the answer is 4"), fake.tool("submit", {"answer": 4})])
    assert result.status == "completed"
    assert "call the `submit` tool" in llm.requests[1]["messages"][-1].content

    result, _ = run([fake.text("a"), fake.text("b"), fake.text("c")], max_nudges=2)
    assert result.status == "no_submit"


def test_invalid_submission_is_retried():
    result, llm = run([fake.tool("submit", {"answer": "many"}), fake.tool("submit", {"answer": 3})])
    assert result.status == "completed" and result.final.answer == 3
    assert "Invalid submission" in llm.requests[1]["messages"][-1].content


def test_max_steps_and_budget():
    result, _ = run([fake.tool("lookup", {"n": i}) for i in range(1, 10)], max_steps=3)
    assert result.status == "max_steps" and result.llm_calls == 3

    result, _ = run([fake.tool("lookup", {"n": i}) for i in range(1, 10)], max_cost_usd=0.00025)  # 0.0001 per call
    assert result.status == "budget_exceeded" and result.llm_calls == 3


def test_repeated_identical_call_uses_cache():
    calls["lookup"] = 0
    result, llm = run([fake.tool("lookup", {"n": 7}), fake.tool("lookup", {"n": 7}), fake.tool("submit", {"answer": 14})])
    assert calls["lookup"] == 1
    assert "identical call" in llm.requests[2]["messages"][-1].content
    assert result.tool_events[1].cached
