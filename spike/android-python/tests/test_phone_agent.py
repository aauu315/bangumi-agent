"""Offline check of the S4-01 phone entry. Fake transport and patched Bangumi reads; no network, no real key."""
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
sys.path.insert(0, str(ROOT / "src"))
import phone_agent  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "anthropic"
KEY = "sk-fake-phone-key-0123456789"


class FakeTransport:
    def __init__(self, *bodies: dict[str, Any]):
        self.bodies = list(bodies)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        self.sent.append({"url": url, "body": json.loads(body), "timeout": timeout})
        return 200, json.dumps(self.bodies.pop(0), ensure_ascii=False).encode()


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PhoneAgent(unittest.TestCase):
    def test_full_task_with_two_tools(self):
        transport = FakeTransport(fixture("thinking_then_two_tools.json"), fixture("final_text.json"))
        with patch.object(phone_agent.api_client, "search_subjects", return_value={"data": []}) as search, \
                patch.object(phone_agent.api_client, "get_subject", return_value={"id": 400602}) as subject:
            text = phone_agent.run_agent(KEY, "芙莉莲", transport)

        self.assertIn("状态：done，请求 2 次", text)
        self.assertIn("400602", text)
        self.assertEqual(search.call_args.kwargs["limit"], 5)
        subject.assert_called_once_with(400602)
        self.assertTrue(transport.sent[0]["url"].startswith("https://api.deepseek.com/anthropic"))
        self.assertEqual(transport.sent[0]["timeout"], phone_agent.TIMEOUT_SECONDS)
        self.assertNotIn(KEY, text)

    def test_bad_arguments_become_tool_errors(self):
        tools = phone_agent.PhoneTools()
        for name, arguments in [("get_subject", {}), ("get_subject", {"subject_id": "1"}),
                                ("search_subjects", {"keyword": " "}), ("execute_episode_plan", {})]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(phone_agent.ToolError):
                tools.run(name, arguments)

    def test_blank_key_and_question(self):
        self.assertIn("API Key", phone_agent.run_agent(" ", "芙莉莲"))
        self.assertEqual(phone_agent.run_agent(KEY, " "), "请输入问题")


if __name__ == "__main__":
    unittest.main()
