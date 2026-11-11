"""S4-03: the frozen cases in evals/20-cases.json are well-formed (format in evals/README.md).

运行：.venv\\Scripts\\python.exe -m unittest tests.test_eval_cases
"""
import json
import re
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

from bangumi_agent.agent.tool_registry import AGENT_TOOLS

CASES_FILE = Path(__file__).parent.parent / "evals" / "20-cases.json"
PREVIEW_TOOLS = {"preview_episode_watched", "preview_watched_up_to", "preview_episode_change"}
CATEGORIES = ("query", "ambiguous", "preview", "injection", "failure")


def load_cases() -> dict[str, Any]:
    with CASES_FILE.open(encoding="utf-8") as file:
        return json.load(file)


class FrozenCases(unittest.TestCase):
    def setUp(self):
        self.doc = load_cases()
        self.cases: list[dict[str, Any]] = self.doc["cases"]

    def test_twenty_cases_four_per_category(self):
        self.assertEqual(len({case["id"] for case in self.cases}), 20)
        self.assertEqual(Counter(case["category"] for case in self.cases), {name: 4 for name in CATEGORIES})

    def test_cases_only_name_agent_tools(self):
        for case in self.cases + self.doc["spare_cases"]:
            with self.subTest(case=case["id"]):
                expect = case["expect"]
                named = [name for item in expect["required_tools"]
                         for name in (item if isinstance(item, list) else [item])]
                named += [name for pair in expect["order"] for name in pair]
                named += list(expect["max_calls"]) + [rule["tool"] for rule in case["tool_rules"]]
                self.assertTrue(set(named) <= set(AGENT_TOOLS), set(named) - set(AGENT_TOOLS))
                self.assertTrue(set(expect["allowed_previews"]) <= PREVIEW_TOOLS)
                self.assertTrue(all(len(pair) == 2 for pair in expect["order"]))
                self.assertTrue(set(case["fixtures"]) <= set(self.doc["fixture_sets"]))

    def test_plans_match_allowed_previews(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                expect = case["expect"]
                if expect["plans"] == 0:
                    self.assertEqual(expect["allowed_previews"], [])
                    self.assertIsNone(expect["preview_args"])
                else:
                    self.assertTrue(expect["allowed_previews"])
                    self.assertIsNotNone(expect["preview_args"])

    def test_rules_have_one_outcome_and_patterns_compile(self):
        rules = [rule for rules in self.doc["fixture_sets"].values() for rule in rules]
        rules += [rule for case in self.cases for rule in case["tool_rules"]]
        for rule in rules:
            self.assertIn(rule["tool"], AGENT_TOOLS)
            self.assertEqual(("result" in rule) + ("error" in rule), 1, rule["tool"])
            self.assertNotIn(rule["tool"], PREVIEW_TOOLS)  # previews make real drafts, not fixtures
        for case in self.cases:
            for check in case["answer_checks"]:
                for key in ("must_match", "must_not_match"):
                    if key in check:
                        re.compile(check[key])


if __name__ == "__main__":
    unittest.main()
