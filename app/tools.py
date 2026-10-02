from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.llm import LLM
from app.models import Outbox, RunStep


@dataclass
class Tool:
    # Does the work and returns the step output. May be called more than once for the
    # same step (retry, or resume after a crash), so it must not have side effects.
    run: Callable[[dict], Awaitable[dict]]
    # Side effect, executed inside the transaction that marks the step completed.
    commit: Callable[[AsyncSession, RunStep, dict], Awaitable[None]] | None = None
    needs_approval: bool = False


async def fetch_url(input: dict) -> dict:
    async with httpx.AsyncClient(timeout=config.TOOL_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(input["url"])
        resp.raise_for_status()
        return {"status_code": resp.status_code, "content": resp.text[:2000]}


async def compose_email(input: dict) -> dict:
    return {"recipient": input["recipient"], "subject": input["subject"], "body": input["body"]}


async def write_outbox(session: AsyncSession, step: RunStep, output: dict) -> None:
    # (run_id, step_no) is unique, so a repeated send for the same step is a no-op.
    await session.execute(
        insert(Outbox)
        .values(run_id=step.run_id, step_no=step.step_no, **output)
        .on_conflict_do_nothing(index_elements=["run_id", "step_no"])
    )


def build_tools(llm: LLM) -> dict[str, Tool]:
    async def summarize(input: dict) -> dict:
        return {"summary": await llm.complete(f"Summarize this in three sentences:\n\n{input['text']}")}

    return {
        "fetch_url": Tool(run=fetch_url),
        "summarize": Tool(run=summarize),
        "send_email": Tool(run=compose_email, commit=write_outbox, needs_approval=True),
    }
