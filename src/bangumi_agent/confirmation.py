"""Approve, cancel, execute and reconcile one saved plan.

Approval only comes from the user: the CLI, or a client popup (MCP elicitation) whose answer
reaches the server without passing through the model. Execution trusts only the status stored
in SQLite, never a "confirmed" flag passed in by a caller.
"""
import time
from typing import Any, Protocol

from .account_client import AccountReadError
from .storage import PlanError, PlanStore


EPISODE_STATES = {0: "未收藏", 1: "想看", 2: "看过", 3: "抛弃"}


def _episode_list(sorts: list) -> str:
    return "第" + "、".join(str(sort) for sort in sorts) + "集"


def approval_message(plan: dict[str, Any]) -> str:
    """What the user is asked to confirm; built from SQLite, never from model text."""
    head = f"确认修改 Bangumi 收藏？\n条目：{plan['subject_name']}（{plan['subject_id']}）\n"
    if len(plan["items"]) == 1:
        return (head + f"章节：第{plan['episode_sort']}集 {plan['episode_name']}（{plan['episode_id']}）\n"
                f"修改：{EPISODE_STATES[plan['before_type']]} → {EPISODE_STATES[plan['after_type']]}")
    lines = [head + f"修改：{len(plan['items'])}集标为看过"]
    for state, label in EPISODE_STATES.items():
        sorts = [item["episode_sort"] for item in plan["items"] if item["before_type"] == state]
        if sorts:
            lines.append(f"原为{label}：{_episode_list(sorts)}")
    for reason in sorted({row["reason"] for row in plan["skipped"]}):
        sorts = [row["episode_sort"] for row in plan["skipped"] if row["reason"] == reason]
        lines.append(f"跳过（{reason}）：{_episode_list(sorts)}")
    return "\n".join(lines)


class WriteRejected(Exception):
    """The site clearly refused the write (4xx): nothing was changed."""


class WriteUncertain(Exception):
    """5xx, timeout or lost response: the write may or may not have happened."""


class EpisodeWriter(Protocol):
    def mark_episode_watched(self, subject_id: int, episode_id: int) -> None: ...
    def mark_episodes_watched(self, subject_id: int, episode_ids: list[int]) -> None: ...


class AccountReader(Protocol):
    def get_me(self) -> dict[str, Any]: ...
    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]: ...
    def get_all_my_episode_states(self, subject_id: int, limit: int = ..., max_pages: int = ...,
                                  episode_type: int | None = ...) -> dict[str, Any]: ...


class SimulatedWriter:
    """Offline stand-in for the real Bangumi write; only tests use it.

    outcome: "success" | "rejected" | "server_error" | "lost_response".
    on_write is called when the simulated site applies the change, so a fake reader
    can show the new state on read-back. never_applies keeps those episode IDs unchanged
    though the server answers OK; first_try_misses does the same on the first request only.
    batches records the IDs of each batch request.
    """

    def __init__(self, outcome="success", on_write=None, never_applies=(), first_try_misses=()):
        self.outcome, self.on_write, self.calls = outcome, on_write, 0
        self.never_applies, self.first_try_misses = set(never_applies), set(first_try_misses)
        self.batches: list[list[int]] = []

    def mark_episode_watched(self, subject_id: int, episode_id: int) -> None:
        self.mark_episodes_watched(subject_id, [episode_id])

    def mark_episodes_watched(self, subject_id: int, episode_ids: list[int]) -> None:
        self.calls += 1
        self.batches.append(list(episode_ids))
        if self.outcome == "rejected":
            raise WriteRejected("模拟：服务器拒绝（4xx）")
        if self.outcome == "server_error":
            raise WriteUncertain("模拟：服务器错误（5xx），是否已修改未知")
        missed = self.never_applies | (self.first_try_misses if self.calls == 1 else set())
        for episode_id in episode_ids:
            if self.on_write and episode_id not in missed:
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

    def _move(self, plan, account_id, to_status, now, detail="", item_results=None) -> bool:
        return self.store.transition(plan["plan_id"], account_id=account_id,
                                     from_status=plan["status"], to_status=to_status,
                                     now=now, detail=detail, item_results=item_results)

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

    def plan_for_approval(self, plan_id) -> dict[str, Any]:
        """The plan to show the user, only if it can still be approved and executed."""
        account_id, plan, now = self._load(plan_id)
        if plan["status"] not in ("draft", "approved"):
            raise PlanError(f"该计划不能再执行，当前状态：{plan['status']}")
        self._expire_if_due(plan, account_id, now)
        return plan

    def approve_and_execute(self, plan_id) -> dict[str, Any]:
        """Call only after the user confirmed this plan through a channel the model cannot reach."""
        if self.plan_for_approval(plan_id)["status"] == "draft":
            self.approve(plan_id)
        return self.execute(plan_id)

    def execute(self, plan_id) -> dict[str, Any]:
        account_id, plan, now = self._load(plan_id)
        if plan["status"] != "approved":
            raise PlanError(f"只有已批准的计划可以执行，当前状态：{plan['status']}")
        self._expire_if_due(plan, account_id, now)
        # Re-read the site just before writing; the draft's snapshot may be stale.
        current = self._read_states(plan)
        changed = [item for item in plan["items"] if current[item["episode_id"]] != item["before_type"]]
        if changed:
            self._move(plan, account_id, "rejected", now, "执行前网站状态与草稿记录不一致："
                       + _episode_list([item["episode_sort"] for item in changed]))
            raise PlanError("网站上的章节状态已变化，已拒绝执行；请重新创建草稿")
        # Compare-and-set approved -> executing: a second caller loses here and never writes.
        if not self._move(plan, account_id, "executing", now):
            raise PlanError("该计划已在执行或已被处理，不会重复执行")
        plan["status"] = "executing"
        try:
            self._write(plan, [item["episode_id"] for item in plan["items"]])
        except WriteRejected as exc:
            self._move(plan, account_id, "failed", int(self.clock()), str(exc))
        except WriteUncertain as exc:
            self._move(plan, account_id, "unknown", int(self.clock()), str(exc))
        else:
            self._settle(plan, account_id, "服务器回复成功", server_ok=True)
        return self.store.get_plan(plan_id, account_id=account_id)

    def _write(self, plan, episode_ids) -> None:
        if len(plan["items"]) == 1:
            self.writer.mark_episode_watched(plan["subject_id"], episode_ids[0])
        else:
            self.writer.mark_episodes_watched(plan["subject_id"], episode_ids)

    def _read_states(self, plan) -> dict[int, int]:
        """Current site state of every episode in the plan, or AccountReadError."""
        if len(plan["items"]) == 1:
            episode_id = plan["episode_id"]
            return {episode_id: self.reader.get_my_episode_state(episode_id)["type"]}
        pages = self.reader.get_all_my_episode_states(plan["subject_id"], limit=50, max_pages=20, episode_type=0)
        states = {row["episode_id"]: row["type"] for row in pages["data"]}
        if not pages["complete"] or any(item["episode_id"] not in states for item in plan["items"]):
            raise AccountReadError("章节状态没有读全，无法核对")
        return states

    def _read_results(self, plan) -> dict[int, str]:
        states = self._read_states(plan)
        results = {}
        for item in plan["items"]:
            state = states[item["episode_id"]]
            results[item["episode_id"]] = ("applied" if state == item["after_type"] else
                                           "not_applied" if state == item["before_type"] else
                                           "changed_elsewhere")
        return results

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

    def _settle(self, plan, account_id, reason, server_ok=False) -> None:
        """Decide the outcome from a read-back; stay unknown if the read itself fails."""
        try:
            results = self._read_results(plan)
            missing = [episode_id for episode_id, result in results.items() if result == "not_applied"]
            # A batch answered OK but some episodes still read as before: the read-back says
            # exactly which, so one more request for those alone cannot write anything twice.
            # Episodes changed elsewhere are the user's newer choice and are never overwritten.
            if server_ok and missing and len(results) > 1:
                try:
                    self._write(plan, missing)
                except WriteRejected:
                    pass
                results = self._read_results(plan)
        except AccountReadError as exc:
            if plan["status"] == "executing":
                self._move(plan, account_id, "unknown", int(self.clock()), f"读回失败：{exc}")
            return
        except WriteUncertain as exc:
            self._move(plan, account_id, "unknown", int(self.clock()), f"重试结果未知：{exc}")
            return
        status = self._outcome(results, server_ok)
        if status is not None:
            self._move(plan, account_id, status, int(self.clock()),
                       f"{reason}；{self._describe(plan, results)}", item_results=results)

    @staticmethod
    def _outcome(results: dict[int, str], server_ok: bool) -> str | None:
        """The plan's status for these per-episode results; None means leave it as it is."""
        values = list(results.values())
        if all(value == "applied" for value in values):
            return "verified"
        if server_ok:
            # Server said OK yet nothing moved: do not call it failed, or a later attempt
            # could write twice. Leave it for a read-only check.
            if len(values) == 1 or all(value == "not_applied" for value in values):
                return "unknown"
            return "partial"
        if all(value == "not_applied" for value in values):
            return "failed"
        # One episode now in some other state during a check: someone changed it; stay unknown.
        return None if len(values) == 1 else "partial"

    @staticmethod
    def _describe(plan, results: dict[int, str]) -> str:
        labels = {"applied": "读回为看过", "not_applied": "读回仍为修改前状态",
                  "changed_elsewhere": "被其他操作改成了别的状态"}
        parts = []
        for result, label in labels.items():
            sorts = [item["episode_sort"] for item in plan["items"] if results[item["episode_id"]] == result]
            if sorts:
                parts.append(f"{_episode_list(sorts)}{label}")
        return "；".join(parts)
