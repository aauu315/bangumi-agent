"""S4-03: score_tools, the user's scoring function (evals/scoring.py).

运行：.venv\\Scripts\\python.exe -m unittest tests.test_eval_scoring
"""
import unittest
from typing import Any

from bangumi_agent.evals.scoring import (MISSING_TOOL, TOO_MANY_CALLS, UNEXPECTED_PREVIEW, WRONG_ORDER,
                                         WRONG_PREVIEW_ARGS, Problem, ToolCall, score_tools)

FRIEREN = 400602


def expect(required: list[Any] | None = None, order: list[list[str]] | None = None,
           previews: list[str] | None = None, max_calls: dict[str, int] | None = None,
           preview_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """An expect block shaped like evals/20-cases.json; plans is there but not scored here."""
    return {"required_tools": required or [], "order": order or [], "allowed_previews": previews or [],
            "max_calls": max_calls or {}, "plans": 1 if previews else 0, "preview_args": preview_args}


def calls(*names: str, **arguments: dict[str, Any]) -> list[ToolCall]:
    """Calls with empty arguments; calls("a", "b", b={"x": 1}) gives b those arguments."""
    return [ToolCall(name, arguments.get(name, {})) for name in names]


def kinds(problems: list[Problem]) -> list[tuple[str, str]]:
    return sorted((problem.kind, problem.tool) for problem in problems)


# Case 3-3: search, episodes and states, then change episode 3 to dropped.
CHANGE_ARGS = {"subject_id": FRIEREN, "episode_ids": [1400003], "target_state": 3}
CHANGE = expect(required=["search_subjects", "get_episodes", "get_my_episode_states", "preview_episode_change"],
                order=[["search_subjects", "get_episodes"], ["get_episodes", "preview_episode_change"],
                       ["get_my_episode_states", "preview_episode_change"]],
                previews=["preview_episode_change"], preview_args=CHANGE_ARGS)


class Passing(unittest.TestCase):
    def test_runs_that_meet_every_rule(self):
        runs = {
            "按预期": calls("search_subjects", "get_episodes", "get_my_episode_states", "preview_episode_change",
                          preview_episode_change=CHANGE_ARGS),
            "非关键顺序不同：先查进度再查章节": calls(
                "search_subjects", "get_my_episode_states", "get_episodes", "preview_episode_change",
                preview_episode_change=CHANGE_ARGS),
            "多调了只读工具": calls("search_subjects", "get_subject", "get_episodes", "get_my_episode_states",
                              "get_subject", "preview_episode_change", preview_episode_change=CHANGE_ARGS),
            "预览参数多出别的键": calls(
                "search_subjects", "get_episodes", "get_my_episode_states", "preview_episode_change",
                preview_episode_change={**CHANGE_ARGS, "note": "extra"}),
        }
        for label, run in runs.items():
            with self.subTest(label):
                score = score_tools(CHANGE, run)
                self.assertEqual(score.problems, [])
                self.assertTrue(score.passed)

    def test_nothing_expected_and_nothing_called(self):
        # Case 4-3: the model should just say it cannot; no tools is fine.
        self.assertTrue(score_tools(expect(), []).passed)


class Required(unittest.TestCase):
    def test_missing_tool(self):
        score = score_tools(expect(required=["search_subjects", "get_my_episode_states"]), calls("search_subjects"))
        self.assertFalse(score.passed)
        self.assertEqual(kinds(score.problems), [(MISSING_TOOL, "get_my_episode_states")])

    def test_any_one_of_the_alternatives_is_enough(self):
        rule = expect(required=["search_subjects", ["get_my_collection", "get_my_collections"]])
        for alternative in ("get_my_collection", "get_my_collections"):
            with self.subTest(alternative):
                self.assertTrue(score_tools(rule, calls("search_subjects", alternative)).passed)

    def test_none_of_the_alternatives(self):
        rule = expect(required=["search_subjects", ["get_my_collection", "get_my_collections"]])
        problems = score_tools(rule, calls("search_subjects", "get_subject")).problems
        self.assertEqual([problem.kind for problem in problems], [MISSING_TOOL])
        self.assertIn("get_my_collection", problems[0].tool)
        self.assertIn("get_my_collections", problems[0].tool)


class Order(unittest.TestCase):
    RULE = expect(order=[["search_subjects", "get_episodes"]])

    def test_order_rules(self):
        runs = {
            "B 在 A 之前": (calls("get_episodes", "search_subjects"), [(WRONG_ORDER, "get_episodes")]),
            "B 被调用但 A 从没调用": (calls("get_episodes"), [(WRONG_ORDER, "get_episodes")]),
            "A 在 B 之前，之后 B 又调用": (calls("search_subjects", "get_episodes", "get_episodes"), []),
            "第一次 B 已在 A 之后，后面又有 A": (calls("search_subjects", "get_episodes", "search_subjects"), []),
            "B 根本没调用，不算顺序错": (calls("search_subjects"), []),
        }
        for label, (run, expected) in runs.items():
            with self.subTest(label):
                self.assertEqual(kinds(score_tools(self.RULE, run).problems), expected)

    def test_only_the_first_b_counts(self):
        # The first get_episodes came too early; a later one after the search does not fix it.
        run = calls("get_episodes", "search_subjects", "get_episodes")
        self.assertEqual(kinds(score_tools(self.RULE, run).problems), [(WRONG_ORDER, "get_episodes")])

    def test_one_problem_per_pair(self):
        # B twice before A is still one broken [A, B] pair.
        run = calls("get_episodes", "get_episodes", "search_subjects")
        self.assertEqual(kinds(score_tools(self.RULE, run).problems), [(WRONG_ORDER, "get_episodes")])


class Previews(unittest.TestCase):
    def test_preview_not_allowed(self):
        # Case 2-1: the model should ask first, so no preview is allowed at all.
        run = calls("search_subjects", "preview_episode_watched")
        problems = score_tools(expect(required=["search_subjects"]), run).problems
        self.assertEqual(kinds(problems), [(UNEXPECTED_PREVIEW, "preview_episode_watched")])

    def test_wrong_kind_of_preview(self):
        # Case 3-2: "watched up to 10" must not become single-episode previews, one per episode.
        # One problem per tool, however many times it was called; the count goes in the detail.
        rule = expect(previews=["preview_watched_up_to"])
        run = calls(*["preview_episode_watched"] * 10)
        problems = score_tools(rule, run).problems
        self.assertEqual(kinds(problems), [(UNEXPECTED_PREVIEW, "preview_episode_watched")])
        self.assertIn("10", problems[0].detail)

    def test_two_kinds_not_allowed(self):
        run = calls("preview_episode_watched", "preview_episode_change", "preview_episode_watched")
        self.assertEqual(kinds(score_tools(expect(), run).problems),
                         [(UNEXPECTED_PREVIEW, "preview_episode_change"),
                          (UNEXPECTED_PREVIEW, "preview_episode_watched")])


class MaxCalls(unittest.TestCase):
    def test_retry_limit(self):
        rule = expect(max_calls={"get_subject": 3})
        for count, expected in ((1, []), (3, []), (4, [(TOO_MANY_CALLS, "get_subject")])):
            with self.subTest(count=count):
                run = calls("search_subjects", *["get_subject"] * count)
                self.assertEqual(kinds(score_tools(rule, run).problems), expected)

    def test_limit_for_a_tool_never_called(self):
        self.assertTrue(score_tools(expect(max_calls={"get_my_episode_states": 1}), []).passed)


class PreviewArgs(unittest.TestCase):
    def test_wrong_arguments(self):
        runs = {
            "第几集写成了章节 ID": {**CHANGE_ARGS, "episode_ids": [3]},
            "目标状态不对": {**CHANGE_ARGS, "target_state": 2},
            "缺了条目 ID": {"episode_ids": [1400003], "target_state": 3},
        }
        for label, arguments in runs.items():
            with self.subTest(label):
                run = calls("search_subjects", "get_episodes", "get_my_episode_states", "preview_episode_change",
                            preview_episode_change=arguments)
                self.assertEqual(kinds(score_tools(CHANGE, run).problems),
                                 [(WRONG_PREVIEW_ARGS, "preview_episode_change")])

    def test_wrong_season(self):
        # Case 2-2: the preview must be for season 2, the one being watched.
        rule = expect(previews=["preview_watched_up_to"], preview_args={"subject_id": 278826, "episode_id": 2788003})
        run = calls("preview_watched_up_to", preview_watched_up_to={"subject_id": 140001, "episode_id": 1400003})
        self.assertEqual(kinds(score_tools(rule, run).problems), [(WRONG_PREVIEW_ARGS, "preview_watched_up_to")])

    def test_no_preview_args_means_no_check(self):
        rule = expect(previews=["preview_episode_watched"])
        run = calls("preview_episode_watched", preview_episode_watched={"subject_id": 1, "episode_id": 2})
        self.assertTrue(score_tools(rule, run).passed)


class Together(unittest.TestCase):
    def test_every_broken_rule_is_listed(self):
        # Case 3-3 gone wrong in four ways at once: no states lookup, preview before the episodes,
        # a second (single-episode) preview, and the episode number passed instead of its ID.
        run = calls("search_subjects", "preview_episode_change", "get_episodes", "preview_episode_watched",
                    preview_episode_change={**CHANGE_ARGS, "episode_ids": [3]})
        problems = score_tools(CHANGE, run).problems
        self.assertEqual(kinds(problems), sorted([
            (MISSING_TOOL, "get_my_episode_states"),
            (WRONG_ORDER, "preview_episode_change"),       # [get_episodes, preview_episode_change]
            (WRONG_ORDER, "preview_episode_change"),       # [get_my_episode_states, preview_episode_change]
            (UNEXPECTED_PREVIEW, "preview_episode_watched"),
            (WRONG_PREVIEW_ARGS, "preview_episode_change"),
        ]))
        self.assertTrue(all(problem.detail for problem in problems), "每条问题都要有一行说明")


if __name__ == "__main__":
    unittest.main()
