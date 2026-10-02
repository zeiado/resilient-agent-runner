from types import SimpleNamespace

import pytest

from app.llm import FINISH, NUDGE, Action, TextReply
from app.llm_claude import ClaudeLLM
from app.models import RunStep


class FakeClient:
    """Stands in for AsyncAnthropic: records the request and returns a canned response."""

    def __init__(self, content, stop_reason):
        self.response = SimpleNamespace(content=content, stop_reason=stop_reason)
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.request = kwargs
        return self.response


async def test_tool_use_block_becomes_action_and_history_is_rebuilt_from_steps():
    client = FakeClient(
        [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="tool_use", name="summarize", input={"text": "page"}),
        ],
        "tool_use",
    )
    done = RunStep(step_no=1, tool="fetch_url", input={"url": "https://example.com"}, output={"content": "page"})

    action = await ClaudeLLM(client, model="m").next_action("summarize https://example.com", [done])

    assert action == Action("summarize", {"text": "page"})
    assert client.request["messages"] == [
        {"role": "user", "content": "summarize https://example.com"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "step_1", "name": "fetch_url", "input": {"url": "https://example.com"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "step_1", "content": '{"content": "page"}'}],
        },
    ]
    assert {t["name"] for t in client.request["tools"]} == {"fetch_url", "summarize", "send_email", "finish"}
    assert "betas" not in client.request
    assert "fallbacks" not in client.request


async def test_finish_tool_call_ends_the_run():
    client = FakeClient([SimpleNamespace(type="tool_use", name="finish", input={"result": "emailed it"})], "tool_use")
    assert await ClaudeLLM(client, model="m").next_action("t", []) == Action(FINISH, {"result": "emailed it"})
    assert "finish" in {t["name"] for t in client.request["tools"]}


async def test_plain_text_reply_is_not_a_finish():
    client = FakeClient([SimpleNamespace(type="text", text="Done.")], "end_turn")
    assert await ClaudeLLM(client, model="m").next_action("t", []) == TextReply("Done.")


async def test_nudge_replays_the_text_reply_and_the_correction():
    client = FakeClient([SimpleNamespace(type="tool_use", name="finish", input={"result": "r"})], "tool_use")
    await ClaudeLLM(client, model="m").next_action("t", [], nudge_reply="Done.")
    assert client.request["messages"] == [
        {"role": "user", "content": "t"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": NUDGE},
    ]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_refusal_and_truncation_raise(stop_reason):
    client = FakeClient([], stop_reason)
    with pytest.raises(RuntimeError, match=stop_reason):
        await ClaudeLLM(client, model="m").next_action("t", [])
