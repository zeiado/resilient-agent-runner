import asyncio

from app.agent import execute_run
from app.llm import MockLLM
from app.tools import Tool, build_tools

from tests.helpers import get_outbox, get_run, get_steps, make_run


async def fake_fetch(input: dict) -> dict:
    return {"status_code": 200, "content": "page"}


def mock_setup():
    llm = MockLLM()
    tools = build_tools(llm)
    tools["fetch_url"] = Tool(run=fake_fetch)
    return llm, tools


async def test_run_pauses_for_approval_then_approve_sends_exactly_one_email(client):
    llm, tools = mock_setup()
    run = await make_run()

    await execute_run(run.id, llm, tools)

    assert (await get_run(run.id)).status == "awaiting_approval"
    steps = await get_steps(run.id)
    assert [(s.tool, s.status, s.attempts) for s in steps] == [
        ("fetch_url", "completed", 1),
        ("summarize", "completed", 1),
        ("send_email", "awaiting_approval", 0),
    ]
    assert await get_outbox(run.id) == []

    # a worker picking the run up while it waits must not do anything
    await execute_run(run.id, llm, tools)
    assert (await get_run(run.id)).status == "awaiting_approval"
    assert await get_outbox(run.id) == []

    resp = await client.post(f"/runs/{run.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    await execute_run(run.id, llm, tools)
    # a duplicate job after completion is a no-op
    await execute_run(run.id, llm, tools)

    assert (await get_run(run.id)).status == "completed"
    assert [s.status for s in await get_steps(run.id)] == ["completed"] * 3
    assert len(await get_outbox(run.id)) == 1


async def test_reject_ends_run_without_sending(client):
    llm, tools = mock_setup()
    run = await make_run()
    await execute_run(run.id, llm, tools)

    resp = await client.post(f"/runs/{run.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["steps"][-1]["status"] == "rejected"

    await execute_run(run.id, llm, tools)
    assert (await get_run(run.id)).status == "rejected"
    assert await get_outbox(run.id) == []

    assert (await client.post(f"/runs/{run.id}/approve")).status_code == 409


async def test_approve_requires_awaiting_approval(client):
    run = await make_run()
    assert (await client.post(f"/runs/{run.id}/approve")).status_code == 409
    assert (await client.post(f"/runs/{run.id}/reject")).status_code == 409
    assert (await client.post("/runs/00000000-0000-0000-0000-000000000000/approve")).status_code == 404


async def test_concurrent_approve_and_reject_only_one_wins(client):
    llm, tools = mock_setup()
    run = await make_run()
    await execute_run(run.id, llm, tools)

    approve, reject = await asyncio.gather(
        client.post(f"/runs/{run.id}/approve"), client.post(f"/runs/{run.id}/reject")
    )
    assert sorted([approve.status_code, reject.status_code]) == [200, 409]
