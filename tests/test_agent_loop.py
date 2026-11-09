"""S4-01 requirements for run_task. The model and the tools are fakes; nothing goes to the network.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_agent_loop
"""
import io
import unittest
from contextlib import redirect_stdout
from typing import Any

from bangumi_agent.agent.loop import (HIDDEN_ERROR, INCOMPLETE_ARGUMENTS, SUMMARY_REQUEST, UNKNOWN_TOOL,
                                      LoopResult, run_task)
from bangumi_agent.agent.messages import (Message, ModelReply, StopReason, TextBlock, ToolCallBlock,
                                          ToolError, ToolResultBlock, ToolSpec, text_of, tool_calls_of)

TOKEN = "fake-bangumi-token-0123456789"
SYSTEM = "你是Bangumi助手。"


def call(call_id: str, name: str = "search_subjects", arguments: dict[str, Any] | None = None) -> ToolCallBlock:
    return ToolCallBlock(call_id, name, {"keyword": "葬送的芙莉莲"} if arguments is None else arguments)


def broken_call(call_id: str, name: str = "search_subjects") -> ToolCallBlock:
    """A call whose arguments the converter could not parse."""
    return ToolCallBlock(call_id, name, None)


def reply(*blocks: TextBlock | ToolCallBlock, stop: StopReason) -> ModelReply:
    return ModelReply(Message("assistant", list(blocks)), stop)


def answer(text: str) -> ModelReply:
    return reply(TextBlock(text), stop="end")


def wants(*calls: ToolCallBlock) -> ModelReply:
    return reply(TextBlock("我查一下。"), *calls, stop="tool_calls")


class FakeModel:
    """Hands out the scripted replies in order and remembers every request."""

    def __init__(self, *replies: ModelReply):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        self.requests.append({"system": system, "messages": list(messages),
                              "tool_names": [spec.name for spec in tools], "allow_tools": allow_tools})
        if not self.replies:
            raise AssertionError("循环发出了多余的模型请求")
        return self.replies.pop(0)


class FakeTools:
    """search_subjects echoes its keyword; get_subject fails the way it is told to."""

    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.ran: list[tuple[str, dict[str, Any]]] = []

    def specs(self) -> list[ToolSpec]:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        return [ToolSpec("search_subjects", "搜索条目", schema), ToolSpec("get_subject", "条目详情", schema)]

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        self.ran.append((name, arguments))
        if name == "get_subject" and self.failure is not None:
            raise self.failure
        return f"{name} 结果：{arguments}"


class LoopCase(unittest.TestCase):
    def run_loop(self, model: FakeModel, tools: FakeTools | None = None, max_rounds: int | None = None
                 ) -> tuple[LoopResult, list[Message]]:
        messages = [Message("user", [TextBlock("帮我找一下芙莉莲")])]
        tools = tools if tools is not None else FakeTools()
        if max_rounds is None:
            result = run_task(model, tools, SYSTEM, messages)
        else:
            result = run_task(model, tools, SYSTEM, messages, max_rounds=max_rounds)
        self.assert_paired(messages)
        return result, messages

    def assert_paired(self, messages: list[Message]) -> None:
        """Every assistant message with tool calls is followed by one tool message answering them in order."""
        for index, message in enumerate(messages):
            calls = tool_calls_of(message)
            if message.role != "assistant" or not calls:
                continue
            self.assertLess(index + 1, len(messages), "工具调用后面没有结果")
            following = messages[index + 1]
            self.assertEqual(following.role, "tool")
            self.assertTrue(all(isinstance(block, ToolResultBlock) for block in following.content))
            self.assertEqual([block.call_id for block in following.content if isinstance(block, ToolResultBlock)],
                             [c.id for c in calls])

    def results(self, message: Message) -> list[ToolResultBlock]:
        self.assertEqual(message.role, "tool")
        return [block for block in message.content if isinstance(block, ToolResultBlock)]


class AnswersAndTools(LoopCase):
    def test_plain_answer_needs_one_request(self):
        model = FakeModel(answer("芙莉莲是2023年的动画。"))
        result, messages = self.run_loop(model)

        self.assertEqual((result.status, result.text, result.rounds), ("done", "芙莉莲是2023年的动画。", 1))
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        first = model.requests[0]
        self.assertEqual(first["system"], SYSTEM)
        self.assertTrue(first["allow_tools"])
        self.assertEqual(first["tool_names"], ["search_subjects", "get_subject"])

    def test_tool_result_goes_back_to_the_model(self):
        model = FakeModel(wants(call("c1")), answer("找到了。"))
        tools = FakeTools()
        result, messages = self.run_loop(model, tools)

        self.assertEqual((result.status, result.text, result.rounds), ("done", "找到了。", 2))
        self.assertEqual(tools.ran, [("search_subjects", {"keyword": "葬送的芙莉莲"})])
        self.assertEqual([m.role for m in messages], ["user", "assistant", "tool", "assistant"])
        [result_block] = self.results(messages[2])
        self.assertEqual(result_block.call_id, "c1")
        self.assertFalse(result_block.is_error)
        self.assertIn("葬送的芙莉莲", result_block.content)
        # The second request already contains the call and its result.
        self.assertEqual([m.role for m in model.requests[1]["messages"]], ["user", "assistant", "tool"])

    def test_several_calls_share_one_result_message_in_order(self):
        model = FakeModel(wants(call("c1"), call("c2", "get_subject", {"subject_id": 400602})), answer("好了。"))
        tools = FakeTools()
        _, messages = self.run_loop(model, tools)

        self.assertEqual([name for name, _ in tools.ran], ["search_subjects", "get_subject"])
        self.assertEqual([m.role for m in messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual([block.call_id for block in self.results(messages[2])], ["c1", "c2"])

    def test_one_failing_call_does_not_stop_the_others(self):
        model = FakeModel(wants(call("c1", "get_subject"), call("c2")), answer("好了。"))
        tools = FakeTools(failure=ToolError("条目不存在"))
        _, messages = self.run_loop(model, tools)

        first, second = self.results(messages[2])
        self.assertTrue(first.is_error)
        self.assertFalse(second.is_error)
        self.assertEqual(len(tools.ran), 2)

    def test_unknown_stop_reason_ends_the_task(self):
        model = FakeModel(reply(TextBlock("……"), stop="other"))
        result, _ = self.run_loop(model)

        self.assertEqual((result.status, result.rounds), ("unexpected_stop", 1))

    def test_loop_prints_nothing(self):
        model = FakeModel(wants(call("c1"), call("c2", "get_subject")), answer("好了。"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.run_loop(model, FakeTools(failure=RuntimeError("boom")))

        self.assertEqual(out.getvalue(), "")


class RejectedCalls(LoopCase):
    def test_tools_outside_the_list_never_run(self):
        # execute_episode_plan exists in MCP but the model must never get it.
        for name in ["execute_episode_plan", "delete_everything"]:
            with self.subTest(name=name):
                model = FakeModel(wants(call("c1", name)), answer("好吧。"))
                tools = FakeTools()
                _, messages = self.run_loop(model, tools)

                self.assertEqual(tools.ran, [])
                [block] = self.results(messages[2])
                self.assertTrue(block.is_error)
                self.assertEqual(block.content, UNKNOWN_TOOL.format(name=name))

    def test_unparsable_arguments_are_not_run(self):
        model = FakeModel(wants(broken_call("c1")), answer("好吧。"))
        tools = FakeTools()
        result, messages = self.run_loop(model, tools)

        self.assertEqual(result.status, "done")
        self.assertEqual(tools.ran, [])
        [block] = self.results(messages[2])
        self.assertTrue(block.is_error)
        self.assertEqual(block.content, INCOMPLETE_ARGUMENTS)


class ToolErrors(LoopCase):
    def test_expected_error_text_reaches_the_model(self):
        model = FakeModel(wants(call("c1", "get_subject")), answer("没找到。"))
        _, messages = self.run_loop(model, FakeTools(failure=ToolError("条目不存在")))

        [block] = self.results(messages[2])
        self.assertTrue(block.is_error)
        self.assertEqual(block.content, "条目不存在")

    def test_unexpected_error_is_hidden(self):
        failures = [RuntimeError(f"401 for Authorization: Bearer {TOKEN}"),
                    ValueError(f"bad token {TOKEN}"),
                    KeyError(TOKEN)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                model = FakeModel(wants(call("c1", "get_subject")), answer("出错了。"))
                _, messages = self.run_loop(model, FakeTools(failure=failure))

                [block] = self.results(messages[2])
                self.assertTrue(block.is_error)
                self.assertEqual(block.content, HIDDEN_ERROR)
                sent = repr(model.requests)
                self.assertNotIn(TOKEN, sent)
                self.assertNotIn("Traceback", sent)


class Truncation(LoopCase):
    def test_cut_off_text_is_kept(self):
        model = FakeModel(reply(TextBlock("芙莉莲的主要角色有：芙莉莲、费伦、修"), stop="max_tokens"))
        result, messages = self.run_loop(model)

        self.assertEqual((result.status, result.rounds), ("text_cut", 1))
        self.assertEqual(result.text, "芙莉莲的主要角色有：芙莉莲、费伦、修")
        self.assertEqual([m.role for m in messages], ["user", "assistant"])

    def test_cut_off_tool_call_is_answered_but_not_run(self):
        cut = reply(TextBlock("我查一下。"), call("c1"), stop="max_tokens")
        model = FakeModel(cut, wants(call("c2")), answer("找到了。"))
        tools = FakeTools()
        result, messages = self.run_loop(model, tools)

        self.assertEqual((result.status, result.rounds), ("done", 3))
        self.assertEqual(tools.ran, [("search_subjects", {"keyword": "葬送的芙莉莲"})])  # only c2
        [block] = self.results(messages[2])
        self.assertEqual((block.call_id, block.is_error, block.content), ("c1", True, INCOMPLETE_ARGUMENTS))

    def test_two_cuts_in_a_row_stop_the_task(self):
        model = FakeModel(reply(call("c1"), stop="max_tokens"), reply(call("c2"), stop="max_tokens"))
        tools = FakeTools()
        result, messages = self.run_loop(model, tools)

        self.assertEqual((result.status, result.rounds), ("repeated_cut", 2))
        self.assertEqual(tools.ran, [])
        self.assertEqual(messages[-1].role, "tool")  # the second cut call is still answered

    def test_a_normal_round_resets_the_cut_count(self):
        model = FakeModel(reply(call("c1"), stop="max_tokens"), wants(call("c2")),
                          reply(call("c3"), stop="max_tokens"), answer("找到了。"))
        result, _ = self.run_loop(model)

        self.assertEqual((result.status, result.rounds), ("done", 4))


class RoundLimit(LoopCase):
    def test_last_round_has_no_tools_and_asks_for_a_summary(self):
        model = FakeModel(wants(call("c1")), wants(call("c2")), answer("已查到条目，还没看章节。"))
        tools = FakeTools()
        result, messages = self.run_loop(model, tools, max_rounds=3)

        self.assertEqual((result.status, result.rounds), ("round_limit", 3))
        self.assertEqual(result.text, "已查到条目，还没看章节。")
        self.assertEqual([r["allow_tools"] for r in model.requests], [True, True, False])
        last_sent = model.requests[2]["messages"][-1]
        self.assertEqual((last_sent.role, text_of(last_sent)), ("user", SUMMARY_REQUEST))
        self.assertEqual(len(tools.ran), 2)
        self.assertEqual(messages[-1].role, "assistant")

    def test_default_limit_is_twenty(self):
        model = FakeModel(*[wants(call(f"c{n}")) for n in range(19)], answer("总结。"))
        result, _ = self.run_loop(model)

        self.assertEqual((result.status, result.rounds), ("round_limit", 20))
        self.assertFalse(model.requests[-1]["allow_tools"])
        self.assertTrue(all(r["allow_tools"] for r in model.requests[:-1]))

    def test_finishing_early_is_not_a_limit(self):
        model = FakeModel(wants(call("c1")), answer("找到了。"))
        result, _ = self.run_loop(model, max_rounds=3)

        self.assertEqual((result.status, result.rounds), ("done", 2))

    def test_continuing_starts_a_fresh_count(self):
        messages = [Message("user", [TextBlock("帮我找一下芙莉莲")])]
        model = FakeModel(wants(call("c1")), answer("总结。"), wants(call("c2")), answer("全部完成。"))
        tools = FakeTools()
        first = run_task(model, tools, SYSTEM, messages, max_rounds=2)
        messages.append(Message("user", [TextBlock("继续")]))
        second = run_task(model, tools, SYSTEM, messages, max_rounds=3)

        self.assertEqual((first.status, second.status), ("round_limit", "done"))
        self.assertEqual(second.rounds, 2)
        self.assert_paired(messages)

    def test_limit_must_be_positive(self):
        for bad in [0, -1]:
            with self.subTest(max_rounds=bad), self.assertRaises(ValueError):
                run_task(FakeModel(), FakeTools(), SYSTEM, [Message("user", [TextBlock("hi")])], max_rounds=bad)


if __name__ == "__main__":
    unittest.main()
