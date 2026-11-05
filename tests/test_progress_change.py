"""S3-08 requirements for ProgressPreview.create_change. The site and the store are fakes.

运行：.venv\\Scripts\\python.exe tests\\test_progress_change.py
"""
import unittest
from typing import Any
from unittest.mock import patch

from bangumi_agent.progress import ProgressPreview
from bangumi_agent.storage import PlanError

UNSEEN, WISH, WATCHED, DROPPED = 0, 1, 2, 3
SUBJECT = 100


class FakeSite:
    """A fake account. Episode IDs are 9000 + number; 9090 is an OP, which main-only reads miss."""

    def __init__(self):
        self.states = {9001: WATCHED, 9002: UNSEEN, 9003: WISH, 9004: DROPPED, 9005: WATCHED, 9090: UNSEEN}
        self.collection_type = 3  # 在看
        self.complete = True
        self.requests = 0

    def get_me(self) -> dict[str, Any]:
        self.requests += 1
        return {"id": 42, "username": "fixture-user"}

    def get_my_collection(self, subject_id: int) -> dict[str, Any]:
        self.requests += 1
        return {"subject_id": subject_id, "type": self.collection_type, "name": "测试作品"}

    def get_all_my_episode_states(self, subject_id: int, limit: int = 40, max_pages: int = 5,
                                  episode_type: int | None = None) -> dict[str, Any]:
        self.requests += 1
        rows = [{"episode_id": episode_id, "type": state, "sort": episode_id - 9000, "ep": episode_id - 9000,
                 "name": f"ep{episode_id - 9000}", "name_cn": None}
                for episode_id, state in self.states.items()]
        return {"data": rows, "total": len(rows), "complete": self.complete}


class FakeStore:
    """Remembers what create_many received; get_preview returns it so tests can look at the plan."""

    def __init__(self):
        self.saved: dict[str, Any] | None = None

    def create_many(self, **kwargs) -> str:
        self.saved = kwargs
        return "plan-1"

    def get_preview(self, plan_id, *, account_id, now) -> dict[str, Any]:
        assert self.saved is not None
        return {"plan_id": plan_id, "account_id": account_id, "now": now, **self.saved}


class CreateChangeTest(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.store = FakeStore()
        self.preview = ProgressPreview(self.site, self.store, lambda: 1000)

    def changed_ids(self, plan) -> list[int]:
        return sorted(item["episode_id"] for item in plan["items"])

    def test_every_target_state_saves_the_episodes_that_differ(self):
        cases = {UNSEEN: [9001, 9003, 9004], WISH: [9001, 9002, 9004],
                 WATCHED: [9002, 9003, 9004], DROPPED: [9001, 9002, 9003]}
        for target, expected in cases.items():
            with self.subTest(target=target):
                plan = self.preview.create_change(SUBJECT, [9001, 9002, 9003, 9004], target)
                self.assertEqual(self.changed_ids(plan), expected)
                self.assertEqual(plan["after_type"], target)

    def test_one_id_is_a_single_episode_change_and_can_undo_watched(self):
        plan = self.preview.create_change(SUBJECT, [9001], UNSEEN)
        self.assertEqual(len(plan["items"]), 1)
        item = plan["items"][0]
        self.assertEqual(item, {"episode_id": 9001, "episode_name": "ep1", "episode_sort": 1, "before_type": WATCHED})

    def test_already_at_target_is_skipped_and_listed(self):
        plan = self.preview.create_change(SUBJECT, [9001, 9002], WATCHED)
        self.assertEqual(self.changed_ids(plan), [9002])
        self.assertEqual(plan["skipped"], [{"episode_sort": 1, "episode_name": "ep1", "reason": "已是目标状态"}])

    def test_all_already_at_target_saves_nothing(self):
        with self.assertRaisesRegex(PlanError, "无需"):
            self.preview.create_change(SUBJECT, [9001, 9005], WATCHED)
        self.assertIsNone(self.store.saved)

    def test_an_id_outside_this_subject_rejects_the_whole_plan(self):
        with self.assertRaisesRegex(PlanError, "不属于"):
            self.preview.create_change(SUBJECT, [9002, 7777], WATCHED)
        self.assertIsNone(self.store.saved)

    def test_dropped_subject_is_refused(self):
        self.site.collection_type = 5
        with self.assertRaisesRegex(PlanError, "抛弃"):
            self.preview.create_change(SUBJECT, [9002], WATCHED)
        self.assertIsNone(self.store.saved)

    def test_unfinished_episode_pages_are_refused(self):
        self.site.complete = False
        with self.assertRaisesRegex(PlanError, "没有读完"):
            self.preview.create_change(SUBJECT, [9002], WATCHED)
        self.assertIsNone(self.store.saved)

    def test_non_main_episodes_can_be_changed(self):
        # Unlike "watched up to", the caller names exact IDs, so an OP is a valid target.
        with patch.object(self.site, "get_all_my_episode_states", wraps=self.site.get_all_my_episode_states) as read:
            plan = self.preview.create_change(SUBJECT, [9090], WATCHED)
        self.assertEqual(self.changed_ids(plan), [9090])
        self.assertIsNone(read.call_args.kwargs["episode_type"])

    def test_bad_input_is_refused_before_any_request(self):
        cases = {
            "target too big": (SUBJECT, [9002], 4),
            "target negative": (SUBJECT, [9002], -1),
            "target is a bool": (SUBJECT, [9002], True),
            "no episodes": (SUBJECT, [], WATCHED),
            "episodes is not a list": (SUBJECT, 9002, WATCHED),
            "more than 50": (SUBJECT, list(range(1, 52)), WATCHED),
            "duplicate id": (SUBJECT, [9002, 9002], WATCHED),
            "bad episode id": (SUBJECT, [0], WATCHED),
            "bad subject id": (0, [9002], WATCHED),
        }
        for name, args in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.preview.create_change(*args)
        self.assertEqual(self.site.requests, 0)
        self.assertIsNone(self.store.saved)

    def test_exactly_50_episodes_is_allowed(self):
        self.site.states = {episode_id: UNSEEN for episode_id in range(9001, 9051)}
        plan = self.preview.create_change(SUBJECT, list(range(9001, 9051)), WATCHED)
        self.assertEqual(len(plan["items"]), 50)

    def test_saved_plan_has_owner_subject_and_expiry(self):
        plan = self.preview.create_change(SUBJECT, [9002], WATCHED, ttl_seconds=60)
        self.assertEqual((plan["account_id"], plan["subject_id"], plan["subject_name"]), (42, SUBJECT, "测试作品"))
        self.assertEqual((plan["created_at"], plan["expires_at"]), (1000, 1060))


if __name__ == "__main__":
    unittest.main()
