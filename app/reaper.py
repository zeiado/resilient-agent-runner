import logging
import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from app import config
from app.db import Session
from app.log import run_id_var
from app.models import Run
from app.queue import enqueue_run

log = logging.getLogger("reaper")


async def reap(enqueue=enqueue_run) -> list[uuid.UUID]:
    """Re-enqueue runs whose worker died (stale heartbeat) or whose job never reached Redis.

    The reaper only enqueues. Ownership is decided by agent.claim, so enqueueing a run
    twice, or racing with another reaper, cannot make it execute twice.
    """
    stale = func.now() - timedelta(seconds=config.HEARTBEAT_STALE_SECONDS)
    async with Session() as session, session.begin():
        run_ids = list(
            await session.scalars(
                select(Run.id)
                .where(
                    or_(
                        and_(Run.status == "running", Run.heartbeat_at < stale),
                        and_(Run.status == "queued", Run.updated_at < stale),
                    )
                )
                .with_for_update(skip_locked=True)
            )
        )
        for run_id in run_ids:
            run_id_var.set(str(run_id))
            await enqueue(run_id)
            log.warning("stale run re-enqueued")
    run_id_var.set(None)
    return run_ids
