"""Approve, cancel, execute and reconcile one saved plan.

Approval is only ever recorded by a human-facing entry point (CLI or app button); there is
deliberately no MCP tool for it, so a model cannot approve its own plan. Execution trusts
only the status stored in SQLite, never a "confirmed" flag passed in by a caller.
"""
import time
from typing import Any, Protocol

from .account_client import AccountReadError
from .storage import PlanError, PlanStore


class WriteRejected(Exception):
    """The site clearly refused the write (4xx): nothing was changed."""


class WriteUncertain(Exception):
    """5xx, timeout or lost response: the write may or may not have happened."""


class EpisodeWriter(Protocol):
    def mark_episode_watched(self, subject_id: int, episode_id: int) -> None: ...


class AccountReader(Protocol):
    def get_me(self) -> dict[str, Any]: ...
    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]: ...


class SimulatedWriter:
    """Offline stand-in for the real Bangumi write; S3-04 replaces it.

    outcome: "success" | "rejected" | "server_error" | "lost_response".
    on_write is called when the simulated site applies the change, so a fake reader
    can show the new state on read-back.
    """

    def __init__(self, outcome="success", on_write=None):
        self.outcome, self.on_write, self.calls = outcome, on_write, 0

    def mark_episode_watched(self, subject_id: int, episode_id: int) -> None:
        self.calls += 1
        if self.outcome == "rejected":
            raise WriteRejected("模拟：服务器拒绝（4xx）")
        if self.outcome == "server_error":
            raise WriteUncertain("模拟：服务器错误（5xx），是否已修改未知")
        if self.on_write:
            self.on_write(episode_id)
        if self.outcome == "lost_response":
            raise WriteUncertain("模拟：修改已生效但回复丢失")


class ExecutionService:
    def __init__(self, reader: AccountReader, store: PlanStore, writer: EpisodeWriter,
                 clock=time.time):
        self.reader, self.store, self.writer, self.clock = reader, store, writer, clock

    def _load(self, plan_id) -> tuple[int, dict[str, Any], int]:
        account_id = self.reader.get_me()["id"]
        return account_id, self.store.get_plan(plan_id, account_id=account_id), int(self.clock())

    def _move(self, plan, account_id, to_status, now, detail="") -> bool:
        return self.store.transition(plan["plan_id"], account_id=account_id,
                                     from_status=plan["status"], to_status=to_status,
                                     now=now, detail=detail)

    def _expire_if_due(self, plan, account_id, now) -> None:
        if now >= plan["expires_at"]:
            self._move(plan, account_id, "expired", now, "到期未执行")
            raise PlanError("计划已过期；请重新读取网站状态并创建新草稿")

    def approve(self, plan_id) -> dict[str, Any]:
        account_id, plan, now = self._load(plan_id)
        if plan["status"] != "draft":
            raise PlanError(f"只有草稿可以批准，当前状态：{plan['status']}")
        self._expire_if_due(plan, account_id, now)
        if not self._move(plan, account_id, "approved", now):
            raise PlanError("计划状态已被其他操作改变，请重新查看")
        return self.store.get_plan(plan_id, account_id=account_id)

    def cancel(self, plan_id) -> dict[str, Any]:
        account_id, plan, now = self._load(plan_id)
        if plan["status"] not in ("draft", "approved"):
            raise PlanError(f"已开始执行或已结束的计划不能取消，当前状态：{plan['status']}")
        if not self._move(plan, account_id, "cancelled", now, "用户取消"):
            raise PlanError("计划状态已被其他操作改变，请重新查看")
        return self.store.get_plan(plan_id, account_id=account_id)

    def execute(self, plan_id) -> dict[str, Any]:
        account_id, plan, now = self._load(plan_id)
        if plan["status"] != "approved":
            raise PlanError(f"只有已批准的计划可以执行，当前状态：{plan['status']}")
        self._expire_if_due(plan, account_id, now)
        # Re-read the site just before writing; the draft's snapshot may be stale.
        current = self.reader.get_my_episode_state(plan["episode_id"])["type"]
        if current != plan["before_type"]:
            self._move(plan, account_id, "rejected", now,
                       f"执行前网站状态为{current}，与草稿记录的{plan['before_type']}不一致")
            raise PlanError("网站上的章节状态已变化，已拒绝执行；请重新创建草稿")
        # Compare-and-set approved -> executing: a second caller loses here and never writes.
        if not self._move(plan, account_id, "executing", now):
            raise PlanError("该计划已在执行或已被处理，不会重复执行")
        plan["status"] = "executing"
        try:
            self.writer.mark_episode_watched(plan["subject_id"], plan["episode_id"])
        except WriteRejected as exc:
            self._move(plan, account_id, "failed", int(self.clock()), str(exc))
        except WriteUncertain as exc:
            self._move(plan, account_id, "unknown", int(self.clock()), str(exc))
        else:
            self._settle(plan, account_id, "服务器回复成功")
        return self.store.get_plan(plan_id, account_id=account_id)

    def check(self, plan_id) -> dict[str, Any]:
        """Read-only reconciliation for unknown plans, or ones left executing by a crash."""
        account_id, plan, now = self._load(plan_id)
        if plan["status"] == "executing":
            # Nothing is mid-flight after a restart; treat it as unknown and never resend.
            self._move(plan, account_id, "unknown", now, "发现中断的执行，转为只读核对")
            plan["status"] = "unknown"
        if plan["status"] != "unknown":
            raise PlanError(f"只有结果未知的计划需要核对，当前状态：{plan['status']}")
        self._settle(plan, account_id, "只读核对")
        return self.store.get_plan(plan_id, account_id=account_id)

    def _settle(self, plan, account_id, reason) -> None:
        """Decide verified/failed from a read-back; stay unknown if the read itself fails."""
        try:
            current = self.reader.get_my_episode_state(plan["episode_id"])["type"]
        except AccountReadError as exc:
            if plan["status"] == "executing":
                self._move(plan, account_id, "unknown", int(self.clock()), f"读回失败：{exc}")
            return
        now = int(self.clock())
        if current == plan["after_type"]:
            self._move(plan, account_id, "verified", now, f"{reason}，读回为看过")
        elif plan["status"] == "executing":
            # The server said OK but the read-back disagrees: do not call it failed,
            # or a retry could write twice. Leave it for a later check.
            self._move(plan, account_id, "unknown", now, f"{reason}，但读回为状态{current}")
        elif current == plan["before_type"]:
            self._move(plan, account_id, "failed", now, f"{reason}，读回仍为修改前状态")
        # Any other state during a check: someone changed it by hand; stay unknown.
