"""S4-02 requirements for cancelling run_task. The flag is set by the fakes at a chosen moment.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_agent_cancel
"""
import sys
import threading
import unittest
from pathlib import Path
from typing import Any

from bangumi_agent.agent.loop import CANCELLED_TOOL, run_task
from bangumi_agent.agent.messages import Message, ModelReply, TextBlock, ToolCallBlock, ToolResultBlock, ToolSpec

sys.path.insert(0, str(Path(__file__).parent))  # the fakes live in test_agent_loop.py, next to this file
from test_agent_loop import SYSTEM, FakeModel, FakeTools, LoopCase, answer, call, wants  # noqa: E402


class CancellingModel(FakeModel):
    """Sets the flag while the given request (1-based) is "in flight", then still returns its reply."""

    def __init__(self, flag: threading.Event, during_request: int, *replies: ModelReply):
        super().__init__(*replies)
        self.flag = flag
        self.during_request = during_request

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        reply = super().complete(system, messages, tools, allow_tools)
        if len(self.requests) == self.during_request:
            self.flag.set()
        return reply


class CancellingTools(FakeTools):
    """Sets the flag while the given tool is running; that tool still returns its result."""

    def __init__(self, flag: threading.Event, during_tool: str):
        super().__init__()
        self.flag = flag
        self.during_tool = during_tool

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        if name == self.during_tool:
            self.flag.set()
        return super().run(name, arguments)


def two_calls() -> ModelReply:
    return wants(call("c1"), call("c2", "get_subject", {"subject_id": 400602}))


class CancelRunTask(LoopCase):
    def start(self) -> list[Message]:
        return [Message("user", [TextBlock("帮我找一下芙莉莲")])]

    def test_cancelled_before_the_first_request(self):
        flag = threading.Event()
        flag.set()
        model, tools, messages = FakeModel(), FakeTools(), self.start()
        result = run_task(model, tools, SYSTEM, messages, cancel=flag)

        self.assertEqual((result.status, result.text, result.rounds), ("cancelled", "", 0))
        self.assertEqual(model.requests, [])
        self.assertEqual(len(messages), 1)

    def test_cancelled_while_the_model_answers_with_tool_calls(self):
        flag = threading.Event()
        model, tools, messages = CancellingModel(flag, 1, two_calls()), FakeTools(), self.start()
        result = run_task(model, tools, SYSTEM, messages, cancel=flag)

        self.assertEqual((result.status, result.rounds), ("cancelled", 1))
        self.assertEqual(tools.ran, [])  # the reply came back, but nothing it asked for is run
        self.assertEqual([m.role for m in messages], ["user", "assistant", "tool"])
        for block in self.results(messages[2]):
            self.assertEqual((block.is_error, block.content), (True, CANCELLED_TOOL))
        self.assert_paired(messages)

    def test_cancelled_while_the_model_gives_its_final_answer(self):
        # The answer is complete, so it stays in the history; the status still says the user cancelled.
        flag = threading.Event()
        model, messages = CancellingModel(flag, 1, answer("找到了。")), self.start()
        result = run_task(model, FakeTools(), SYSTEM, messages, cancel=flag)

        self.assertEqual((result.status, result.text, result.rounds), ("cancelled", "找到了。", 1))
        self.assertEqual([m.role for m in messages], ["user", "assistant"])

    def test_cancelled_during_the_first_of_two_tools(self):
        flag = threading.Event()
        model, messages = FakeModel(two_calls()), self.start()
        tools = CancellingTools(flag, "search_subjects")
        result = run_task(model, tools, SYSTEM, messages, cancel=flag)

        self.assertEqual((result.status, result.rounds), ("cancelled", 1))
        self.assertEqual([name for name, _ in tools.ran], ["search_subjects"])
        first, second = self.results(messages[2])
        self.assertFalse(first.is_error)  # it had started, so its real result is kept
        self.assertEqual((second.is_error, second.content), (True, CANCELLED_TOOL))
        self.assert_paired(messages)

    def test_cancelled_during_the_last_tool_stops_before_the_next_request(self):
        flag = threading.Event()
        model, messages = FakeModel(two_calls()), self.start()  # a second request would have no reply
        tools = CancellingTools(flag, "get_subject")
        result = run_task(model, tools, SYSTEM, messages, cancel=flag)

        self.assertEqual((result.status, result.rounds), ("cancelled", 1))
        self.assertEqual(len(tools.ran), 2)
        self.assertTrue(all(not block.is_error for block in self.results(messages[2])))
        self.assertEqual(len(model.requests), 1)

    def test_task_can_continue_after_a_cancel(self):
        flag = threading.Event()
        model = CancellingModel(flag, 1, two_calls(), answer("这次只查了条目。"))
        tools, messages = FakeTools(), self.start()
        first = run_task(model, tools, SYSTEM, messages, cancel=flag)
        messages.append(Message("user", [TextBlock("只要条目ID就行")]))
        second = run_task(model, tools, SYSTEM, messages, cancel=threading.Event())

        self.assertEqual((first.status, second.status, second.rounds), ("cancelled", "done", 1))
        sent = model.requests[1]["messages"]
        self.assertEqual([m.role for m in sent], ["user", "assistant", "tool", "user"])
        self.assert_paired(messages)

    def test_unset_flag_changes_nothing(self):
        model, messages = FakeModel(wants(call("c1")), answer("找到了。")), self.start()
        result = run_task(model, FakeTools(), SYSTEM, messages, cancel=threading.Event())

        self.assertEqual((result.status, result.rounds), ("done", 2))


class CancelInsideToolExecution(unittest.TestCase):
    """Ported part (pack B), already implemented: the flag is read before each call."""

    def test_remaining_calls_are_answered_without_running(self):
        from bangumi_agent.agent.tool_execution import execute_tool_calls

        flag = threading.Event()
        tools = CancellingTools(flag, "search_subjects")
        calls = [call("c1"), call("c2", "get_subject"), ToolCallBlock("c3", "search_subjects", {"keyword": "x"})]
        results = execute_tool_calls(calls, tools, flag)

        self.assertEqual([r.call_id for r in results], ["c1", "c2", "c3"])
        self.assertEqual([r.is_error for r in results], [False, True, True])
        self.assertEqual({r.content for r in results[1:]}, {CANCELLED_TOOL})
        self.assertEqual(len(tools.ran), 1)
        self.assertTrue(all(isinstance(r, ToolResultBlock) for r in results))


if __name__ == "__main__":
    unittest.main()
