"""Compare the tools a case expected (the "expect" part of evals/20-cases.json) with the calls made.

score_tools is the user's function (S4-03). Plan counts, writes and answer checks are checked
elsewhere; this only looks at tool names, their order and the preview arguments.
"""
from dataclasses import dataclass
from typing import Any

PREVIEW_TOOLS = frozenset({"preview_episode_watched", "preview_watched_up_to", "preview_episode_change"})

# Problem kinds
MISSING_TOOL = "missing_tool"              # a required tool (or none of its alternatives) was called
WRONG_ORDER = "wrong_order"                # [A, B]: B was called before any A
UNEXPECTED_PREVIEW = "unexpected_preview"  # a preview tool not in allowed_previews
TOO_MANY_CALLS = "too_many_calls"          # a tool called more often than max_calls allows
WRONG_PREVIEW_ARGS = "wrong_preview_args"  # an allowed preview call lacks a key/value of preview_args


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Problem:
    kind: str    # one of the kinds above
    tool: str    # the tool it is about; for [A, B] order, B
    detail: str  # one line for the report, free wording


@dataclass(frozen=True)
class ToolScore:
    problems: list[Problem]

    @property
    def passed(self) -> bool:
        return not self.problems


def score_tools(expect: dict[str, Any], calls: list[ToolCall]) -> ToolScore:
    """List every rule of expect that the calls break; no problems means the case passes on tools.

    expect has required_tools, order, allowed_previews, max_calls and preview_args, as in
    evals/README.md; plans is not checked here. calls are in the order they were made.
    """
    problems: list[Problem] = []

    # 1. required_tools: 每一项必须被调用过；某一项是 list 时，其中任一个被调用过就算满足。
    #    问题的 tool 写那个工具名；是 list 时写成 "A 或 B"。

    # 2. order: 每一对 [A, B]，B 第一次被调用之前，A 至少被调用过一次。
    #    B 根本没被调用：这一对不算错（缺不缺由第 1 项管）。

    # 3. 预览类工具（PREVIEW_TOOLS）中不在 allowed_previews 里的，每出现一个记一条。
    #    只读工具多调用不算错。

    # 4. max_calls: {工具名: 上限}，调用次数超过上限记一条。

    # 5. preview_args 不是 None 时：每一次允许的预览调用，它的参数都要包含 preview_args 的所有键，
    #    值也相等；多出来的键不管。不符合的每次记一条。

    raise NotImplementedError("S4-03：由用户实现")
    return ToolScore(problems)
