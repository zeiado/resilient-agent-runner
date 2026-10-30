import asyncio

from app.agent import claim, execute_run
from app.tools import Tool, write_outbox

from tests.helpers import CountingTool, ScriptedLLM, get_outbox, get_run, get_steps, make_run
from tests.test_approval import mock_setup


async def test_cancel_during_a_step_stops_the_worker_without_writing(client):
    run = await make_run()
    after = CountingTool()

    async def email(input: dict) -> dict:
        # The run is cancelled while the worker is inside the tool call.
        assert (await client.post(f"/runs/{run.id}/cancel")).status_code == 200
        return {"recipient": "a@example.com", "subject": "s", "body": "b"}

    tools = {"email": Tool(run=email, commit=write_outbox), "after": after.tool()}
    await execute_run(run.id, ScriptedLLM(["email", "after"]), tools)

    run = await get_run(run.id)
    (step,) = await get_steps(run.id)
    assert (run.status, run.lease_owner) == ("cancelled", None)
    assert (step.status, step.output) == ("cancelled", None)
    assert await get_outbox(run.id) == []
    assert after.calls == 0


async def test_cancel_while_awaiting_approval(client):
    llm, tools = mock_setup()
    run = await make_run()
    await execute_run(run.id, llm, tools)

    resp = await client.post(f"/runs/{run.id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["steps"][-1]["status"] == "cancelled"

    assert (await client.post(f"/runs/{run.id}/approve")).status_code == 409
    await execute_run(run.id, llm, tools)
    assert (await get_run(run.id)).status == "cancelled"
    assert await get_outbox(run.id) == []


async def test_cancelled_queued_run_cannot_be_claimed(client):
    run = await make_run()
    assert (await client.post(f"/runs/{run.id}/cancel")).status_code == 200
    assert not await claim(run.id, "worker")


async def test_cancel_after_the_run_ended_is_409(client):
    run = await make_run()
    await execute_run(run.id, ScriptedLLM([]), {})
    assert (await get_run(run.id)).status == "completed"

    assert (await client.post(f"/runs/{run.id}/cancel")).status_code == 409
    assert (await client.post("/runs/00000000-0000-0000-0000-000000000000/cancel")).status_code == 404


async def test_concurrent_approve_and_cancel_never_send(client):
    llm, tools = mock_setup()
    run = await make_run()
    await execute_run(run.id, llm, tools)

    approve, cancel = await asyncio.gather(client.post(f"/runs/{run.id}/approve"), client.post(f"/runs/{run.id}/cancel"))
    # Either cancel wins (approve gets 409) or approve wins first and cancel still stops the queued run.
    assert cancel.status_code == 200
    assert approve.status_code in (200, 409)

    await execute_run(run.id, llm, tools)
    assert (await get_run(run.id)).status == "cancelled"
    assert await get_outbox(run.id) == []
