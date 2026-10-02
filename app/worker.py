import uuid

from arq import cron, func

from app import agent, config
from app.llm import build_llm
from app.log import setup_logging
from app.queue import redis_settings
from app.reaper import reap
from app.tools import build_tools


async def execute_run(ctx, run_id: str) -> None:
    await agent.execute_run(uuid.UUID(run_id), ctx["llm"], ctx["tools"])


async def reap_stale_runs(ctx) -> None:
    await reap()


async def startup(ctx) -> None:
    setup_logging()
    ctx["llm"] = build_llm()
    ctx["tools"] = build_tools(ctx["llm"])


class WorkerSettings:
    functions = [func(execute_run, name="execute_run")]
    redis_settings = redis_settings
    cron_jobs = [
        cron(
            reap_stale_runs,
            second=set(range(0, 60, config.REAPER_INTERVAL_SECONDS)),
            run_at_startup=True,
        )
    ]
    on_startup = startup
    # Recovery is driven by the reaper and the lease in Postgres, not by ARQ retries.
    max_tries = 1
    job_timeout = 600
