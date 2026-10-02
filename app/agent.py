import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select, update

from app import config
from app.db import Session
from app.llm import FINISH, LLM
from app.log import run_id_var
from app.models import Run, RunStep
from app.tools import Tool

log = logging.getLogger("agent")


class LeaseLost(Exception):
    """This worker no longer owns the run; it must stop without writing anything."""


async def claim(run_id: uuid.UUID, worker_id: str) -> bool:
    """Atomically take ownership of a queued run. Only one caller can win."""
    async with Session() as session, session.begin():
        claimed = await session.scalar(
            update(Run)
            .where(Run.id == run_id, Run.status == "queued")
            .values(status="running", lease_owner=worker_id)
            .returning(Run.id)
        )
    return claimed is not None


@asynccontextmanager
async def owned_run(run_id: uuid.UUID, worker_id: str):
    """Transaction that holds a row lock on the run, entered only if we still own the lease."""
    async with Session() as session, session.begin():
        run = await session.scalar(
            select(Run)
            .where(Run.id == run_id, Run.lease_owner == worker_id, Run.status == "running")
            .with_for_update()
        )
        if run is None:
            raise LeaseLost()
        yield session, run


async def finish_run(run_id: uuid.UUID, worker_id: str, status: str, error: str | None = None) -> None:
    async with owned_run(run_id, worker_id) as (_, run):
        run.status = status
        run.error = error
        run.lease_owner = None
    log.info("run %s%s", status, f": {error}" if error else "")


async def execute_run(run_id: uuid.UUID, llm: LLM, tools: dict[str, Tool], worker_id: str | None = None) -> None:
    worker_id = worker_id or uuid.uuid4().hex
    run_id_var.set(str(run_id))
    if not await claim(run_id, worker_id):
        log.info("run is not claimable, skipping")
        return
    log.info("run claimed by worker %s", worker_id)
    try:
        await agent_loop(run_id, worker_id, llm, tools)
    except LeaseLost:
        log.warning("lease lost, stopping without writing")


async def agent_loop(run_id: uuid.UUID, worker_id: str, llm: LLM, tools: dict[str, Tool]) -> None:
    while True:
        async with Session() as session:
            run = await session.get(Run, run_id)
            steps = list(await session.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_no)))

        # A pending last step was checkpointed but not finished: execute it, don't ask the LLM again.
        step = steps[-1] if steps and steps[-1].status == "pending" else None

        if step is None:
            if len(steps) >= config.MAX_STEPS:
                await finish_run(run_id, worker_id, "failed", f"step limit of {config.MAX_STEPS} reached")
                return
            try:
                action = await llm.next_action(run.task, steps)
            except Exception as exc:
                await finish_run(run_id, worker_id, "failed", f"llm error: {type(exc).__name__}: {exc}")
                return
            if action.tool == FINISH:
                await finish_run(run_id, worker_id, "completed")
                return
            if action.tool not in tools:
                await finish_run(run_id, worker_id, "failed", f"llm chose unknown tool: {action.tool}")
                return

            async with owned_run(run_id, worker_id) as (session, _):
                step = RunStep(run_id=run_id, step_no=len(steps) + 1, tool=action.tool, input=action.input, status="pending")
                session.add(step)
            log.info("step %d checkpointed: %s", step.step_no, step.tool)

        if not await execute_step(run_id, worker_id, step, tools[step.tool]):
            return


async def execute_step(run_id: uuid.UUID, worker_id: str, step: RunStep, tool: Tool) -> bool:
    """Run one step to completion. Returns False if the run was marked failed."""
    async with owned_run(run_id, worker_id) as (session, _):
        await session.execute(update(RunStep).where(RunStep.id == step.id).values(attempts=RunStep.attempts + 1))

    try:
        output = await asyncio.wait_for(tool.run(step.input), config.TOOL_TIMEOUT_SECONDS)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        async with owned_run(run_id, worker_id) as (session, run):
            await session.execute(
                update(RunStep)
                .where(RunStep.id == step.id)
                .values(status="failed", error=error, finished_at=datetime.now(timezone.utc))
            )
            run.status = "failed"
            run.error = f"step {step.step_no} ({step.tool}) failed: {error}"
            run.lease_owner = None
        log.error("step %d failed: %s", step.step_no, error)
        return False

    # The side effect and the "completed" mark commit together or not at all.
    async with owned_run(run_id, worker_id) as (session, _):
        if tool.commit:
            await tool.commit(session, step, output)
        await session.execute(
            update(RunStep)
            .where(RunStep.id == step.id)
            .values(status="completed", output=output, finished_at=datetime.now(timezone.utc))
        )
    log.info("step %d completed: %s", step.step_no, step.tool)
    return True
