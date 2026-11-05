"""Offline S3-05 read tools: every HTTP call is mocked; fake credentials only."""
import asyncio
import json
import unittest
from unittest.mock import patch

from mcp import Client
from mcp.types import TextContent
from bangumi_agent import api_client
from bangumi_agent.account_client import AccountClient
from bangumi_agent.mcp_server import mcp
from bangumi_agent.settings import Settings

TOKEN = "offline-read-tools-fixture"
EPISODE = {"id": 9001, "subject_id": 100, "sort": 5, "ep": 5, "type": 0, "name": "選択", "name_cn": "选择",
           "airdate": "2026-02-01", "duration": "00:23:40", "desc": "很长的单集简介", "comment": 3}


def calendar_day(day_id, subject_id):
    return {"weekday": {"id": day_id, "cn": f"星期{day_id}"}, "items": [{
        "id": subject_id, "name": "作品", "name_cn": "作品", "air_date": "2026-10-05",
        "summary": "不返回", "rating": {"score": 7.5, "count": {"10": 1}},
    }]}


class PublicReadTest(unittest.TestCase):
    def test_episodes_drop_unneeded_fields_and_summary_is_opt_in(self):
        with patch.object(api_client, "_request_json", return_value={"total": 3, "data": [EPISODE]}) as request:
            plain = api_client.list_episodes(100, limit=1)
            detailed = api_client.list_episodes(100, limit=1, include_summary=True)
        self.assertEqual((plain["data"][0]["episode_id"], plain["data"][0]["episode_type"]), (9001, 0))
        self.assertNotIn("summary", plain["data"][0])
        self.assertNotIn("comment", plain["data"][0])
        self.assertEqual(plain["next_offset"], 1)
        self.assertEqual(detailed["data"][0]["summary"], "很长的单集简介")
        # Without an episode_type the default is main episodes, so "episode 5" means the show's 5th.
        self.assertIn("type=0", request.call_args.args[0])

    def test_calendar_returns_only_the_requested_day(self):
        week = [calendar_day(1, 11), calendar_day(3, 33)]
        with patch.object(api_client, "_request_list", return_value=week), \
             patch.object(api_client, "_today_weekday", return_value=3):
            self.assertEqual(api_client.get_calendar(1)["data"][0]["subject_id"], 11)
            today = api_client.get_calendar()
        self.assertEqual((today["weekday"], today["data"][0]["subject_id"]), (3, 33))
        self.assertEqual(today["data"][0]["score"], 7.5)
        self.assertNotIn("summary", today["data"][0])

    def test_invalid_arguments_make_no_request(self):
        cases = [(api_client.get_calendar, (8,)), (api_client.get_subject_characters, (0,)),
                 (api_client.get_subject_persons, (True,)), (api_client.get_subject_relations, (-1,))]
        with patch.object(api_client, "_request_list") as request:
            for read, args in cases:
                with self.subTest(read.__name__), self.assertRaises(ValueError):
                    read(*args)
        request.assert_not_called()

    def test_long_lists_come_back_one_page_at_a_time(self):
        rows = [{"id": n, "name": f"人{n}", "relation": "脚本" if n % 2 else "原画", "eps": ""} for n in range(120)]
        with patch.object(api_client, "_request_list", return_value=rows):
            first = api_client.get_subject_persons(100)
            last = api_client.get_subject_persons(100, limit=50, offset=100)
            writers = api_client.get_subject_persons(100, relation="脚本")
            with self.assertRaises(ValueError):
                api_client.get_subject_persons(100, limit=51)
        self.assertEqual((first["total"], len(first["data"]), first["next_offset"]), (120, 30, 30))
        self.assertEqual((len(last["data"]), last["next_offset"]), (20, None))
        self.assertEqual(writers["total"], 60)

    def test_characters_keep_actor_id_and_name_only(self):
        rows = [{"id": 1, "name": "角色", "relation": "主角", "summary": "不返回",
                 "actors": [{"id": 2, "name": "声优", "short_summary": "不返回"}]}]
        with patch.object(api_client, "_request_list", return_value=rows):
            data = api_client.get_subject_characters(100)["data"]
        self.assertEqual(data, [{"character_id": 1, "name": "角色", "relation": "主角",
                                 "actors": [{"person_id": 2, "name": "声优"}]}])

    def test_list_endpoint_rejects_an_unexpected_shape(self):
        with patch.object(api_client, "_request_any", return_value={"data": []}), \
             self.assertRaises(api_client.BangumiApiError):
            api_client.get_subject_relations(100)


class AccountReadTest(unittest.TestCase):
    ME = {"id": 42, "username": "fixture-user"}

    def client(self, page) -> AccountClient:
        client = AccountClient(Settings(access_token=TOKEN))
        self.addCleanup(patch.stopall)
        patch.object(client, "_get", side_effect=[self.ME, page]).start()
        return client

    def test_one_collection_includes_progress_and_comment(self):
        row = {"subject_id": 253, "type": 3, "ep_status": 5, "comment": "我的短评", "rate": 8,
               "tags": ["科幻"], "private": True, "subject": {"id": 253, "name": "x", "summary": "不返回"}}
        result = self.client(row).get_my_collection(253)
        self.assertEqual((result["ep_status"], result["comment"], result["private"]), (5, "我的短评", True))
        self.assertNotIn("subject", result)

    def test_comment_and_private_mark_only_when_asked(self):
        page = {"total": 1, "limit": 20, "offset": 0, "data": [{
            "subject_id": 253, "subject_type": 2, "type": 3, "ep_status": 5, "subject": {"id": 253, "name": "x"},
            "comment": "我的短评", "rate": 8, "tags": ["科幻"], "private": True,
        }]}
        plain = self.client(page).get_my_collections()["data"][0]
        for key in ("comment", "rate", "tags", "private"):
            self.assertNotIn(key, plain)
        full = self.client(page).get_my_collections(include_comment=True)["data"][0]
        self.assertEqual((full["comment"], full["private"]), ("我的短评", True))

    def test_episode_states_filter_and_summary(self):
        page = {"total": 1, "limit": 40, "offset": 0, "data": [{"episode": EPISODE, "type": 2}]}
        client = self.client(page)
        row = client.get_my_episode_states(100, episode_type=0, include_summary=True)["data"][0]
        self.assertEqual((row["episode_id"], row["type"], row["summary"]), (9001, 2, "很长的单集简介"))
        # type is the user's watch state; episode_type says whether this is a main episode, OP, ED...
        self.assertEqual(row["episode_type"], 0)
        self.assertIn("episode_type=0", client._get.call_args.args[0])  # pyright: ignore[reportAttributeAccessIssue] patched with a mock
        self.assertNotIn("summary", self.client(page).get_my_episode_states(100)["data"][0])


class McpReadToolsTest(unittest.TestCase):
    def call(self, name, arguments):
        async def run():
            async with Client(mcp) as client:
                return await client.call_tool(name, arguments)
        return asyncio.run(run())

    def test_every_tool_parameter_is_described_for_the_model(self):
        async def tools():
            async with Client(mcp) as client:
                return (await client.list_tools()).tools
        for tool in asyncio.run(tools()):
            for name, schema in tool.input_schema["properties"].items():
                with self.subTest(tool=tool.name, parameter=name):
                    self.assertTrue(schema.get("description"))

    def test_out_of_range_argument_is_refused_before_any_request(self):
        with patch.object(api_client, "_request_list") as request:
            self.assertTrue(self.call("get_subject_persons", {"subject_id": 100, "limit": 51}).is_error)
        request.assert_not_called()

    def test_public_tool_returns_projection_and_reports_api_failure(self):
        with patch.object(api_client, "_request_json", return_value={"total": 1, "data": [EPISODE]}):
            result = self.call("get_episodes", {"subject_id": 100})
        self.assertFalse(result.is_error)
        content = result.content[0]
        assert isinstance(content, TextContent)
        self.assertEqual(json.loads(content.text)["data"][0]["episode_id"], 9001)
        with patch.object(api_client, "_request_list", side_effect=api_client.BangumiApiError("Bangumi 返回 HTTP 500")):
            self.assertTrue(self.call("get_subject_relations", {"subject_id": 100}).is_error)

    def test_account_tool_failure_hides_the_raw_exception(self):
        with patch("bangumi_agent.mcp_server._account_client", side_effect=RuntimeError(TOKEN)):
            result = self.call("get_my_collections", {})
        self.assertTrue(result.is_error)
        content = result.content[0]
        assert isinstance(content, TextContent)
        self.assertNotIn(TOKEN, content.text)


if __name__ == "__main__":
    unittest.main()
