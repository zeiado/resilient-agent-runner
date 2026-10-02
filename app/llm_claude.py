import json

import anthropic

from app import config
from app.llm import FINISH, Action
from app.models import RunStep

SYSTEM = """You plan the steps of a task for a task runner. On each turn, call exactly one tool \
that makes progress on the user's task. When the task is done, reply with a one-line final \
message and no tool call. send_email is reviewed by a human before it is sent; call it normally."""

TOOLS = [
    {
        "name": "fetch_url",
        "description": "Fetch a URL and return the first 2000 characters of the response body.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "summarize",
        "description": "Summarize a piece of text in three sentences.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_email",
        "description": "Send an email.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["recipient", "subject", "body"],
            "additionalProperties": False,
        },
    },
]


def build_messages(task: str, steps: list[RunStep]) -> list[dict]:
    """Rebuild the conversation from the checkpointed steps, so a resumed run needs no in-memory state."""
    messages = [{"role": "user", "content": task}]
    for step in steps:
        tool_use_id = f"step_{step.step_no}"
        messages.append(
            {"role": "assistant", "content": [{"type": "tool_use", "id": tool_use_id, "name": step.tool, "input": step.input}]}
        )
        messages.append(
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": json.dumps(step.output)}]}
        )
    return messages


class ClaudeLLM:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None, model: str = config.CLAUDE_MODEL):
        # The SDK retries 429 and 5xx itself; anything it raises fails the run with the error saved.
        self.client = client or anthropic.AsyncAnthropic(timeout=120.0)
        self.model = model

    async def _create(self, **kwargs):
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            **kwargs,
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"claude stopped with {response.stop_reason}")
        return response

    async def next_action(self, task: str, steps: list[RunStep]) -> Action:
        response = await self._create(
            system=SYSTEM,
            tools=TOOLS,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=build_messages(task, steps),
        )
        for block in response.content:
            if block.type == "tool_use":
                return Action(block.name, dict(block.input))
        return Action(FINISH, {})

    async def complete(self, prompt: str) -> str:
        response = await self._create(messages=[{"role": "user", "content": prompt}])
        return "".join(block.text for block in response.content if block.type == "text")
