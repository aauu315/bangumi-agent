"""S4-02 event contract: what the interface is told while a task runs.

Every event has one line an ordinary user can read (summary) and a few facts (data). Short facts
such as the round or a plan ID can be shown directly; function names and arguments are detail the
interface may fold away. An event describes what the program did or saw, never what the model
claims: a write's outcome is only ever reported by plan_result, built from the plan record.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

EventType = Literal[
    "model_request",      # a request is about to be sent; data: round
    "thinking",           # the reply carried reasoning; data: text
    "tool_call",          # a tool is about to run; data: call name, arguments
    "tool_result",        # it finished; data: name, is_error, size, error (only on failure)
    "approval_required",  # a preview made a plan the user must confirm; data: plan_id, message
    "plan_result",        # what really happened to a plan; data: plan_id, status, action, detail
    "completed",          # the task ended on its own; data: status, rounds, text
    "cancelled",          # the user cancelled; data: rounds, text
    "error",              # the task could not go on; data: kind
]


@dataclass(frozen=True)
class Event:
    type: EventType
    summary: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "summary": self.summary, "data": self.data}

    def to_json(self) -> str:
        """One line of JSON, for handing events to the phone interface."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


Emit = Callable[[Event], None]

# What each tool is doing, in words an ordinary user would use. {keyword} is taken from the arguments.
TOOL_LABELS = {
    "search_subjects": "搜索“{keyword}”",
    "get_subject": "查看条目详情",
    "get_episodes": "查看章节列表",
    "get_calendar": "查看每日放送",
    "get_subject_characters": "查看角色",
    "get_subject_persons": "查看制作人员",
    "get_subject_relations": "查看关联条目",
    "get_my_collections": "查看我的收藏",
    "get_my_collection": "查看我对这部作品的收藏",
    "get_my_episode_states": "查看我的观看进度",
    "preview_episode_watched": "准备把一集标为看过（要你确认后才会修改）",
    "preview_watched_up_to": "准备标记看到第几集（要你确认后才会修改）",
    "preview_episode_change": "准备修改章节状态（要你确认后才会修改）",
}

PLAN_STATUS_TEXT = {
    "draft": "还是草稿，没有修改 Bangumi",
    "approved": "已批准，还没有修改 Bangumi",
    "cancelled": "已作废，没有修改 Bangumi",
    "expired": "已过期，没有修改 Bangumi",
    "rejected": "网站上的状态变了，没有修改 Bangumi",
    "executing": "正在执行，结果还不知道",
    "verified": "已修改，并从 Bangumi 读回核对过",
    "failed": "没有改成",
    "unknown": "结果未知，请稍后核对，不要重复操作",
    "partial": "只改成了一部分",
}


def tool_label(name: str, arguments: dict[str, Any]) -> str:
    template = TOOL_LABELS.get(name, "使用工具")
    try:
        return template.format(**{key: str(value)[:40] for key, value in arguments.items()})
    except (KeyError, IndexError):
        return template.replace("“{keyword}”", "")
