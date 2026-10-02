import json

from openai import AsyncOpenAI

from app import config
from app.llm import FINISH, Action
from app.llm_claude import SYSTEM, TOOLS
from app.models import RunStep

# Same tools as ClaudeLLM, in the OpenAI function-calling shape.
FUNCTIONS = [
    {
        "type": "function",
        "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]},
    }
    for tool in TOOLS
]


def build_messages(task: str, steps: list[RunStep]) -> list[dict]:
    """Rebuild the conversation from the checkpointed steps, so a resumed run needs no in-memory state."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    for step in steps:
        call_id = f"step_{step.step_no}"
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": call_id, "type": "function", "function": {"name": step.tool, "arguments": json.dumps(step.input)}}
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(step.output)})
    return messages


class OpenAICompatibleLLM:
    """Any OpenAI-compatible chat endpoint; the default configuration points at the Ollama service."""

    def __init__(self, client: AsyncOpenAI | None = None, model: str = config.OPENAI_MODEL):
        self.client = client or AsyncOpenAI(base_url=config.OPENAI_BASE_URL, api_key=config.OPENAI_API_KEY)
        self.model = model

    async def _create(self, **kwargs):
        response = await self.client.chat.completions.create(model=self.model, **kwargs)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model stopped with length")
        return choice.message

    async def next_action(self, task: str, steps: list[RunStep]) -> Action:
        message = await self._create(messages=build_messages(task, steps), tools=FUNCTIONS)
        if message.tool_calls:
            call = message.tool_calls[0].function
            return Action(call.name, json.loads(call.arguments))
        return Action(FINISH, {})

    async def complete(self, prompt: str) -> str:
        message = await self._create(messages=[{"role": "user", "content": prompt}])
        return message.content or ""
