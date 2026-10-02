import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, update

from app import config
from app.agent import claim, execute_run
from app.db import Session
from app.models import Run
from app.reaper import reap
from app.tools import Tool, write_outbox

from tests.helpers import CountingTool, ScriptedLLM, get_outbox, get_run, get_steps, make_run


class SimulatedCrash(BaseException):
    """Stands in for the worker process dying: not an Exception, so no retry or cleanup handles it."""


async def crash(input: dict) -> dict:
    raise SimulatedCrash()


async def make_stale(run_id, column=Run.heartbeat_at) -> None:
    async with Session() as session, session.begin():
        await session.execute(
            update(Run).where(Run.id == run_id).values({column: func.now() - timedelta(seconds=120)})
        )


async def reap_into_list() -> list:
    enqueued = []

    async def enqueue(run_id):
        enqueued.append(run_id)

    await reap(enqueue)
    return enqueued


async def test_resume_after_crash_does_not_rerun_completed_steps():
    one, two, three = CountingTool(), CountingTool(), CountingTool()
    llm = ScriptedLLM(["one", "two", "three"])
    run = await make_run()

    with pytest.raises(SimulatedCrash):
        await execute_run(run.id, llm, {"one": one.tool(), "two": two.tool(), "three": Tool(run=crash)})

    assert (await get_run(run.id)).status == "running"
    assert [(s.step_no, s.status) for s in await get_steps(run.id)] == [
        (1, "completed"),
        (2, "completed"),
        (3, "pending"),
    ]

    tools = {"one": one.tool(), "two": two.tool(), "three": three.tool()}

    # heartbeat is still fresh: the reaper leaves the run alone and nobody can claim it
    assert await reap_into_list() == []
    await execute_run(run.id, llm, tools)
    assert (await get_run(run.id)).status == "running"

    await make_stale(run.id)
    assert await reap_into_list() == [run.id]
    await execute_run(run.id, llm, tools)

    assert (await get_run(run.id)).status == "completed"
    steps = await get_steps(run.id)
    assert [(s.step_no, s.status, s.attempts) for s in steps] == [
        (1, "completed", 1),
        (2, "completed", 1),
        (3, "completed", 2),
    ]
    assert (one.calls, two.calls, three.calls) == (1, 1, 1)


async def test_only_one_worker_can_claim_a_run():
    run = await make_run()
    results = await asyncio.gather(*(claim(run.id, f"worker-{i}") for i in range(8)))
    assert sorted(results) == [False] * 7 + [True]

    await make_stale(run.id)
    results = await asyncio.gather(*(claim(run.id, f"worker-{i}") for i in range(8)))
    assert sorted(results) == [False] * 7 + [True]


async def test_worker_that_lost_its_lease_cannot_write():
    run = await make_run()

    async def slow_tool(input: dict) -> dict:
        # While worker A is inside the tool call, its heartbeat goes stale and B takes over.
        await make_stale(run.id)
        assert await claim(run.id, "worker-b")
        return {"recipient": "a@example.com", "subject": "s", "body": "b"}

    await execute_run(run.id, ScriptedLLM(["email"]), {"email": Tool(run=slow_tool, commit=write_outbox)}, "worker-a")

    run = await get_run(run.id)
    (step,) = await get_steps(run.id)
    assert (run.status, run.lease_owner) == ("running", "worker-b")
    assert (step.status, step.output) == ("pending", None)
    assert await get_outbox(run.id) == []


async def test_reaper_selects_only_stale_runs():
    stale_running = await make_run(key="stale-running")
    fresh_running = await make_run(key="fresh-running")
    lost_job = await make_run(key="queued-job-never-reached-redis")
    await make_run(key="fresh-queued")
    done = await make_run(key="done")

    assert await claim(stale_running.id, "dead-worker")
    assert await claim(fresh_running.id, "live-worker")
    await make_stale(stale_running.id)
    await make_stale(lost_job.id, Run.updated_at)
    async with Session() as session, session.begin():
        await session.execute(
            update(Run).where(Run.id == done.id).values(status="completed", heartbeat_at=func.now() - timedelta(hours=1))
        )

    assert set(await reap_into_list()) == {stale_running.id, lost_job.id}


async def test_heartbeat_advances_during_a_long_tool_call(monkeypatch):
    monkeypatch.setattr(config, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    run = await make_run()
    seen = []

    async def slow(input: dict) -> dict:
        seen.append((await get_run(run.id)).heartbeat_at)
        await asyncio.sleep(0.4)
        seen.append((await get_run(run.id)).heartbeat_at)
        return {}

    await execute_run(run.id, ScriptedLLM(["slow"]), {"slow": Tool(run=slow)})

    assert seen[1] > seen[0]
    assert (await get_run(run.id)).status == "completed"
