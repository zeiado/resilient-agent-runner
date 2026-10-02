import asyncio
import re
from dataclasses import dataclass
from typing import Protocol

from app import config
from app.models import RunStep

FINISH = "finish"


@dataclass
class Action:
    tool: str
    input: dict


class LLM(Protocol):
    async def next_action(self, task: str, steps: list[RunStep]) -> Action:
        """Pick the next tool given the task and the completed steps, or FINISH."""

    async def complete(self, prompt: str) -> str:
        """Plain text completion, used by the summarize tool."""


class MockLLM:
    """Deterministic script: fetch_url -> summarize -> send_email -> finish."""

    def __init__(self, delay: float = 0):
        self.delay = delay

    async def next_action(self, task: str, steps: list[RunStep]) -> Action:
        await asyncio.sleep(self.delay)
        url = re.search(r"https?://\S+", task)
        url = url.group(0) if url else "https://example.com"

        if len(steps) == 0:
            return Action("fetch_url", {"url": url})
        if len(steps) == 1:
            return Action("summarize", {"text": steps[0].output["content"]})
        if len(steps) == 2:
            return Action(
                "send_email",
                {"recipient": "demo@example.com", "subject": f"Summary of {url}", "body": steps[1].output["summary"]},
            )
        return Action(FINISH, {})

    async def complete(self, prompt: str) -> str:
        text = prompt.split("\n\n", 1)[-1]
        return f"[mock summary, {len(text)} chars] {text[:120]}"


def build_llm() -> LLM:
    if config.LLM_PROVIDER == "mock":
        return MockLLM(delay=config.MOCK_LLM_DELAY_SECONDS)
    raise ValueError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")
