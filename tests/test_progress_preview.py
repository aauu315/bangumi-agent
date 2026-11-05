"""Offline preview acceptance: fake token, mocked HTTP, temporary SQLite only."""
import asyncio
import io
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.types import TextContent
from bangumi_agent.account_client import AccountClient, AccountReadError
from bangumi_agent.settings import Settings
from bangumi_agent.storage import PlanStore, PlanError
from bangumi_agent.progress import ProgressPreview
from bangumi_agent.mcp_server import mcp

TOKEN = "offline-preview-fixture"


class Response(io.BytesIO):
    status = 200


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "progress.sqlite3"
        self.store = PlanStore(self.path)
        self.client = AccountClient(Settings(access_token=TOKEN))
        self.now = 1000
        self.service = ProgressPreview(self.client, self.store, lambda: self.now)
        self.payloads = [
            {"id": 42, "username": "fixture-user"},
            {"subject_id": 100, "type": 3, "subject": {"id": 100, "name": "测试作品"}},
            {"episode": {"id": 9001, "subject_id": 100, "sort": 4, "name": "小雪"}, "type": 0},
        ]

    def create(self):
        def respond(request, **kwargs):
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            return Response(json.dumps(self.payloads.pop(0)).encode())
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.side_effect = respond
            plan = self.service.create(100, 9001)
            self.assertEqual(opener.return_value.open.call_count, 3)
        return plan

    def test_get_only_snapshot_persistence_and_no_credentials(self):
        plan = self.create()
        self.assertEqual((plan["before_type"], plan["after_type"], plan["episode_id"]), (0, 2, 9001))
        self.assertEqual(plan["expires_at"], 1900)
        # A fresh interpreter reads the actual SQLite file, not an in-memory cache.
        code = ("import json,sys; from bangumi_agent.storage import PlanStore; "
                "print(json.dumps(PlanStore(sys.argv[1]).get_preview(sys.argv[2],account_id=42,now=1001)))")
        result = subprocess.run([sys.executable, "-c", code, str(self.path), plan["plan_id"]],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["episode_id"], 9001)
        self.assertNotIn(TOKEN.encode(), self.path.read_bytes())

    def test_wrong_subject_rejected_before_save(self):
        self.payloads[2]["episode"]["subject_id"] = 101
        with self.assertRaisesRegex(PlanError, "不属于"):
            self.create()
        self.assertFalse(self.path.exists())

    def test_uncollected_subject_rejected_before_save(self):
        with patch.object(self.client, "_get", side_effect=[self.payloads[0], AccountReadError("未收藏", 404)]) as get:
            with self.assertRaises(AccountReadError):
                self.service.create(100, 9001)
            self.assertEqual(get.call_count, 2)
        self.assertFalse(self.path.exists())

    def test_expired_and_other_account_rejected_but_record_retained(self):
        plan = self.create()
        with self.assertRaisesRegex(PlanError, "过期"):
            self.store.get_preview(plan["plan_id"], account_id=42, now=1900)
        with self.assertRaisesRegex(PlanError, "账号"):
            self.store.get_preview(plan["plan_id"], account_id=43, now=1000)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operation_plans").fetchone()[0], 1)

    def test_already_watched_noop_rejected(self):
        self.payloads[2]["type"] = 2
        with self.assertRaisesRegex(PlanError, "已是看过"):
            self.create()
        self.assertFalse(self.path.exists())

    def test_discarded_subject_rejected(self):
        self.payloads[1]["type"] = 5
        with self.assertRaisesRegex(PlanError, "目标条目已抛弃"):
            self.create()
        self.assertFalse(self.path.exists())

    def test_invalid_arguments_do_not_read_account(self):
        with patch.object(self.client, "get_me") as get:
            for args in [(True, 9001, 900), (100, 0, 900), (100, 9001, 3601)]:
                with self.assertRaises(ValueError):
                    self.service.create(*args)
            get.assert_not_called()

    def test_invalid_episode_identity_and_state_rejected(self):
        for update in ({"id": 99}, {"subject_id": None}):
            row = {"episode": {**self.payloads[2]["episode"], **update}, "type": 0}
            with patch.object(self.client, "_get", side_effect=[self.payloads[0], row]):
                with self.assertRaises(AccountReadError):
                    self.client.get_my_episode_state(9001)
        with patch.object(self.client, "_get", return_value={**self.payloads[2], "type": True}):
            with self.assertRaises(AccountReadError):
                self.client.get_my_episode_state(9001)

    def test_transaction_rolls_back_plan_when_item_invalid(self):
        self.create()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create(account_id=42, subject_id=100, episode_id=9002,
                              subject_name="测试", episode_name="测试", episode_sort=5,
                              before_type=2, created_at=1000, expires_at=1900)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operation_plans").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM plan_items").fetchone()[0], 1)

    def test_sql_values_are_bound_and_foreign_keys_enabled(self):
        self.payloads[2]["episode"]["name"] = "'); DROP TABLE operation_plans; --"
        plan = self.create()
        self.assertIn("DROP TABLE", plan["episode_name"])
        with self.store._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO plan_items VALUES ('missing',1,2,'','','4',0,2,NULL)")
        connection.close()

    def test_mcp_preview_uses_fake_credentials_and_temporary_store(self):
        async def exercise():
            with patch("bangumi_agent.settings.Settings.from_token_file", return_value=Settings(access_token=TOKEN)), \
                 patch("bangumi_agent.storage.PlanStore", return_value=self.store), \
                 patch("bangumi_agent.account_client.AccountClient._get", side_effect=self.payloads):
                async with Client(mcp) as client:
                    result = await client.call_tool("preview_episode_watched", {"subject_id": 100, "episode_id": 9001})
                    self.assertFalse(result.is_error)
                    content = result.content[0]
                    assert isinstance(content, TextContent)
                    plan = json.loads(content.text)
                    self.assertFalse(plan["remote_write_performed"])
                    self.assertEqual(plan["episode_id"], 9001)
        asyncio.run(asyncio.wait_for(exercise(), 10))

    def test_cli_create_then_show_without_reading_real_token_file(self):
        script = Path(__file__).resolve().parents[1] / "scripts/progress_preview.py"
        spec = importlib.util.spec_from_file_location("preview_cli_fixture", script)
        assert spec is not None and spec.loader is not None
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        output = io.StringIO()
        with patch.object(cli.Settings, "from_token_file", return_value=Settings(access_token=TOKEN)) as credentials, \
             patch.object(AccountClient, "_get", side_effect=self.payloads), \
             patch.object(sys, "argv", [str(script), "--db", str(self.path), "create", "--subject-id", "100", "--episode-id", "9001"]), \
             redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
            credentials.assert_called_once()
        with closing(sqlite3.connect(self.path)) as connection:
            plan_id = connection.execute("SELECT plan_id FROM operation_plans").fetchone()[0]
        with patch.object(cli.Settings, "from_token_file", return_value=Settings(access_token=TOKEN)), \
             patch.object(AccountClient, "_get", return_value=self.payloads[0]) as get, \
             patch.object(sys, "argv", [str(script), "--db", str(self.path), "show", plan_id]), \
             redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
            get.assert_called_once_with("/v0/me")
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertIn("9001", output.getvalue())


if __name__ == "__main__":
    unittest.main()
