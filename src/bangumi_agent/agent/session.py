"""S4-02 session layer: several tasks over one history, events, the user's confirmations, limits.

Ported from the old project's AgentState and outer loop (docs/prior-agent-notes.md, pack C).
run_task stays as it is: events come from wrapping the model and the tools it is given.

One session is used from one worker thread. Only the cancel flag may be set from another thread.
"""
import json
from dataclasses import dataclass
from typing import Any, Protocol

from ..account_client import AccountReadError
from ..confirmation import approval_message
from ..storage import PlanError
from .anthropic_format import ModelError
from .events import PLAN_STATUS_TEXT, Emit, Event, tool_label
from .loop import DEFAULT_MAX_ROUNDS, run_task
from .messages import (CancelFlag, ChatModel, Message, ModelReply, OpaqueBlock, TextBlock, ToolError, Tools,
                       ToolSpec)
from .plan_cancel import PlanService, settle_plan_on_cancel
from .tool_execution import HIDDEN_ERROR

# Stop before the next request once the last one used this many tokens (input + output).
# Both providers allow far more (1M); this keeps a runaway history from costing a lot.
DEFAULT_CONTEXT_LIMIT = 200_000

CONTEXT_FULL = "这次对话已经太长，不能再继续了。请开始一个新对话；之前查到的内容不会自动带过去。"
ENDED_PLAN_STATUSES = ("cancelled", "expired", "rejected", "verified", "failed", "partial")


class ApprovalService(PlanService, Protocol):
    """What the session needs from confirmation.ExecutionService. Only confirm() reaches the write."""

    def plan_for_approval(self, plan_id: str) -> dict[str, Any]: ...

    def approve_and_execute(self, plan_id: str) -> dict[str, Any]: ...


class ContextFull(Exception):
    """The history is too large for another request. Nothing was dropped; the task just stops."""


@dataclass
class SessionState:
    total_rounds: int = 0   # model requests in the whole session; run_task counts each task itself
    input_tokens: int = 0   # summed over every request
    output_tokens: int = 0
    last_request_tokens: int = 0  # input + output of the latest reply: about the size of the next input


@dataclass
class TaskOutcome:
    # A LoopStatus, or "model_error" / "context_full" when the task could not go on.
    status: str
    text: str
    rounds: int


class _ObservedModel:
    """Announces each request, counts tokens, and refuses to send once the history is too large."""

    def __init__(self, model: ChatModel, state: SessionState, emit: Emit, context_limit: int):
        self.model, self.state, self.emit, self.context_limit = model, state, emit, context_limit
        self.task_rounds = 0

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        if self.state.last_request_tokens >= self.context_limit:
            raise ContextFull()
        self.task_rounds += 1
        self.emit(Event("model_request", "正在思考……", {"round": self.task_rounds}))
        reply = self.model.complete(system, messages, tools, allow_tools)
        self.state.total_rounds += 1
        self.state.input_tokens += reply.usage.input_tokens
        self.state.output_tokens += reply.usage.output_tokens
        self.state.last_request_tokens = reply.usage.input_tokens + reply.usage.output_tokens
        for block in reply.message.content:
            if isinstance(block, OpaqueBlock) and block.kind == "thinking" and block.text:
                self.emit(Event("thinking", "思考过程", {"text": block.text}))
        return reply


class _ObservedTools:
    """Announces each tool that really runs, and notices previews that made a plan."""

    def __init__(self, tools: Tools, session: "AgentSession"):
        self.tools, self.session = tools, session

    def specs(self) -> list[ToolSpec]:
        return self.tools.specs()

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        emit = self.session.emit
        label = tool_label(name, arguments)
        emit(Event("tool_call", label, {"name": name, "arguments": arguments}))
        try:
            result = self.tools.run(name, arguments)
        except ToolError as error:
            emit(Event("tool_result", f"{label}：没有成功", {"name": name, "is_error": True, "error": str(error)}))
            raise
        except Exception:
            emit(Event("tool_result", f"{label}：没有成功", {"name": name, "is_error": True, "error": HIDDEN_ERROR}))
            raise
        emit(Event("tool_result", f"{label}：完成", {"name": name, "is_error": False, "size": len(result)}))
        self.session.note_preview(name, result)
        return result


class AgentSession:
    def __init__(self, model: ChatModel, tools: Tools, system: str, emit: Emit,
                 plans: ApprovalService | None = None, max_rounds: int = DEFAULT_MAX_ROUNDS,
                 context_limit: int = DEFAULT_CONTEXT_LIMIT):
        self.model, self.tools, self.system, self.emit, self.plans = model, tools, system, emit, plans
        self.max_rounds, self.context_limit = max_rounds, context_limit
        self.messages: list[Message] = []
        self.state = SessionState()
        self.pending_plans: list[str] = []  # made by a preview in this session, not ended yet

    # --- tasks ---------------------------------------------------------------------------------

    def send(self, text: str, cancel: CancelFlag | None = None) -> TaskOutcome:
        """Run one task: a new question, or "继续" after a cancel, a cut-off answer or the round limit."""
        if self.state.last_request_tokens >= self.context_limit:
            # Checked before the message is added, so a refused message leaves the history untouched.
            return self._stopped("context_full", CONTEXT_FULL, 0)
        self.messages.append(Message("user", [TextBlock(text)]))
        model = _ObservedModel(self.model, self.state, self.emit, self.context_limit)
        try:
            result = run_task(model, _ObservedTools(self.tools, self), self.system, self.messages,
                              max_rounds=self.max_rounds, cancel=cancel)
        except ContextFull:
            return self._stopped("context_full", CONTEXT_FULL, model.task_rounds)
        except ModelError as error:
            return self._stopped("model_error", f"模型请求失败：{error}", model.task_rounds)
        if result.status == "cancelled":
            self.emit(Event("cancelled", "已取消", {"rounds": result.rounds, "text": result.text}))
            self._settle_plans_after_cancel()
        else:
            self.emit(Event("completed", result.text, {"status": result.status, "rounds": result.rounds,
                                                       "text": result.text}))
        return TaskOutcome(result.status, result.text, result.rounds)

    def _stopped(self, kind: str, text: str, rounds: int) -> TaskOutcome:
        self.emit(Event("error", text, {"kind": kind}))
        return TaskOutcome(kind, text, rounds)

    # --- plans ---------------------------------------------------------------------------------

    def note_preview(self, tool_name: str, result: str) -> None:
        """A preview tool returned: if it made a plan, ask the user. The text shown comes from the plan
        record (the same text as the MCP popup), not from anything the model wrote."""
        if self.plans is None or not tool_name.startswith("preview_"):
            return
        try:
            plan_id = json.loads(result).get("plan_id")
        except (ValueError, AttributeError):
            return
        if not isinstance(plan_id, str) or plan_id in self.pending_plans:
            return
        try:
            message = approval_message(self.plans.plan_for_approval(plan_id))
        except PlanError:
            return
        self.pending_plans.append(plan_id)
        self.emit(Event("approval_required", "需要你确认这次修改", {"plan_id": plan_id, "message": message}))

    def confirm(self, plan_id: str) -> dict[str, Any] | None:
        """The user confirmed this plan in the interface. The only path in the agent that writes.

        Never call this for the model: it has no tool that leads here, and text such as "已确认"
        in its reply is not a confirmation. Returns the plan record, or None if its state could
        not be read (an error event says so).
        """
        if self.plans is None or plan_id not in self.pending_plans:
            raise PlanError("这份计划不是本次对话里等待确认的计划")
        try:
            plan = self.plans.approve_and_execute(plan_id)
        except (PlanError, AccountReadError) as error:
            # Refused, or the site could not be read. Treat the attempt like a cancel: an approval
            # is withdrawn, a write that went out is checked, and the record decides what is shown.
            return self._settle_one(plan_id, str(error))
        self._report_plan(plan, "confirm", plan.get("detail") or "")
        return plan

    def decline(self, plan_id: str) -> dict[str, Any]:
        """The user said no in the interface: the plan is voided, nothing is written."""
        if self.plans is None or plan_id not in self.pending_plans:
            raise PlanError("这份计划不是本次对话里等待确认的计划")
        plan = self.plans.cancel(plan_id)
        self._report_plan(plan, "decline", "")
        return plan

    def _settle_plans_after_cancel(self) -> None:
        if self.plans is None:
            return
        for plan_id in list(self.pending_plans):
            self._settle_one(plan_id, "", report_kept=False)

    def _settle_one(self, plan_id: str, reason: str, report_kept: bool = True) -> dict[str, Any] | None:
        assert self.plans is not None
        try:
            outcome = settle_plan_on_cancel(self.plans, plan_id)
        except (PlanError, AccountReadError) as error:
            self.emit(Event("error", "这份计划的状态没能确定，请稍后核对，不要重复操作",
                            {"kind": "plan_unsettled", "plan_id": plan_id, "detail": str(error)}))
            return None
        if outcome.action != "kept" or report_kept:  # after a cancel, an untouched draft is not news
            self._report_plan(outcome.plan, outcome.action, reason or outcome.plan.get("detail") or "")
        return outcome.plan

    def _report_plan(self, plan: dict[str, Any], action: str, detail: str) -> None:
        """Tell the interface, and the model's history, what the plan record says. The model is told
        after the fact; what it then says about the write is not the source of truth."""
        status = str(plan["status"])
        summary = PLAN_STATUS_TEXT.get(status) or status
        self.emit(Event("plan_result", summary, {"plan_id": plan["plan_id"], "status": status,
                                                 "action": action, "detail": detail}))
        if status in ENDED_PLAN_STATUSES and plan["plan_id"] in self.pending_plans:
            self.pending_plans.remove(plan["plan_id"])
        note = f"【程序记录，不是用户说的话】计划 {plan['plan_id']} 现在的状态：{status}（{summary}）。"
        self.messages.append(Message("user", [TextBlock(note + (f"说明：{detail}" if detail else ""))]))
