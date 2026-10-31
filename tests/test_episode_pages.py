import unittest
from unittest.mock import patch

from bangumi_agent.api_client import get_all_episodes, BangumiApiError


class EpisodePagesTest(unittest.TestCase):
    def test_two_pages(self):
        # 模拟第一页：共有3条，本页返回2条
        first_page = {
            "total": 3,
            "data": [{"id": 101}, {"id": 102}],
        }

        # 模拟第二页：返回剩下的1条
        second_page = {
            "total": 3,
            "data": [{"id": 103}],
        }

        with patch(
            "bangumi_agent.api_client.get_episodes",
            side_effect=[first_page, second_page],
        ) as query:
            result = get_all_episodes(
                253,
                episode_type=0,
                limit=2,
                max_pages=5,
            )

        self.assertEqual(
            result["data"],
            [{"id": 101}, {"id": 102}, {"id": 103}],
        )
        self.assertTrue(result["complete"])
        self.assertIsNone(result["next_offset"])
        self.assertEqual(result["stop_reason"], "completed")

        # 应只查两次，第二次从第3条开始，并保留类型筛选
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[1].kwargs["offset"], 2)
        self.assertEqual(query.call_args_list[1].kwargs["episode_type"], 0)

    def test_second_page_failure(self):
        first_page = {
            "total": 3,
            "data": [{"id": 101}, {"id": 102}],
            }
        second_page = BangumiApiError("模拟超时")

        with patch(
            "bangumi_agent.api_client.get_episodes",
            side_effect=[first_page, second_page],
        ) as query:
            result = get_all_episodes(
                253,
                episode_type=0,
                limit=2,
                max_pages=5,
            )

        self.assertFalse(result["complete"])
        self.assertEqual(result["next_offset"], 2)
        self.assertEqual(result["data"], [{"id": 101}, {"id": 102}])
        self.assertEqual(result["stop_reason"], "request_failed")
        self.assertEqual(result["error"], "模拟超时")
        self.assertEqual(query.call_count, 2)

    def test_empty_result(self):
        page = {"total": 0, "data": []}

        with patch(
            "bangumi_agent.api_client.get_episodes",
            return_value = page,
        ) as query:
            result = get_all_episodes(
                253,
                episode_type=0,
                limit=2,
                max_pages=5,
            )

        self.assertEqual(result["data"], [])
        self.assertEqual(result["next_offset"], None)
        self.assertTrue(result["complete"])
        self.assertEqual(query.call_count, 1)

    def test_max_page_failure(self):
            first_page = {
                "total": 3,
                "data": [{"id": 101}, {"id": 102}],
                }
            
    
            with patch(
                "bangumi_agent.api_client.get_episodes",
                return_value = first_page
            ) as query:
                result = get_all_episodes(
                    253,
                    episode_type=0,
                    limit=2,
                    max_pages=1,
                )
    
            self.assertFalse(result["complete"])
            self.assertEqual(result["next_offset"], 2)
            self.assertEqual(result["data"], [{"id": 101}, {"id": 102}])
            self.assertEqual(result["stop_reason"], "page_limit")
            self.assertEqual(query.call_count, 1)

if __name__ == "__main__":
    unittest.main()