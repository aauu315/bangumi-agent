"""Offline S3-07 "watched up to": fake site, simulated writer, temporary SQLite only."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bangumi_agent.account_client import AccountReadError
from bangumi_agent.confirmation import ExecutionService, SimulatedWriter, approval_message
from bangumi_agent.episode_writer import BangumiEpisodeWriter
from bangumi_agent.progress import ProgressPreview
from bangumi_agent.settings import Settings
from bangumi_agent.storage import MIGRATIONS, PlanError, PlanStore, SCHEMA_VERSION

UNSEEN, WISH, WATCHED, DROPPED = 0, 1, 2, 3

# The S3-03 layout: one episode per plan.
SCHEMA_V1 = """
CREATE TABLE operation_plans (
    plan_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft', 'verified')),
    updated_at INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE plan_items (
    plan_id TEXT PRIMARY KEY REFERENCES operation_plans(plan_id), subject_id INTEGER NOT NULL,
    episode_id INTEGER NOT NULL, subject_name TEXT NOT NULL, episode_name TEXT NOT NULL,
    episode_sort TEXT NOT NULL, before_type INTEGER NOT NULL, after_type INTEGER NOT NULL);
INSERT INTO operation_plans VALUES ('old', 42, 100, 5000, 'verified', 200, '读回为看过');
INSERT INTO plan_items VALUES ('old', 100, 9001, '测试作品', '小雪', '4', 0, 2);
PRAGMA user_version = 1;
"""


class BatchSite:
    """A fake account: episode IDs are 9000 + their number, states given by number."""

    def __init__(self, states: dict[int, int]):
        self.states = {9000 + number: state for number, state in states.items()}
        self.complete, self.fail_reads = True, False

    def get_me(self) -> dict[str, Any]:
        return {"id": 42, "username": "fixture-user"}

    def get_my_collection(self, subject_id: int) -> dict[str, Any]:
        return {"subject_id": subject_id, "type": 3, "name": "测试作品"}

    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]:
        return {"episode_id": episode_id, "subject_id": 100, "type": self.states[episode_id], "sort": 1, "name": "集"}

    def get_all_my_episode_states(self, subject_id: int, limit: int = 40, max_pages: int = 5,
                                  episode_type: int | None = None) -> dict[str, Any]:
        if self.fail_reads:
            raise AccountReadError("模拟：读回超时")
        rows = [{"episode_id": episode_id, "type": state, "sort": episode_id - 9000,
                 "name": f"ep{episode_id - 9000}", "name_cn": None}
                for episode_id, state in self.states.items()]
        return {"data": rows, "total": len(rows), "complete": self.complete}

    def apply_write(self, episode_id: int) -> None:
        self.states[episode_id] = WATCHED


class BatchTestCase(unittest.TestCase):
    """Shared set-up. plan_up_to(n) creates and approves "watched up to episode n"."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "progress.sqlite3"
        self.store = PlanStore(self.path)
        self.site = BatchSite({1: WATCHED, 2: UNSEEN, 3: WISH, 4: DROPPED, 5: UNSEEN, 6: UNSEEN, 7: UNSEEN})

    def preview(self) -> ProgressPreview:
        return ProgressPreview(self.site, self.store, lambda: 1000)

    def service(self, writer: SimulatedWriter) -> ExecutionService:
        return ExecutionService(self.site, self.store, writer, lambda: 1000)

    def writer(self, *args, **kwargs) -> SimulatedWriter:
        return SimulatedWriter(*args, on_write=self.site.apply_write, **kwargs)

    def plan_up_to(self, number: int, service: ExecutionService) -> str:
        plan_id = self.preview().create_up_to(100, 9000 + number)["plan_id"]
        service.approve(plan_id)
        return plan_id

    def results(self, plan_id: str) -> dict[int, str | None]:
        """Per-episode result by episode number."""
        items = self.store.get_plan(plan_id, account_id=42)["items"]
        return {item["episode_id"] - 9000: item["result"] for item in items}


class SelectionRuleTest(BatchTestCase):
    def test_selects_unseen_and_wish_up_to_target_and_skips_watched_and_dropped(self):
        plan = self.preview().create_up_to(100, 9005)
        self.assertEqual([(item["episode_id"], item["before_type"]) for item in plan["items"]],
                         [(9002, UNSEEN), (9003, WISH), (9005, UNSEEN)])
        self.assertEqual([(row["episode_sort"], row["reason"]) for row in plan["skipped"]],
                         [(1, "已看过"), (4, "已抛弃")])

    def test_selection_follows_sort_not_episode_id(self):
        # Real data: Cowboy Bebop jumps from ID 519 to 7027 between episodes 1 and 2, and a
        # second season's sort carries on from the first. IDs are labels, never positions.
        rows = [{"episode_id": episode_id, "type": UNSEEN, "sort": sort, "name": "集", "name_cn": None}
                for episode_id, sort in ((7027, 27), (519, 26), (530, 28))]
        pages = {"data": rows, "total": 3, "complete": True}
        with patch.object(self.site, "get_all_my_episode_states", return_value=pages):
            plan = self.preview().create_up_to(100, 7027)
        self.assertEqual({item["episode_id"] for item in plan["items"]}, {519, 7027})

    def test_preview_shows_the_season_number_but_selects_by_sort(self):
        # Attack on Titan season 2: sort carries on from season 1, ep restarts.
        rows = [{"episode_id": 500 + sort, "type": UNSEEN, "sort": sort, "ep": sort - 25, "name": "集", "name_cn": None}
                for sort in (26, 27, 28)]
        pages = {"data": rows, "total": 3, "complete": True}
        with patch.object(self.site, "get_all_my_episode_states", return_value=pages):
            plan = self.preview().create_up_to(100, 527)
        self.assertEqual([item["episode_sort"] for item in plan["items"]], ["1", "2"])
        self.assertIn("第1、2集", approval_message(plan))

    def test_fractional_main_episodes_are_selected_and_shown(self):
        # Real data: NANA (486) has recap episodes numbered 11.5, 21.5 and 36.5 as main episodes.
        rows = [{"episode_id": episode_id, "type": UNSEEN, "sort": sort, "ep": sort, "name": "集", "name_cn": None}
                for episode_id, sort in ((40222, 11), (40223, 11.5), (40224, 12), (40225, 13))]
        pages = {"data": rows, "total": 4, "complete": True}
        with patch.object(self.site, "get_all_my_episode_states", return_value=pages):
            plan = self.preview().create_up_to(100, 40224)
        self.assertEqual([item["episode_id"] for item in plan["items"]], [40222, 40223, 40224])
        self.assertIn("第11、11.5、12集", approval_message(plan))

    def test_only_main_episodes_are_read(self):
        with patch.object(self.site, "get_all_my_episode_states", wraps=self.site.get_all_my_episode_states) as read:
            self.preview().create_up_to(100, 9005)
        self.assertEqual(read.call_args.kwargs["episode_type"], 0)

    def test_refuses_and_saves_nothing(self):
        cases = {
            "unfinished pages": (lambda: setattr(self.site, "complete", False), 9005, "没有读完"),
            "not a main episode": (lambda: None, 9999, "本篇"),
            "nothing to change": (lambda: self.site.states.update({9002: WATCHED, 9003: DROPPED}), 9003, "无需"),
        }
        for name, (prepare, episode_id, message) in cases.items():
            with self.subTest(name):
                self.setUp()
                prepare()
                with self.assertRaisesRegex(PlanError, message):
                    self.preview().create_up_to(100, episode_id)
                self.assertFalse(self.path.exists())

    def test_popup_shows_the_count_previous_states_and_skips(self):
        message = approval_message(self.preview().create_up_to(100, 9005))
        for text in ("测试作品", "3集标为看过", "原为未收藏：第2、5集", "原为想看：第3集",
                     "跳过（已看过）：第1集", "跳过（已抛弃）：第4集"):
            self.assertIn(text, message)


class BatchExecutionTest(BatchTestCase):
    def test_all_applied_is_verified_in_one_request(self):
        writer = self.writer()
        service = self.service(writer)
        plan = service.execute(self.plan_up_to(5, service))
        self.assertEqual(plan["status"], "verified")
        self.assertEqual(writer.batches, [[9002, 9003, 9005]])
        self.assertEqual(self.site.states[9004], DROPPED)

    def test_repeated_execute_sends_nothing_new(self):
        writer = self.writer()
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        service.execute(plan_id)
        with self.assertRaises(PlanError):
            service.execute(plan_id)
        self.assertEqual(writer.calls, 1)

    def test_lost_response_is_settled_by_a_read_only_check(self):
        writer = self.writer("lost_response")
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        self.assertEqual(service.execute(plan_id)["status"], "unknown")
        self.assertEqual(service.check(plan_id)["status"], "verified")
        self.assertEqual(writer.calls, 1)

    def test_rejected_batch_is_failed_and_not_retried(self):
        writer = self.writer("rejected")
        service = self.service(writer)
        self.assertEqual(service.execute(self.plan_up_to(5, service))["status"], "failed")
        self.assertEqual(writer.calls, 1)

    def test_site_changed_before_execution_rejects_the_whole_plan(self):
        writer = self.writer()
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        self.site.states[9003] = DROPPED
        with self.assertRaisesRegex(PlanError, "已拒绝"):
            service.execute(plan_id)
        self.assertEqual(writer.calls, 0)
        self.assertEqual(self.store.get_plan(plan_id, account_id=42)["status"], "rejected")

    def test_failed_read_back_stays_unknown(self):
        def write_then_lose_reads(episode_id):
            self.site.apply_write(episode_id)
            self.site.fail_reads = True
        service = self.service(SimulatedWriter(on_write=write_then_lose_reads))
        self.assertEqual(service.execute(self.plan_up_to(5, service))["status"], "unknown")

    # S3-07 practice: the user's partial-success tests go in the class below.


class PartialSuccessTest(BatchTestCase):
    """The server answers OK but the read-back shows only some episodes changed.

    self.writer(never_applies=[9003]) never applies 9003; first_try_misses=[9003] misses it
    only on the first request. self.results(plan_id) gives each episode's result by number.
    """

    def test_first_miss_is_retried_and_verified(self):
        writer = self.writer(first_try_misses=[9003])
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        self.assertEqual(service.execute(plan_id)["status"], "verified")
        self.assertEqual(writer.batches, [[9002, 9003, 9005], [9003]]) 

    def test_never_applies(self):
        writer = self.writer(never_applies=[9003])
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        plan = service.execute(plan_id)
        self.assertEqual(plan["status"], "partial")
        self.assertEqual(self.results(plan_id), {2: "applied", 3: "not_applied", 5: "applied"})
        self.assertEqual(writer.batches, [[9002, 9003, 9005], [9003]])

    def test_apply_but_drop_one(self):
        def apply_but_drop_one(episode_id):
            self.site.states[episode_id] = DROPPED if episode_id == 9003 else WATCHED
        writer = SimulatedWriter(on_write=apply_but_drop_one)
        service = self.service(writer)
        plan_id = self.plan_up_to(5, service)
        plan = service.execute(plan_id)
        self.assertEqual(plan["status"], "partial")
        self.assertEqual(self.results(plan_id)[3], "changed_elsewhere")
        self.assertEqual(writer.batches, [[9002, 9003, 9005]])

class MigrationTest(unittest.TestCase):
    def test_version_1_plan_survives_and_new_tables_allow_several_episodes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(SCHEMA_V1)
            store = PlanStore(path)
            old = store.get_plan("old", account_id=42)
            self.assertEqual((old["status"], old["detail"], old["episode_id"]), ("verified", "读回为看过", 9001))
            self.assertEqual(old["items"][0]["result"], None)
            new_id = store.create_many(account_id=42, subject_id=100, subject_name="作品", created_at=1, expires_at=2,
                                       items=[{"episode_id": n, "episode_name": "", "episode_sort": n, "before_type": 0}
                                              for n in (1, 2)])
            self.assertEqual(len(store.get_plan(new_id, account_id=42)["items"]), 2)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_every_older_version_has_a_migration(self):
        self.assertEqual(set(MIGRATIONS), set(range(SCHEMA_VERSION)))


class NoContent(io.BytesIO):
    status = 204


class BatchWriterTest(unittest.TestCase):
    def test_sends_one_patch_with_all_episode_ids(self):
        with patch("bangumi_agent.episode_writer.build_opener") as opener:
            opener.return_value.open.return_value = NoContent()
            BangumiEpisodeWriter(Settings(access_token="offline-fixture")).mark_episodes_watched(100, [9002, 9003])
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.full_url, "https://api.bgm.tv/v0/users/-/collections/100/episodes")
        self.assertEqual(json.loads(request.data), {"episode_id": [9002, 9003], "type": 2})


if __name__ == "__main__":
    unittest.main()
