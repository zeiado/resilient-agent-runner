import pytest

from app.agent import execute_run
from app.llm import FINISH, Action, TextReply
from app.reaper import reap
from app.tools import Tool

from tests.helpers import CountingTool, get_run, get_steps, make_run
from tests.test_recovery import SimulatedCrash, crash, make_stale


class TalkativeLLM:
    """Follows a script of tool names, but answers in plain text at the decisions listed in `text_at`.

    text_at maps a number of completed steps to how many plain-text replies to give at that decision.
    """

    def __init__(self, tool_names, text_at, text="I have sent the email."):
        self.tool_names = tool_names
        self.text_left = dict(text_at)
        self.text = text
        self.nudges = []

    async def next_action(self, task, steps, nudge_reply=None):
        self.nudges.append(nudge_reply)
        if self.text_left.get(len(steps), 0) > 0:
            self.text_left[len(steps)] -= 1
            return TextReply(self.text)
        if len(steps) < len(self.tool_names):
            return Action(self.tool_names[len(steps)], {})
        return Action(FINISH, {"result": "did everything"})


async def test_finish_call_completes_the_run_and_stores_the_result():
    one = CountingTool()
    run = await make_run()

    await execute_run(run.id, TalkativeLLM(["one"], {}), {"one": one.tool()})

    run = await get_run(run.id)
    assert (run.status, run.result, run.error) == ("completed", "did everything", None)
    assert [s.tool for s in await get_steps(run.id)] == ["one"]


async def test_text_reply_once_is_corrected_and_the_run_recovers():
    one = CountingTool()
    llm = TalkativeLLM(["one"], {0: 1, 1: 1})
    run = await make_run()

    await execute_run(run.id, llm, {"one": one.tool()})

    run = await get_run(run.id)
    assert (run.status, run.result) == ("completed", "did everything")
    # the two corrections are not steps
    assert [(s.step_no, s.tool, s.status) for s in await get_steps(run.id)] == [(1, "one", "completed")]
    assert one.calls == 1
    assert llm.nudges == [None, "I have sent the email.", None, "I have sent the email."]


async def test_text_reply_twice_fails_the_run_with_the_text_saved():
    one = CountingTool()
    llm = TalkativeLLM(["one"], {1: 2}, text="I have sent the email. " + "x" * 300)
    run = await make_run()

    await execute_run(run.id, llm, {"one": one.tool()})

    run = await get_run(run.id)
    assert run.status == "failed"
    assert run.result is None
    assert run.error == "model replied without a tool call: " + llm.text[:200]
    assert len(await get_steps(run.id)) == 1


async def test_resume_after_crash_with_text_replies_and_finish():
    one, two, three = CountingTool(), CountingTool(), CountingTool()
    # a plain-text reply before the crash (step 2) and another after the resume (finish decision)
    llm = TalkativeLLM(["one", "two", "three"], {1: 1, 3: 1})
    run = await make_run()

    with pytest.raises(SimulatedCrash):
        await execute_run(run.id, llm, {"one": one.tool(), "two": two.tool(), "three": Tool(run=crash)})
    assert (await get_run(run.id)).status == "running"

    await make_stale(run.id)
    await reap(lambda run_id: _noop())
    await execute_run(run.id, llm, {"one": one.tool(), "two": two.tool(), "three": three.tool()})

    run = await get_run(run.id)
    assert (run.status, run.result) == ("completed", "did everything")
    assert [(s.step_no, s.status, s.attempts) for s in await get_steps(run.id)] == [
        (1, "completed", 1),
        (2, "completed", 1),
        (3, "completed", 2),
    ]
    assert (one.calls, two.calls, three.calls) == (1, 1, 1)


async def _noop():
    pass
