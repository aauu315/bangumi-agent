"""S4-R: the checks in tool_execution.py that tests/test_agent_loop.py does not reach through run_task.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_tool_execution
"""
import unittest

from bangumi_agent.agent.messages import ToolCallBlock
from bangumi_agent.agent.tool_execution import (INCOMPLETE_ARGUMENTS, UNKNOWN_TOOL, check_tool_call,
                                                refuse_tool_calls)

ALLOWED = {"search_subjects", "get_subject"}


class CheckToolCall(unittest.TestCase):
    def test_decisions(self):
        cases = [
            ("allowed tool, usable arguments", ToolCallBlock("c1", "search_subjects", {"keyword": "x"}),
             ("allow", "")),
            ("allowed tool, no arguments needed", ToolCallBlock("c1", "get_subject", {}), ("allow", "")),
            ("tool outside the list", ToolCallBlock("c1", "execute_episode_plan", {"plan_id": "p1"}),
             ("deny", UNKNOWN_TOOL.format(name="execute_episode_plan"))),
            ("allowed tool, unparsable arguments", ToolCallBlock("c1", "search_subjects", None),
             ("invalid", INCOMPLETE_ARGUMENTS)),
            # The name is checked first: a tool the model may not call is never asked for again.
            ("tool outside the list, unparsable arguments", ToolCallBlock("c1", "execute_episode_plan", None),
             ("deny", UNKNOWN_TOOL.format(name="execute_episode_plan"))),
        ]
        for label, call, expected in cases:
            with self.subTest(label):
                self.assertEqual(check_tool_call(call, ALLOWED), expected)


class RefuseToolCalls(unittest.TestCase):
    def test_every_call_gets_the_same_error_in_order(self):
        calls = [ToolCallBlock("c1", "search_subjects", {"keyword": "x"}), ToolCallBlock("c2", "get_subject", None)]
        results = refuse_tool_calls(calls, "已取消")

        self.assertEqual([(r.call_id, r.content, r.is_error) for r in results],
                         [("c1", "已取消", True), ("c2", "已取消", True)])


if __name__ == "__main__":
    unittest.main()
