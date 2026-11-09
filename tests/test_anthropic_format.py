"""S4-01 Anthropic-format converter, HTTP client and tool registry. No network: the transport is a fake.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_anthropic_format
"""
import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.server import MCPServer

from bangumi_agent.agent.anthropic_format import (AnthropicConfig, AnthropicModel, ModelError, anthropic,
                                                  build_request, deepseek, parse_response)
from bangumi_agent.agent.loop import SUMMARY_REQUEST, run_task
from bangumi_agent.agent.messages import (Message, OpaqueBlock, TextBlock, ToolCallBlock, ToolError,
                                          ToolResultBlock, ToolSpec, tool_calls_of)
from bangumi_agent.agent.tool_registry import AGENT_TOOLS, McpTools

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
KEY = "sk-fake-model-key-0123456789"
CONFIG = AnthropicConfig("https://example.test/anthropic", "deepseek-flash", max_tokens=1000)
SPECS = [ToolSpec("search_subjects", "搜索条目", {"type": "object", "properties": {}})]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Answers with the queued (status, body) pairs and remembers each request."""

    def __init__(self, *answers: tuple[int, Any]):
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        self.requests.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        status, payload = self.answers.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        return status, raw


class BuildRequest(unittest.TestCase):
    def test_tool_results_become_one_user_message_with_the_summary_after_them(self):
        history = [
            Message("user", [TextBlock("找芙莉莲")]),
            Message("assistant", [TextBlock("我查一下。"), ToolCallBlock("t1", "search_subjects", {"keyword": "芙莉莲"}),
                                  ToolCallBlock("t2", "get_subject", None)]),
            Message("tool", [ToolResultBlock("t1", "结果"), ToolResultBlock("t2", "参数不完整", is_error=True)]),
            Message("user", [TextBlock(SUMMARY_REQUEST)]),
        ]
        body = build_request(CONFIG, "系统", history, SPECS, allow_tools=False)

        self.assertEqual([m["role"] for m in body["messages"]], ["user", "assistant", "user"])
        assistant = body["messages"][1]["content"]
        self.assertEqual(assistant[1], {"type": "tool_use", "id": "t1", "name": "search_subjects",
                                        "input": {"keyword": "芙莉莲"}})
        self.assertEqual(assistant[2]["input"], {})  # cut-off call: still sent, so its result pairs
        last = body["messages"][2]["content"]
        self.assertEqual([b["type"] for b in last], ["tool_result", "tool_result", "text"])
        self.assertNotIn("is_error", last[0])
        self.assertTrue(last[1]["is_error"])
        self.assertEqual(body["tool_choice"], {"type": "none"})
        self.assertEqual(body["system"], "系统")
        self.assertEqual(body["max_tokens"], 1000)

    def test_tool_choice_and_optional_fields(self):
        history = [Message("user", [TextBlock("hi")])]
        with_tools = build_request(CONFIG, "", history, SPECS, allow_tools=True)
        without = build_request(CONFIG, "", history, [], allow_tools=True)

        self.assertEqual(with_tools["tool_choice"], {"type": "auto"})
        self.assertEqual(with_tools["tools"][0]["name"], "search_subjects")
        self.assertNotIn("system", with_tools)
        self.assertNotIn("tools", without)
        self.assertNotIn("tool_choice", without)

    def test_presets(self):
        self.assertEqual((deepseek().base_url, deepseek().max_tokens),
                         ("https://api.deepseek.com/anthropic", 384_000))
        self.assertEqual((anthropic().base_url, anthropic().model, anthropic().max_tokens),
                         ("https://api.anthropic.com", "claude-opus-5-5", 128_000))


class ParseResponse(unittest.TestCase):
    def test_thinking_text_and_two_calls(self):
        reply = parse_response(fixture("thinking_then_two_tools.json"))

        self.assertEqual(reply.stop, "tool_calls")
        self.assertIsInstance(reply.message.content[0], OpaqueBlock)
        calls = tool_calls_of(reply.message)
        self.assertEqual([(c.id, c.name) for c in calls], [("toolu_01", "search_subjects"), ("toolu_02", "get_subject")])
        self.assertEqual(calls[1].arguments, {"subject_id": 400602})
        self.assertEqual((reply.usage.input_tokens, reply.usage.output_tokens), (1200, 85))

    def test_thinking_goes_back_unchanged(self):
        raw = fixture("thinking_then_two_tools.json")
        reply = parse_response(raw)
        body = build_request(CONFIG, "", [Message("user", [TextBlock("找")]), reply.message], SPECS, True)

        self.assertEqual(body["messages"][1]["content"], raw["content"])

    def test_stop_reasons(self):
        cases = {"final_text.json": "end", "cut_tool_call.json": "max_tokens", "refusal.json": "other"}
        for name, stop in cases.items():
            with self.subTest(fixture=name):
                self.assertEqual(parse_response(fixture(name)).stop, stop)

    def test_non_object_arguments_count_as_incomplete(self):
        raw = fixture("cut_tool_call.json")
        raw["content"][1]["input"] = '{"keyword": "芙'
        [call] = tool_calls_of(parse_response(raw).message)
        self.assertIsNone(call.arguments)

    def test_broken_bodies_raise_model_error(self):
        for body in [[], {"content": "text"}, {"content": [{"type": "tool_use", "name": "x"}]}]:
            with self.subTest(body=body), self.assertRaises(ModelError):
                parse_response(body)


class HttpModel(unittest.TestCase):
    def test_request_url_headers_and_body(self):
        transport = FakeTransport((200, fixture("final_text.json")))
        model = AnthropicModel(CONFIG, KEY, transport)
        reply = model.complete("系统", [Message("user", [TextBlock("hi")])], SPECS, True)

        sent = transport.requests[0]
        self.assertEqual(sent["url"], "https://example.test/anthropic/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], KEY)
        self.assertEqual(sent["headers"]["anthropic-version"], "2026-10-21")
        self.assertEqual(sent["body"]["model"], "deepseek-flash")
        self.assertEqual(reply.stop, "end")
        self.assertNotIn(KEY, repr(model))

    def test_errors_never_carry_the_key_or_the_body(self):
        echo = {"type": "error", "error": {"type": "authentication_error", "message": f"bad key {KEY}"}}
        cases = [(401, echo), (500, b"<html>oops</html>"), (200, b"not json")]
        for status, payload in cases:
            with self.subTest(status=status):
                model = AnthropicModel(CONFIG, KEY, FakeTransport((status, payload)))
                with self.assertRaises(ModelError) as caught:
                    model.complete("", [Message("user", [TextBlock("hi")])], [], True)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn("bad key", str(caught.exception))
        self.assertIn("authentication_error", self._error_for(401, echo))

    def _error_for(self, status: int, payload: Any) -> str:
        model = AnthropicModel(CONFIG, KEY, FakeTransport((status, payload)))
        try:
            model.complete("", [Message("user", [TextBlock("hi")])], [], True)
        except ModelError as exc:
            return str(exc)
        raise AssertionError("没有报错")

    def test_blank_key_is_refused(self):
        with self.assertRaises(ValueError):
            AnthropicModel(CONFIG, "  ")


class EchoTools:
    def specs(self) -> list[ToolSpec]:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        return [ToolSpec("search_subjects", "搜索", schema), ToolSpec("get_subject", "详情", schema)]

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        return f"{name}:{arguments}"


class LoopOverTheWire(unittest.TestCase):
    def test_run_task_through_the_converter(self):
        first = fixture("thinking_then_two_tools.json")
        transport = FakeTransport((200, first), (200, fixture("final_text.json")))
        messages = [Message("user", [TextBlock("帮我找一下芙莉莲")])]
        result = run_task(AnthropicModel(CONFIG, KEY, transport), EchoTools(), "系统", messages)

        self.assertEqual((result.status, result.rounds), ("done", 2))
        self.assertEqual(result.text, "《葬送的芙莉莲》条目 ID 是 400602。")
        second = transport.requests[1]["body"]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[1]["content"], first["content"])  # thinking passed back unchanged
        self.assertEqual([b["tool_use_id"] for b in second[2]["content"]], ["toolu_01", "toolu_02"])


class Registry(unittest.TestCase):
    def test_offers_the_listed_tools_and_never_execution(self):
        names = [spec.name for spec in McpTools().specs()]

        self.assertEqual(names, list(AGENT_TOOLS))
        self.assertNotIn("execute_episode_plan", names)
        with self.assertRaises(ToolError):
            McpTools().run("execute_episode_plan", {"plan_id": "x"})

    def test_runs_a_tool_and_reports_bad_arguments(self):
        tools = McpTools()
        with patch("bangumi_agent.api_client._request_json", return_value={"id": 253, "name": "カウボーイビバップ"}):
            text = tools.run("get_subject", {"subject_id": 253})
        self.assertEqual(json.loads(text)["id"], 253)
        with self.assertRaises(ToolError):
            tools.run("get_subject", {"subject_id": -1})

    def test_crash_text_is_not_passed_on(self):
        server = MCPServer("crash")

        @server.tool()
        def boom() -> str:
            raise RuntimeError(f"Authorization: Bearer {KEY}")

        tools = McpTools(server, allowed=("boom",))
        with self.assertRaises(Exception) as caught:
            tools.run("boom", {})
        self.assertNotIsInstance(caught.exception, ToolError)
        self.assertNotIn(KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
