from sqlalchemy import select

from app.db import Session
from app.llm import FINISH, Action
from app.models import Outbox, Run, RunStep
from app.tools import Tool


async def make_run(task: str = "summarize https://example.com", key: str = "k") -> Run:
    async with Session() as session, session.begin():
        run = Run(task=task, idempotency_key=key, status="queued")
        session.add(run)
    return run


async def get_run(run_id) -> Run:
    async with Session() as session:
        return await session.get(Run, run_id)


async def get_steps(run_id) -> list[RunStep]:
    async with Session() as session:
        return list(await session.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_no)))


async def get_outbox(run_id) -> list[Outbox]:
    async with Session() as session:
        return list(await session.scalars(select(Outbox).where(Outbox.run_id == run_id)))


class ScriptedLLM:
    """Returns the given tool names in order, then finish."""

    def __init__(self, tool_names: list[str]):
        self.tool_names = tool_names

    async def next_action(self, task, steps) -> Action:
        if len(steps) < len(self.tool_names):
            return Action(self.tool_names[len(steps)], {"n": len(steps) + 1})
        return Action(FINISH, {})

    async def complete(self, prompt: str) -> str:
        return "summary"


class CountingTool:
    """A tool that records every call and fails the first `fail_times` calls."""

    def __init__(self, fail_times: int = 0):
        self.calls = 0
        self.fail_times = fail_times

    async def run(self, input: dict) -> dict:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom {self.calls}")
        return {"ok": True, "call": self.calls}

    def tool(self) -> Tool:
        return Tool(run=self.run)
