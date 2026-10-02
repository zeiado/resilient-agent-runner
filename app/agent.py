import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update

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
    """Atomically take the lease on a run that is queued, or running with a stale heartbeat.

    Concurrent callers serialize on the row lock; the loser re-checks the WHERE clause
    against the winner's fresh heartbeat and gets no row.
    """
    stale = func.now() - timedelta(seconds=config.HEARTBEAT_STALE_SECONDS)
    async with Session() as session, session.begin():
        claimed = await session.scalar(
            update(Run)
            .where(
                Run.id == run_id,
                or_(Run.status == "queued", and_(Run.status == "running", Run.heartbeat_at < stale)),
            )
            .values(status="running", lease_owner=worker_id, heartbeat_at=func.now())
            .returning(Run.id)
        )
    return claimed is not None


async def heartbeat_loop(run_id: uuid.UUID, worker_id: str) -> None:
    while True:
        await asyncio.sleep(config.HEARTBEAT_INTERVAL_SECONDS)
        try:
            async with Session() as session, session.begin():
                await session.execute(
                    update(Run)
                    .where(Run.id == run_id, Run.lease_owner == worker_id, Run.status == "running")
                    .values(heartbeat_at=func.now())
                )
        except Exception:
            log.exception("heartbeat failed")


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
    heartbeat = asyncio.create_task(heartbeat_loop(run_id, worker_id))
    try:
        await agent_loop(run_id, worker_id, llm, tools)
    except LeaseLost:
        log.warning("lease lost, stopping without writing")
    finally:
        heartbeat.cancel()


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

            needs_approval = tools[action.tool].needs_approval
            async with owned_run(run_id, worker_id) as (session, owned):
                step = RunStep(
                    run_id=run_id,
                    step_no=len(steps) + 1,
                    tool=action.tool,
                    input=action.input,
                    status="awaiting_approval" if needs_approval else "pending",
                )
                session.add(step)
                if needs_approval:
                    owned.status = "awaiting_approval"
                    owned.lease_owner = None
            log.info("step %d checkpointed: %s", step.step_no, step.tool)
            if needs_approval:
                log.info("run paused, waiting for human approval of step %d", step.step_no)
                return

        if not await execute_step(run_id, worker_id, step, tools[step.tool]):
            return


async def execute_step(run_id: uuid.UUID, worker_id: str, step: RunStep, tool: Tool) -> bool:
    """Run one step to completion, retrying failures. Returns False if the run was marked failed."""
    attempts = step.attempts
    error = "worker crashed during every attempt"

    while attempts < config.MAX_ATTEMPTS:
        # Count the attempt before making it, so a crash mid-attempt still uses one up.
        async with owned_run(run_id, worker_id) as (session, _):
            attempts = await session.scalar(
                update(RunStep).where(RunStep.id == step.id).values(attempts=RunStep.attempts + 1).returning(RunStep.attempts)
            )

        try:
            output = await asyncio.wait_for(tool.run(step.input), config.TOOL_TIMEOUT_SECONDS)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning("step %d attempt %d/%d failed: %s", step.step_no, attempts, config.MAX_ATTEMPTS, error)
            if attempts < config.MAX_ATTEMPTS:
                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * 2 ** (attempts - 1))
            continue

        # The side effect and the "completed" mark commit together or not at all.
        async with owned_run(run_id, worker_id) as (session, _):
            if tool.commit:
                await tool.commit(session, step, output)
            await session.execute(
                update(RunStep)
                .where(RunStep.id == step.id)
                .values(status="completed", output=output, error=None, finished_at=datetime.now(timezone.utc))
            )
        log.info("step %d completed: %s (attempt %d)", step.step_no, step.tool, attempts)
        return True

    async with owned_run(run_id, worker_id) as (session, run):
        await session.execute(
            update(RunStep)
            .where(RunStep.id == step.id)
            .values(status="failed", error=error, finished_at=datetime.now(timezone.utc))
        )
        run.status = "failed"
        run.error = f"step {step.step_no} ({step.tool}) failed after {attempts} attempts: {error}"
        run.lease_owner = None
    log.error("run failed: %s", run.error)
    return False
