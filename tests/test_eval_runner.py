import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bangumi_agent.agent.tool_registry import McpTools
from bangumi_agent.confirmation import SimulatedWriter
from bangumi_agent.evals.control import ScriptedModel
from bangumi_agent.evals.runner import run_case


class EvalRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "evals/20-cases.json"
        cls.suite = json.loads(path.read_text(encoding="utf-8"))
        cls.specs = McpTools().specs()

    def run_control(self, case: dict) -> dict:
        return run_case(case, self.suite["fixture_sets"], ScriptedModel(case["id"]), self.specs)

    def case(self, case_id: str) -> dict:
        return next(case for case in self.suite["cases"] if case["id"] == case_id)

    def test_all_frozen_controls(self):
        for case in self.suite["cases"]:
            with self.subTest(case=case["id"]):
                result = self.run_control(case)
                self.assertTrue(result["passed"], result)
                self.assertEqual(result["plan_statuses"], ["draft"] * case["expect"]["plans"])
                self.assertEqual(result["unapproved_writes"], 0)
                if case["id"] == "4-4":
                    self.assertTrue(any("execute_episode_plan" in error for error in result["tool_errors"]))
                if case["id"] == "5-1":
                    self.assertEqual(result["tool_errors"], ["Bangumi 返回 HTTP 500"] * 3)
                if case["id"] == "5-4":
                    self.assertIsNone(result["answer_checks"][0]["passed"])

    def test_reports_plan_fixture_and_answer_failures(self):
        case = self.case("3-1")
        wrong_count = {**case, "expect": {**case["expect"], "plans": 0}}
        missing = {**case, "fixtures": []}
        wrong_answer = {**case, "answer_checks": [{"rule": "需要确认", "must_not_include": ["确认"]}]}
        for modified, problem in ((wrong_count, "计划份数"), (missing, "夹具缺数据"),
                                  (wrong_answer, "回答自动检查")):
            with self.subTest(problem=problem):
                result = self.run_control(modified)
                self.assertFalse(result["passed"])
                self.assertIn(problem, result["failures"])

    def test_any_unapproved_writer_call_fails_an_otherwise_passing_case(self):
        writer = SimulatedWriter()
        writer.set_episode_state(400602, 1400006, 2)
        with patch("bangumi_agent.evals.runner.SimulatedWriter", return_value=writer):
            result = self.run_control(self.case("3-1"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"], ["未经批准的写入"])
        self.assertEqual(result["unapproved_writes"], 1)
