"""S3-08 requirements for ProgressPreview.create_change. The site and the store are fakes.

运行：.venv\\Scripts\\python.exe tests\\test_progress_change.py
"""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bangumi_agent.confirmation import ExecutionService, SimulatedWriter, approval_message
from bangumi_agent.episode_writer import BangumiEpisodeWriter
from bangumi_agent.progress import ProgressPreview
from bangumi_agent.settings import Settings
from bangumi_agent.storage import MIGRATIONS, PlanError, PlanStore, SCHEMA_VERSION

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

    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]:
        return {"episode_id": episode_id, "subject_id": SUBJECT, "type": self.states[episode_id],
                "sort": episode_id - 9000, "ep": episode_id - 9000, "name": "集"}

    def get_all_my_episode_states(self, subject_id: int, limit: int = 40, max_pages: int = 5,
                                  episode_type: int | None = None) -> dict[str, Any]:
        self.requests += 1
        rows = [{"episode_id": episode_id, "type": state, "sort": episode_id - 9000, "ep": episode_id - 9000,
                 "name": f"ep{episode_id - 9000}", "name_cn": None}
                for episode_id, state in self.states.items()
                if episode_type is None or episode_id != 9090]
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

    def test_dropped_subject_is_allowed(self):
        self.site.collection_type = 5
        self.preview.create_change(SUBJECT, [9002], WATCHED)
        self.assertIsNotNone(self.store.saved)

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
            "two level list":(SUBJECT, [[9001], [9002]], WATCHED)
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


SCHEMA_V2 = """
CREATE TABLE operation_plans (
    plan_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL, status TEXT NOT NULL, updated_at INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '', skipped TEXT NOT NULL DEFAULT '[]');
CREATE TABLE plan_items (
    plan_id TEXT NOT NULL REFERENCES operation_plans(plan_id), subject_id INTEGER NOT NULL,
    episode_id INTEGER NOT NULL, subject_name TEXT NOT NULL, episode_name TEXT NOT NULL,
    episode_sort TEXT NOT NULL, before_type INTEGER NOT NULL, after_type INTEGER NOT NULL CHECK(after_type = 2),
    result TEXT, PRIMARY KEY (plan_id, episode_id));
INSERT INTO operation_plans VALUES ('old', 42, 100, 5000, 'partial', 200, '部分完成', '[{"episode_sort": 1}]');
INSERT INTO plan_items VALUES ('old', 100, 9002, '测试作品', '集', '2', 0, 2, 'not_applied');
PRAGMA user_version = 2;
"""


# Version 3: the S3-08 layout, before the subject's collection type was saved.
SCHEMA_V3 = """
CREATE TABLE operation_plans (
    plan_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL, status TEXT NOT NULL, updated_at INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '', skipped TEXT NOT NULL DEFAULT '[]');
CREATE TABLE plan_items (
    plan_id TEXT NOT NULL REFERENCES operation_plans(plan_id), subject_id INTEGER NOT NULL,
    episode_id INTEGER NOT NULL, subject_name TEXT NOT NULL, episode_name TEXT NOT NULL,
    episode_sort TEXT NOT NULL, before_type INTEGER NOT NULL, after_type INTEGER NOT NULL,
    result TEXT, PRIMARY KEY (plan_id, episode_id));
INSERT INTO operation_plans VALUES ('old', 42, 100, 5000, 'draft', 100, '', '[]');
INSERT INTO plan_items VALUES ('old', 100, 9001, '测试作品', '集', '1', 2, 0, NULL);
PRAGMA user_version = 3;
"""


class ChangeWithRealStoreTest(unittest.TestCase):
    """The whole path on a real temporary database; only the site and the write are simulated."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "progress.sqlite3"
        self.store = PlanStore(self.path)
        self.site = FakeSite()

    def run_change(self, episode_ids, target) -> tuple[dict[str, Any], SimulatedWriter]:
        def apply(episode_id):
            self.site.states[episode_id] = target
        writer = SimulatedWriter(on_write=apply)
        service = ExecutionService(self.site, self.store, writer, lambda: 1000)
        plan_id = ProgressPreview(self.site, self.store, lambda: 1000).create_change(SUBJECT, episode_ids, target)["plan_id"]
        service.approve(plan_id)
        return service.execute(plan_id), writer

    def test_undo_watched_on_one_episode_writes_unseen(self):
        plan, writer = self.run_change([9001], UNSEEN)
        self.assertEqual(plan["status"], "verified")
        self.assertEqual((writer.batches, writer.states, self.site.states[9001]), ([[9001]], [UNSEEN], UNSEEN))
        self.assertIn("读回为未收藏", plan["detail"])

    def test_batch_writes_the_target_state_and_an_op_counts(self):
        plan, writer = self.run_change([9002, 9003, 9090], DROPPED)
        self.assertEqual(plan["status"], "verified")
        self.assertEqual(writer.states, [DROPPED])
        self.assertEqual(self.site.states[9090], DROPPED)

    def test_popup_names_the_target_state(self):
        preview = ProgressPreview(self.site, self.store, lambda: 1000)
        single = preview.create_change(SUBJECT, [9001], UNSEEN)
        self.assertIn("修改：看过 → 未收藏", approval_message(single))
        batch = preview.create_change(SUBJECT, [9001, 9002, 9004], WISH)
        for text in ("3集标为想看", "原为看过：第1集", "原为未收藏：第2集", "原为抛弃：第4集"):
            self.assertIn(text, approval_message(batch))

    def test_popup_shows_the_subject_collection_state(self):
        preview = ProgressPreview(self.site, self.store, lambda: 1000)
        single = preview.create_change(SUBJECT, [9001], UNSEEN)
        batch = preview.create_change(SUBJECT, [9001, 9002], WISH)
        labels = {1: "想看", 2: "看过", 3: "在看", 4: "搁置", 5: "抛弃", None: "未记录"}
        for plan in (single, batch):
            for state, label in labels.items():
                with self.subTest(items=len(plan["items"]), state=state):
                    self.assertIn(f"条目状态：{label}", approval_message({**plan, "subject_type": state}))

    def test_every_way_of_creating_a_plan_saves_the_subject_state(self):
        self.site.collection_type = 5
        preview = ProgressPreview(self.site, self.store, lambda: 1000)
        creators = {"single": lambda: preview.create(SUBJECT, 9002),
                    "up_to": lambda: preview.create_up_to(SUBJECT, 9003),
                    "change": lambda: preview.create_change(SUBJECT, [9002], WISH)}
        for name, create in creators.items():
            with self.subTest(name):
                plan = create()
                self.assertEqual(plan["subject_type"], 5)
                self.assertIn("条目状态：抛弃", approval_message(plan))

    def test_version_3_plan_survives_and_shows_no_recorded_state(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SCHEMA_V3)
        old = self.store.get_plan("old", account_id=42)
        self.assertIsNone(old["subject_type"])
        self.assertEqual(old["items"][0]["after_type"], UNSEEN)
        self.assertIn("条目状态：未记录", approval_message(old))

    def test_version_2_database_keeps_its_plans_and_accepts_any_target(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SCHEMA_V2)
        old = self.store.get_plan("old", account_id=42)
        self.assertEqual((old["status"], old["detail"], old["skipped"]), ("partial", "部分完成", [{"episode_sort": 1}]))
        self.assertEqual(old["items"][0]["result"], "not_applied")
        plan, _ = self.run_change([9001], UNSEEN)
        self.assertEqual(plan["after_type"], UNSEEN)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(set(MIGRATIONS), set(range(SCHEMA_VERSION)))


class NoContent(io.BytesIO):
    status = 204


class WriterStateTest(unittest.TestCase):
    def writer(self) -> BangumiEpisodeWriter:
        return BangumiEpisodeWriter(Settings(access_token="offline-fixture"))

    def test_requests_carry_the_target_state(self):
        calls = [(lambda w: w.set_episode_state(SUBJECT, 9001, UNSEEN), "PUT", {"type": 0}),
                 (lambda w: w.set_episodes_state(SUBJECT, [9001, 9002], DROPPED), "PATCH",
                  {"episode_id": [9001, 9002], "type": 3})]
        for write, method, body in calls:
            with self.subTest(method), patch("bangumi_agent.episode_writer.build_opener") as opener:
                opener.return_value.open.return_value = NoContent()
                write(self.writer())
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual((request.get_method(), json.loads(request.data)), (method, body))

    def test_invalid_state_is_refused_before_any_request(self):
        with patch("bangumi_agent.episode_writer.build_opener") as opener, self.assertRaises(ValueError):
            self.writer().set_episode_state(SUBJECT, 9001, 4)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
