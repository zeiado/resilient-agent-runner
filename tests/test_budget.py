from sqlalchemy import update

from app import config
from app.agent import execute_run
from app.db import Session
from app.llm import FINISH, Action, TextReply
from app.models import Run
from app.tools import Tool

from tests.helpers import CountingTool, get_run, get_steps, make_run


class TokenLLM:
    """Calls the given tools in order, then finish; every decision reports `tokens` tokens."""

    def __init__(self, tool_names, tokens=100, text_first=False):
        self.tool_names = tool_names
        self.tokens = tokens
        self.text_first = text_first
        self.calls = 0

    async def next_action(self, task, steps, nudge_reply=None):
        self.calls += 1
        if self.text_first and nudge_reply is None:
            return TextReply("Done.", self.tokens)
        if len(steps) < len(self.tool_names):
            return Action(self.tool_names[len(steps)], {"n": len(steps) + 1}, self.tokens)
        return Action(FINISH, {"result": "all done"}, self.tokens)


async def set_budget(run_id, max_tokens, tokens_used=0):
    async with Session() as session, session.begin():
        await session.execute(update(Run).where(Run.id == run_id).values(max_tokens=max_tokens, tokens_used=tokens_used))


async def test_tokens_are_recorded_per_step_and_per_run():
    tools = {name: CountingTool().tool() for name in ("a", "b", "c")}
    run = await make_run()

    await execute_run(run.id, TokenLLM(["a", "b", "c"]), tools)

    run = await get_run(run.id)
    assert run.status == "completed"
    # three tool decisions and the finish decision
    assert run.tokens_used == 400
    assert [s.tokens for s in await get_steps(run.id)] == [100, 100, 100]


async def test_run_fails_when_a_decision_would_exceed_the_budget():
    counters = {name: CountingTool() for name in ("a", "b", "c", "d")}
    run = await make_run()
    await set_budget(run.id, 250)

    await execute_run(run.id, TokenLLM(list(counters)), {n: c.tool() for n, c in counters.items()})

    run = await get_run(run.id)
    assert run.status == "failed"
    assert run.error == "token budget exceeded: 300 of 250 tokens"
    assert run.tokens_used == 300
    # the third decision went over, so its tool was never checkpointed or run
    assert [s.tool for s in await get_steps(run.id)] == ["a", "b"]
    assert [c.calls for c in counters.values()] == [1, 1, 0, 0]


async def test_spent_budget_fails_the_run_without_calling_the_llm():
    llm = TokenLLM(["a"])
    run = await make_run()
    await set_budget(run.id, 100, tokens_used=100)

    await execute_run(run.id, llm, {"a": CountingTool().tool()})

    run = await get_run(run.id)
    assert (run.status, run.error) == ("failed", "token budget exceeded: 100 of 100 tokens")
    assert llm.calls == 0


async def test_nudge_tokens_count_against_the_budget():
    run = await make_run()
    await execute_run(run.id, TokenLLM([], text_first=True), {})

    run = await get_run(run.id)
    assert (run.status, run.tokens_used) == ("completed", 200)


async def test_max_tokens_is_set_through_the_api(client):
    resp = await client.post("/runs", json={"task": "t", "idempotency_key": "budget", "max_tokens": 500})
    assert resp.status_code == 201
    assert (resp.json()["max_tokens"], resp.json()["tokens_used"]) == (500, 0)

    bad = await client.post("/runs", json={"task": "t", "idempotency_key": "budget-0", "max_tokens": 0})
    assert bad.status_code == 422


async def test_same_call_with_the_same_output_stops_the_run():
    class StuckLLM:
        async def next_action(self, task, steps):
            return Action("search", {"q": "flights"})

    calls = 0

    async def search(input: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"hits": []}


    run = await make_run()
    await execute_run(run.id, StuckLLM(), {"search": Tool(run=search)})

    run = await get_run(run.id)
    assert run.status == "failed"
    assert run.error == (
        f"no progress: search already returned the same output for the same input {config.REPEAT_LIMIT - 1} times in a row"
    )
    assert calls == config.REPEAT_LIMIT - 1
    assert len(await get_steps(run.id)) == config.REPEAT_LIMIT - 1


async def test_repeat_check_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "REPEAT_LIMIT", 0)

    class StuckLLM:
        async def next_action(self, task, steps):
            return Action("search", {"q": "flights"})


    async def search(input: dict) -> dict:
        return {"hits": []}

    run = await make_run()
    await execute_run(run.id, StuckLLM(), {"search": Tool(run=search)})

    run = await get_run(run.id)
    assert "step limit of 10" in run.error
