from types import SimpleNamespace

import pytest

from app.llm import FINISH, NUDGE, Action, TextReply
from app.llm_openai import OpenAICompatibleLLM
from app.models import RunStep


class FakeClient:
    """Stands in for AsyncOpenAI: records the request and returns a canned response."""

    def __init__(self, message, finish_reason):
        self.response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.request = kwargs
        return self.response


def tool_call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


async def test_tool_call_becomes_action_and_history_is_rebuilt_from_steps():
    client = FakeClient(
        SimpleNamespace(content="", tool_calls=[tool_call("summarize", '{"text": "page"}')]), "tool_calls"
    )
    done = RunStep(step_no=1, tool="fetch_url", input={"url": "https://example.com"}, output={"content": "page"})

    action = await OpenAICompatibleLLM(client, model="m").next_action("summarize https://example.com", [done])

    assert action == Action("summarize", {"text": "page"})
    assert client.request["model"] == "m"
    assert client.request["messages"][1:] == [
        {"role": "user", "content": "summarize https://example.com"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "step_1",
                    "type": "function",
                    "function": {"name": "fetch_url", "arguments": '{"url": "https://example.com"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "step_1", "content": '{"content": "page"}'},
    ]
    assert client.request["messages"][0]["role"] == "system"
    assert [t["function"]["name"] for t in client.request["tools"]] == ["fetch_url", "summarize", "send_email", "finish"]
    assert client.request["tools"][0]["function"]["parameters"]["required"] == ["url"]


async def test_finish_tool_call_ends_the_run():
    client = FakeClient(SimpleNamespace(content="", tool_calls=[tool_call("finish", '{"result": "emailed it"}')]), "tool_calls")
    assert await OpenAICompatibleLLM(client, model="m").next_action("t", []) == Action(FINISH, {"result": "emailed it"})


async def test_plain_text_reply_is_not_a_finish():
    client = FakeClient(SimpleNamespace(content="Sent the email.", tool_calls=None), "stop")
    assert await OpenAICompatibleLLM(client, model="m").next_action("t", []) == TextReply("Sent the email.")


async def test_nudge_replays_the_text_reply_and_the_correction():
    client = FakeClient(SimpleNamespace(content="", tool_calls=[tool_call("finish", '{"result": "r"}')]), "tool_calls")
    await OpenAICompatibleLLM(client, model="m").next_action("t", [], nudge_reply="Sent the email.")
    assert client.request["messages"][1:] == [
        {"role": "user", "content": "t"},
        {"role": "assistant", "content": "Sent the email."},
        {"role": "user", "content": NUDGE},
    ]


async def test_truncated_reply_raises():
    client = FakeClient(SimpleNamespace(content="...", tool_calls=None), "length")
    with pytest.raises(RuntimeError, match="length"):
        await OpenAICompatibleLLM(client, model="m").next_action("t", [])


async def test_complete_returns_text():
    client = FakeClient(SimpleNamespace(content="a summary", tool_calls=None), "stop")
    assert await OpenAICompatibleLLM(client, model="m").complete("Summarize: x") == "a summary"
    assert "tools" not in client.request
