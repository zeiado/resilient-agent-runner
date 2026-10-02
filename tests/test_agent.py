import asyncio

from dataclasses import replace

from app import config
from app.agent import execute_run
from app.llm import Action, MockLLM
from app.tools import Tool, build_tools

from tests.helpers import CountingTool, ScriptedLLM, get_outbox, get_run, get_steps, make_run


async def fake_fetch(input: dict) -> dict:
    return {"status_code": 200, "content": f"page at {input['url']}"}


async def test_full_run_with_mock_llm_completes():
    llm = MockLLM()
    tools = build_tools(llm)
    tools["fetch_url"] = Tool(run=fake_fetch)
    # approval gate off here; it has its own tests
    tools["send_email"] = replace(tools["send_email"], needs_approval=False)
    run = await make_run("summarize https://example.com/a")

    await execute_run(run.id, llm, tools)

    run = await get_run(run.id)
    steps = await get_steps(run.id)
    assert run.status == "completed"
    assert run.error is None
    assert [(s.step_no, s.tool, s.status, s.attempts) for s in steps] == [
        (1, "fetch_url", "completed", 1),
        (2, "summarize", "completed", 1),
        (3, "send_email", "completed", 1),
    ]
    assert steps[0].output["content"] == "page at https://example.com/a"
    assert steps[0].output["content"] in steps[1].output["summary"]
    outbox = await get_outbox(run.id)
    assert len(outbox) == 1
    assert outbox[0].body == steps[1].output["summary"]


async def test_looping_llm_stops_at_step_limit():
    class LoopingLLM:
        async def next_action(self, task, steps):
            return Action("loop", {})

    counter = CountingTool()
    run = await make_run()

    await execute_run(run.id, LoopingLLM(), {"loop": counter.tool()})

    run = await get_run(run.id)
    assert run.status == "failed"
    assert "step limit of 10" in run.error
    assert len(await get_steps(run.id)) == 10
    assert counter.calls == 10


async def test_tool_fails_twice_then_succeeds():
    flaky = CountingTool(fail_times=2)
    run = await make_run()

    await execute_run(run.id, ScriptedLLM(["flaky"]), {"flaky": flaky.tool()})

    run = await get_run(run.id)
    (step,) = await get_steps(run.id)
    assert run.status == "completed"
    assert step.status == "completed"
    assert step.attempts == 3
    assert step.error is None
    assert flaky.calls == 3


async def test_tool_fails_permanently():
    broken = CountingTool(fail_times=99)
    after = CountingTool()
    run = await make_run()

    await execute_run(run.id, ScriptedLLM(["broken", "after"]), {"broken": broken.tool(), "after": after.tool()})

    run = await get_run(run.id)
    (step,) = await get_steps(run.id)
    assert run.status == "failed"
    assert "step 1 (broken) failed after 3 attempts" in run.error
    assert "boom 3" in run.error
    assert step.status == "failed"
    assert step.attempts == 3
    assert step.error == "RuntimeError: boom 3"
    assert broken.calls == 3
    assert after.calls == 0


async def test_tool_call_times_out(monkeypatch):
    monkeypatch.setattr(config, "TOOL_TIMEOUT_SECONDS", 0.05)

    async def hang(input: dict) -> dict:
        await asyncio.sleep(5)

    run = await make_run()
    await execute_run(run.id, ScriptedLLM(["hang"]), {"hang": Tool(run=hang)})

    run = await get_run(run.id)
    assert run.status == "failed"
    assert "TimeoutError" in run.error
