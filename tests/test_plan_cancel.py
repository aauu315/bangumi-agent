"""S4-02 requirements for settle_plan_on_cancel. The plan service is a fake; nothing is written anywhere.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_plan_cancel
"""
import unittest
from typing import Any

from bangumi_agent.agent.plan_cancel import settle_plan_on_cancel
from bangumi_agent.storage import PlanError

PLAN_ID = "plan-1"


class FakePlans:
    """Holds one plan's status and remembers which methods were called.

    after_check: the status check() finds on the site.
    moved_on_to: if set, the plan changes to this status just before cancel() looks at it,
    as if another thread had started executing in between; cancel() then refuses.
    cancel_broken: cancel() refuses every time although the plan still reads as approved.
    """

    def __init__(self, status: str, after_check: str = "verified", moved_on_to: str | None = None,
                 cancel_broken: bool = False):
        self.status = status
        self.after_check = after_check
        self.moved_on_to = moved_on_to
        self.cancel_broken = cancel_broken
        self.calls: list[str] = []

    def _plan(self) -> dict[str, Any]:
        return {"plan_id": PLAN_ID, "status": self.status}

    def get(self, plan_id: str) -> dict[str, Any]:
        self.calls.append("get")
        return self._plan()

    def cancel(self, plan_id: str) -> dict[str, Any]:
        self.calls.append("cancel")
        if self.cancel_broken:
            raise PlanError("计划状态已被其他操作改变，请重新查看")
        if self.moved_on_to is not None:
            self.status, self.moved_on_to = self.moved_on_to, None
        if self.status not in ("draft", "approved"):
            raise PlanError(f"已开始执行或已结束的计划不能取消，当前状态：{self.status}")
        self.status = "cancelled"
        return self._plan()

    def check(self, plan_id: str) -> dict[str, Any]:
        self.calls.append("check")
        if self.status not in ("executing", "unknown"):
            raise PlanError(f"只有结果未知的计划需要核对，当前状态：{self.status}")
        self.status = self.after_check
        return self._plan()


class SettlePlanOnCancel(unittest.TestCase):
    def test_draft_is_left_alone(self):
        plans = FakePlans("draft")
        outcome = settle_plan_on_cancel(plans, PLAN_ID)

        self.assertEqual((outcome.action, outcome.plan["status"]), ("kept", "draft"))
        self.assertNotIn("cancel", plans.calls)
        self.assertNotIn("check", plans.calls)

    def test_approval_is_withdrawn(self):
        plans = FakePlans("approved")
        outcome = settle_plan_on_cancel(plans, PLAN_ID)

        self.assertEqual((outcome.action, outcome.plan["status"]), ("voided", "cancelled"))
        self.assertNotIn("check", plans.calls)

    def test_sent_write_is_checked_not_cancelled(self):
        # The check may find the write applied, not applied, or still be unable to tell.
        for status in ["executing", "unknown"]:
            for found in ["verified", "failed", "partial", "unknown"]:
                with self.subTest(status=status, found=found):
                    plans = FakePlans(status, after_check=found)
                    outcome = settle_plan_on_cancel(plans, PLAN_ID)

                    self.assertEqual((outcome.action, outcome.plan["status"]), ("checked", found))
                    self.assertEqual(plans.calls.count("check"), 1)
                    self.assertNotIn("cancel", plans.calls)

    def test_finished_plans_are_only_reported(self):
        for status in ["verified", "failed", "partial", "rejected", "expired", "cancelled"]:
            with self.subTest(status=status):
                plans = FakePlans(status)
                outcome = settle_plan_on_cancel(plans, PLAN_ID)

                self.assertEqual((outcome.action, outcome.plan["status"]), ("reported", status))
                self.assertNotIn("cancel", plans.calls)
                self.assertNotIn("check", plans.calls)

    def test_plan_that_started_executing_meanwhile_is_checked(self):
        # Read as approved, but execution began before cancel() got there: the write is out.
        plans = FakePlans("approved", moved_on_to="executing", after_check="verified")
        outcome = settle_plan_on_cancel(plans, PLAN_ID)

        self.assertEqual((outcome.action, outcome.plan["status"]), ("checked", "verified"))
        self.assertEqual(plans.calls.count("check"), 1)

    def test_plan_that_finished_meanwhile_is_reported(self):
        plans = FakePlans("approved", moved_on_to="verified")
        outcome = settle_plan_on_cancel(plans, PLAN_ID)

        self.assertEqual((outcome.action, outcome.plan["status"]), ("reported", "verified"))
        self.assertNotIn("check", plans.calls)

    def test_cancel_failing_twice_is_raised_not_guessed(self):
        plans = FakePlans("approved", cancel_broken=True)
        with self.assertRaises(PlanError):
            settle_plan_on_cancel(plans, PLAN_ID)

        self.assertEqual(plans.calls.count("cancel"), 2)  # one retry after re-reading, then give up
        self.assertNotIn("check", plans.calls)


if __name__ == "__main__":
    unittest.main()
