import logging
import uuid

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.db import Session
from app.log import run_id_var, setup_logging
from app.models import Run, RunStep
from app.queue import enqueue_run, get_pool
from app.schemas import RunCreate, RunOut

setup_logging()
log = logging.getLogger("api")

app = FastAPI(title="resilient-agent-runner")


@app.get("/health")
async def health():
    checks = {}
    try:
        async with Session() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    try:
        await (await get_pool()).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        log.error("health check failed: %s", checks)
    return JSONResponse({"status": "ok" if healthy else "unhealthy", **checks}, status_code=200 if healthy else 503)


async def load_run(session, run_id: uuid.UUID) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    steps = await session.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_no))
    out = RunOut.model_validate(run)
    out.steps = list(steps)
    return out


async def enqueue_or_leave_for_reaper(run_id: uuid.UUID) -> None:
    try:
        await enqueue_run(run_id)
    except Exception:
        # The run is already committed as queued; the reaper re-enqueues it.
        log.exception("enqueue failed, leaving run for the reaper")


@app.post("/runs", response_model=RunOut, status_code=201)
async def create_run(body: RunCreate, response: Response):
    async with Session() as session:
        new_id = await session.scalar(
            insert(Run)
            .values(id=uuid.uuid4(), task=body.task, idempotency_key=body.idempotency_key, status="queued")
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Run.id)
        )
        await session.commit()

        if new_id is None:
            existing_id = await session.scalar(select(Run.id).where(Run.idempotency_key == body.idempotency_key))
            run_id_var.set(str(existing_id))
            log.info("idempotency key already used, returning existing run")
            response.status_code = 200
            return await load_run(session, existing_id)

        run_id_var.set(str(new_id))
        await enqueue_or_leave_for_reaper(new_id)
        log.info("run created")
        return await load_run(session, new_id)


@app.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID):
    run_id_var.set(str(run_id))
    async with Session() as session:
        return await load_run(session, run_id)


async def decide(run_id: uuid.UUID, run_status: str, step_status: str) -> None:
    """Move a run out of awaiting_approval. The conditional UPDATE lets exactly one decision win."""
    async with Session() as session, session.begin():
        if await session.get(Run, run_id) is None:
            raise HTTPException(404, "run not found")
        decided = await session.scalar(
            update(Run)
            .where(Run.id == run_id, Run.status == "awaiting_approval")
            .values(status=run_status)
            .returning(Run.id)
        )
        if decided is None:
            raise HTTPException(409, "run is not awaiting approval")
        await session.execute(
            update(RunStep)
            .where(RunStep.run_id == run_id, RunStep.status == "awaiting_approval")
            .values(status=step_status)
        )


@app.post("/runs/{run_id}/approve", response_model=RunOut)
async def approve_run(run_id: uuid.UUID):
    run_id_var.set(str(run_id))
    await decide(run_id, run_status="queued", step_status="pending")
    await enqueue_or_leave_for_reaper(run_id)
    log.info("run approved")
    async with Session() as session:
        return await load_run(session, run_id)


@app.post("/runs/{run_id}/reject", response_model=RunOut)
async def reject_run(run_id: uuid.UUID):
    run_id_var.set(str(run_id))
    await decide(run_id, run_status="rejected", step_status="rejected")
    log.info("run rejected")
    async with Session() as session:
        return await load_run(session, run_id)
