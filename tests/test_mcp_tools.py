"""Exercise the two registered tools through the MCP client, without network."""

import asyncio
import json
import sys
import unittest
from unittest.mock import patch

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from bangumi_agent.api_client import BangumiApiError
from bangumi_agent.mcp_server import mcp


class McpToolsTest(unittest.TestCase):
    def test_tool_discovery_and_calls(self):
        async def exercise():
            async with Client(mcp) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, {"search_subjects", "get_subject", "preview_episode_watched", "execute_episode_plan",
    "get_episodes", "get_calendar", "get_subject_characters", "get_subject_persons", "get_subject_relations",
    "get_my_collections", "get_my_collection", "get_my_episode_states"})
                search_schema = next(tool.input_schema for tool in tools if tool.name == "search_subjects")#next: find the first suitable result
                self.assertIn("keyword", search_schema["required"])

                with patch("bangumi_agent.api_client._request_json", return_value={
                    "total": 1,
                    "data": [{"id": 253, "name": "カウボーイビバップ", "name_cn": "星际牛仔", "type": 2}],
                }) as request:
                    found = await client.call_tool("search_subjects", {"keyword": "星际牛仔"})
                    self.assertFalse(found.is_error)

                    # 确认列表不是空的
                    self.assertTrue(found.content)

                    # 取第一块内容，确认它确实是文字
                    content = found.content[0]
                    assert isinstance(content, TextContent)

                    # 文字里的JSON转换为字典
                    result = json.loads(content.text)

                    # 检查第一个作品的ID
                    self.assertEqual(result["data"][0]["id"], 253)
                    self.assertEqual(request.call_args.args[0], "/v0/search/subjects?limit=10&offset=0")
                    self.assertEqual(request.call_args.kwargs["body"], {"keyword": "星际牛仔"})

                with patch("bangumi_agent.api_client._request_json", return_value={
                    "id": 253, "name": "カウボーイビバップ", "name_cn": "星际牛仔"
                }) as request:
                    subject = await client.call_tool("get_subject", {"subject_id": 253})
                    self.assertFalse(subject.is_error)
                    # 确认列表不是空的
                    self.assertTrue(subject.content)

                    # 取第一块内容，确认它确实是文字
                    content = subject.content[0]
                    assert isinstance(content, TextContent)

                    # 文字里的JSON转换为字典
                    result = json.loads(content.text)

                    # 检查第一个作品的ID
                    self.assertEqual(result["id"], 253)
                    #self.assertEqual(json.loads(subject.content[0].text)["id"], 253)
                    request.assert_called_once_with("/v0/subjects/253")

                with patch("bangumi_agent.api_client._request_json") as request:
                    invalid = await client.call_tool("get_subject", {"subject_id": 0})
                    self.assertTrue(invalid.is_error)
                    request.assert_not_called()

                with patch("bangumi_agent.api_client._request_json", side_effect=BangumiApiError("Bangumi 返回 HTTP 404")):
                    missing = await client.call_tool("get_subject", {"subject_id": 999999999})
                    self.assertTrue(missing.is_error)
                    self.assertTrue(missing.content)
                    content = missing.content[0]
                    assert isinstance(content, TextContent)
                    
                    self.assertIn("404", content.text)
                    

        asyncio.run(asyncio.wait_for(exercise(), timeout=10))

    def test_stdio_startup_and_invalid_call(self):
        async def exercise():
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "bangumi_agent.mcp_server"],
                encoding="utf-8",
            )
            async with Client(server) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, {"search_subjects", "get_subject", "preview_episode_watched", "execute_episode_plan",
    "get_episodes", "get_calendar", "get_subject_characters", "get_subject_persons", "get_subject_relations",
    "get_my_collections", "get_my_collection", "get_my_episode_states"})
                result = await client.call_tool("get_subject", {"subject_id": 0})
                self.assertTrue(result.is_error)

        asyncio.run(asyncio.wait_for(exercise(), timeout=15))


if __name__ == "__main__":
    unittest.main()
