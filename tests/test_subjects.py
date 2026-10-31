import unittest
from unittest.mock import patch

from bangumi_agent.api_client import search_subjects

class SubjectTest(unittest.TestCase):
	def test_two_items(self):
		items = {"total": 2, 
		          "data":[{"id": 10},{"id": 20}]}

		with patch(
			"bangumi_agent.api_client._request_json",
			return_value = items
			):
			result  = search_subjects(
				"k", 10, 0
			)
		self.assertEqual(result["total"], 2)
		self.assertEqual(
			[item["id"] for item in result["data"]],
			[10, 20],
		)

	def test_zero_items(self):
		items = {"total":0,
		         "data":[]}
		with patch(
			"bangumi_agent.api_client._request_json",
			return_value = items
			):
			result = search_subjects("k",10,0)
		self.assertEqual(result["total"], 0)
		self.assertEqual(
					[item["id"] for item in result["data"]],
					[],
				)

if __name__ == "__main__":
    unittest.main()