"""S4-02: what happens to a write plan when the user cancels the agent task.

settle_plan_on_cancel is written by the user; tests/test_plan_cancel.py is its spec.
The caller only calls it when no execute() for this plan is still running in this process:
an "executing" plan seen here is one whose outcome nobody has recorded yet.
"""
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class PlanService(Protocol):
    """The part of confirmation.ExecutionService this module may use. There is no execute or approve here."""

    def get(self, plan_id: str) -> dict[str, Any]:
        """Read the plan; changes nothing. Its "status" is one of storage.STATUSES."""
        ...

    def cancel(self, plan_id: str) -> dict[str, Any]:
        """draft/approved -> cancelled. Raises storage.PlanError if the plan is in any other status."""
        ...

    def check(self, plan_id: str) -> dict[str, Any]:
        """executing/unknown -> read the site and record what is really there. Never writes again."""
        ...


# kept: nothing done, the plan is still a draft; voided: an approval was withdrawn (now cancelled);
# checked: the write had been sent, the site was read to learn the outcome;
# reported: the plan had already ended, nothing done.
CancelAction = Literal["kept", "voided", "checked", "reported"]


@dataclass
class CancelOutcome:
    action: CancelAction
    plan: dict[str, Any]  # the plan as it is after the action; the interface shows its status


def settle_plan_on_cancel(service: PlanService, plan_id: str) -> CancelOutcome:
    """Decide and carry out what a task cancel means for one plan."""
    raise NotImplementedError("S4-02：由用户实现")
