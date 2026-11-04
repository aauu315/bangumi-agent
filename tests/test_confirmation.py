"""Offline S3-03/S3-04: fake site, simulated or mocked writer, temporary SQLite only."""
import asyncio
import io
import json
import traceback
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from email.message import Message

from mcp import Client
from mcp_types import ElicitResult
from bangumi_agent.account_client import AccountReadError
from bangumi_agent.confirmation import ExecutionService, SimulatedWriter, WriteRejected, WriteUncertain
from bangumi_agent.episode_writer import BangumiEpisodeWriter
from bangumi_agent.settings import Settings
from bangumi_agent.mcp_server import execute_episode_plan, mcp
from bangumi_agent.storage import PlanError, PlanStore, SCHEMA_VERSION

# The S3-02 layout, before status could be anything but 'draft'.
OLD_SCHEMA = """
CREATE TABLE operation_plans (
    plan_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL CHECK(account_id > 0),
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
    status TEXT NOT NULL CHECK(status = 'draft'));
CREATE TABLE plan_items (
    plan_id TEXT PRIMARY KEY REFERENCES operation_plans(plan_id),
    subject_id INTEGER NOT NULL CHECK(subject_id > 0), episode_id INTEGER NOT NULL CHECK(episode_id > 0),
    subject_name TEXT NOT NULL, episode_name TEXT NOT NULL, episode_sort TEXT NOT NULL,
    before_type INTEGER NOT NULL CHECK(before_type IN (0, 1, 3)),
    after_type INTEGER NOT NULL CHECK(after_type = 2));
INSERT INTO operation_plans VALUES ('old', 42, 100, 5000, 'draft');
INSERT INTO plan_items VALUES ('old', 100, 9001, '测试作品', '小雪', '4', 0, 2);
"""


class FakeSite:
    """Reads for one account and one episode. A writer's on_write updates the state."""

    def __init__(self, state=0):
        self.state, self.reads, self.fail_reads = state, 0, False

    def get_me(self) -> dict[str, Any]:
        return {"id": 42, "username": "fixture-user", "nickname": ""}

    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]:
        self.reads += 1
        if self.fail_reads:
            raise AccountReadError("模拟：读回超时")
        return {"episode_id": episode_id, "subject_id": 100, "type": self.state, "sort": 4, "name": "小雪"}

    def apply_write(self, episode_id: int) -> None:
        self.state = 2


class ConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "progress.sqlite3"
        self.store = PlanStore(self.path)
        self.site = FakeSite()
        self.now = 1000

    def service(self, outcome="success") -> tuple[ExecutionService, SimulatedWriter]:
        writer = SimulatedWriter(outcome, on_write=self.site.apply_write)
        return ExecutionService(self.site, self.store, writer, lambda: self.now), writer

    def draft(self) -> str:
        return self.store.create(account_id=42, subject_id=100, episode_id=9001, subject_name="测试作品",
                                 episode_name="小雪", episode_sort=4, before_type=0,
                                 created_at=1000, expires_at=1900)

    def status(self, plan_id) -> str:
        return self.store.get_plan(plan_id, account_id=42)["status"]

    def test_old_database_is_migrated_and_keeps_its_draft(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(OLD_SCHEMA)
        service, _ = self.service()
        self.assertEqual(service.approve("old")["status"], "approved")
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT count(*) FROM plan_items").fetchone()[0], 1)

    def test_only_listed_transitions_are_allowed(self):
        plan_id = self.draft()
        for source, target in (("draft", "executing"), ("draft", "verified"), ("verified", "failed")):
            with self.subTest(move=f"{source}->{target}"), self.assertRaises(PlanError):
                self.store.transition(plan_id, account_id=42, from_status=source, to_status=target, now=1000)
        self.assertEqual(self.status(plan_id), "draft")

    def test_execution_refused_without_valid_approval_makes_no_write(self):
        cases = {
            "not approved": ("draft", lambda s, p: None),
            "cancelled": ("cancelled", lambda s, p: s.cancel(p)),
            "cancelled after approval": ("cancelled", lambda s, p: (s.approve(p), s.cancel(p))),
        }
        for name, (expected, prepare) in cases.items():
            with self.subTest(name):
                service, writer = self.service()
                plan_id = self.draft()
                prepare(service, plan_id)
                with self.assertRaises(PlanError):
                    service.execute(plan_id)
                self.assertEqual((writer.calls, self.status(plan_id)), (0, expected))

    def test_expired_at_execution_makes_no_write(self):
        service, writer = self.service()
        plan_id = self.draft()
        service.approve(plan_id)
        self.now = 1900
        with self.assertRaisesRegex(PlanError, "过期"):
            service.execute(plan_id)
        self.assertEqual((writer.calls, self.status(plan_id)), (0, "expired"))

    def test_site_changed_since_draft_is_rejected_without_write(self):
        service, writer = self.service()
        plan_id = self.draft()
        service.approve(plan_id)
        self.site.state = 2  # marked watched on the website after the draft was made
        with self.assertRaisesRegex(PlanError, "已拒绝"):
            service.execute(plan_id)
        self.assertEqual((writer.calls, self.status(plan_id)), (0, "rejected"))

    def test_repeated_execute_writes_at_most_once(self):
        service, writer = self.service()
        plan_id = self.draft()
        service.approve(plan_id)
        self.assertEqual(service.execute(plan_id)["status"], "verified")
        with self.assertRaises(PlanError):
            service.execute(plan_id)
        self.assertEqual(writer.calls, 1)

    def test_lock_lets_only_one_caller_move_to_executing(self):
        plan_id = self.draft()
        self.service()[0].approve(plan_id)
        moves = [self.store.transition(plan_id, account_id=42, from_status="approved",
                                       to_status="executing", now=1000) for _ in range(2)]
        self.assertEqual(moves, [True, False])

    def test_write_outcomes(self):
        cases = {"rejected": ("failed", 0), "server_error": ("unknown", 0)}
        for outcome, (expected, site_state) in cases.items():
            with self.subTest(outcome):
                self.site.state = 0
                service, writer = self.service(outcome)
                plan_id = self.draft()
                service.approve(plan_id)
                self.assertEqual(service.execute(plan_id)["status"], expected)
                self.assertEqual((writer.calls, self.site.state), (1, site_state))

    def test_unknown_check_is_read_only_and_settles_failed_when_not_applied(self):
        service, writer = self.service("server_error")
        plan_id = self.draft()
        service.approve(plan_id)
        service.execute(plan_id)
        self.assertEqual(service.check(plan_id)["status"], "failed")
        self.assertEqual(writer.calls, 1)

    def test_unknown_check_is_read_only_and_verified_when_applied(self):
        service, writer = self.service("lost_response")
        plan_id = self.draft()
        service.approve(plan_id)
        service.execute(plan_id)
        self.assertEqual(self.status(plan_id), "unknown")
        self.assertEqual(service.check(plan_id)["status"], "verified")
        self.assertEqual(writer.calls, 1)

    def test_success_with_failed_read_back_stays_unknown(self):
        service, _ = self.service()
        plan_id = self.draft()
        service.approve(plan_id)
        original = self.site.apply_write

        def write_then_lose_reads(episode_id):
            original(episode_id)
            self.site.fail_reads = True
        service.writer = SimulatedWriter(on_write=write_then_lose_reads)
        self.assertEqual(service.execute(plan_id)["status"], "unknown")

    def test_restart_while_executing_is_checked_not_resent(self):
        service, writer = self.service()
        plan_id = self.draft()
        service.approve(plan_id)
        # Simulate a crash right after the write reached the site but before status was saved.
        self.store.transition(plan_id, account_id=42, from_status="approved", to_status="executing", now=1000)
        self.site.state = 2
        restarted, _ = self.service()
        self.assertEqual(restarted.check(plan_id)["status"], "verified")
        self.assertEqual(writer.calls, 0)

    def test_mcp_has_no_approval_tool_and_execution_takes_only_plan_id(self):
        async def tools():
            async with Client(mcp) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}
        found = asyncio.run(tools())
        self.assertFalse({name for name in found if "approve" in name})
        self.assertEqual(set(found[execute_episode_plan.__name__].input_schema["properties"]), {"plan_id"})


class McpApprovalTest(unittest.TestCase):
    """execute_episode_plan with a fake site; the callback plays the user answering the popup."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / "progress.sqlite3")
        self.site = FakeSite()
        self.writer = SimulatedWriter(on_write=self.site.apply_write)
        self.plan_id = self.store.create(account_id=42, subject_id=100, episode_id=9001, subject_name="测试作品",
                                         episode_name="小雪", episode_sort=4, before_type=0,
                                         created_at=1000, expires_at=1900)
        self.popups = []

    def status(self) -> str:
        return self.store.get_plan(self.plan_id, account_id=42)["status"]

    def answering(self, action, confirm=True):
        """A popup callback: records what the user saw and answers accept/decline/cancel."""
        async def callback(context, params):
            self.popups.append(params.message)
            return ElicitResult(action=action, content={"confirm": confirm} if action == "accept" else None)
        return callback

    def call(self, arguments, callback=None, mode="auto"):
        """Call the tool. callback=None is a client that does not support popups."""
        service = ExecutionService(self.site, self.store, self.writer, lambda: 1000)

        async def run():
            with patch("bangumi_agent.mcp_server._execution_service", return_value=service):
                async with Client(mcp, mode=mode, elicitation_callback=callback) as client:
                    return await client.call_tool(execute_episode_plan.__name__, arguments)
        return asyncio.run(run())

    def test_user_confirming_the_popup_writes_once_and_reads_back(self):
        result = self.call({"plan_id": self.plan_id}, self.answering("accept"))
        self.confirm_written(result)

    def test_popup_also_works_on_the_older_protocol(self):
        # Clients may negotiate the pre-2026-07-28 handshake, where the popup is a mid-call request.
        self.confirm_written(self.call({"plan_id": self.plan_id}, self.answering("accept"), mode="legacy"))

    def confirm_written(self, result):
        self.assertFalse(result.is_error)
        self.assertEqual((self.status(), self.writer.calls), ("verified", 1))
        # The popup itself names the concrete change, not just an opaque plan ID.
        for text in ("测试作品", "9001", "小雪", "未收藏 → 看过"):
            self.assertIn(text, self.popups[0])

    # S3-06 practice: the user's tests for model self-report and decline/cancel/no popup go here.
    def test_model_confirmed(self):
        self.call({"plan_id": self.plan_id,"confirmed": True}, self.answering("decline"))
        self.assertEqual(self.status(), "draft")
        self.assertEqual(self.writer.calls, 0)

    def test_unwritten_when_decline_cancel_and_nopop(self):
        for name, answer in (("cancel", self.answering("cancel")),
                     ("decline", self.answering("decline")),
                     ("no popup", None)):
            with self.subTest(name):
                self.setUp()
                result = self.call({"plan_id": self.plan_id}, answer)
                self.assertEqual(self.status(), "draft")
                self.assertEqual(self.writer.calls, 0)
                if answer is None:
                    self.assertTrue(result.is_error)
                else:
                    self.assertFalse(result.is_error)


TOKEN = "offline-writer-fixture"


class NoContent(io.BytesIO):
    status = 204


class EpisodeWriterTest(unittest.TestCase):
    def writer(self) -> BangumiEpisodeWriter:
        return BangumiEpisodeWriter(Settings(access_token=TOKEN))

    def test_sends_one_put_marking_the_episode_watched(self):
        with patch("bangumi_agent.episode_writer.build_opener") as opener:
            opener.return_value.open.return_value = NoContent()
            self.writer().mark_episode_watched(100, 9001)
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, "https://api.bgm.tv/v0/users/-/collections/-/episodes/9001")
        self.assertEqual(json.loads(request.data), {"type": 2})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)

    def test_errors_map_to_rejected_or_uncertain_without_leaking_token(self):
        cases = {400: WriteRejected, 401: WriteRejected, 404: WriteRejected,
                 500: WriteUncertain, 503: WriteUncertain, "network": WriteUncertain}
        for case, expected in cases.items():
            error = (URLError(TOKEN) if case == "network" else
                     HTTPError("https://api.bgm.tv", case, TOKEN, Message(), io.BytesIO(TOKEN.encode())))
            with self.subTest(case=case), patch("bangumi_agent.episode_writer.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(expected) as caught:
                    self.writer().mark_episode_watched(100, 9001)
                self.assertNotIn(TOKEN, "".join(traceback.format_exception(caught.exception)))


if __name__ == "__main__":
    unittest.main()
