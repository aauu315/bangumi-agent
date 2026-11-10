"""Checks and runs the tool calls of one assistant message (S4-R).

Ported from Handwrite-ClaudeCode: check_tool_call (permissions.py), run_tool and execute_tool_uses
(real_agent.py). The loop only decides when to call this; what may run and how errors are worded
is decided here. docs/prior-agent-notes.md lists what changed in the port.
"""
from typing import Literal

from .messages import CancelFlag, ToolCallBlock, ToolError, ToolResultBlock, Tools

# Texts put into tool results. The tests compare against these names.
INCOMPLETE_ARGUMENTS = "工具参数不完整（输出被截断或不是合法JSON），本次没有执行，请重新生成这次调用"
UNKNOWN_TOOL = "没有名为 {name} 的工具，或当前不允许调用它"
HIDDEN_ERROR = "工具执行失败，原始错误已隐藏"
CANCELLED_TOOL = "用户取消了任务，这次工具调用没有执行"

# allow: run it; deny: this agent may not call it; invalid: the arguments cannot be used.
# S4-06 adds a fourth value for calls that need the user's confirmation.
Decision = Literal["allow", "deny", "invalid"]


def check_tool_call(call: ToolCallBlock, allowed: set[str]) -> tuple[Decision, str]:
    """Decide whether one call may run. The second value is the text for the model when it may not."""
    if call.name not in allowed:
        return "deny", UNKNOWN_TOOL.format(name=call.name)
    if call.arguments is None:
        return "invalid", INCOMPLETE_ARGUMENTS
    return "allow", ""


def run_tool(tools: Tools, call: ToolCallBlock) -> tuple[str, bool]:
    """Run a call that passed check_tool_call. Returns (content, is_error); never raises."""
    if call.arguments is None:
        raise RuntimeError("检查放行了一个参数不完整的工具调用")
    try:
        return tools.run(call.name, call.arguments), False
    except ToolError as error:
        return str(error), True  # the tool's own message, safe for the model
    except Exception:
        return HIDDEN_ERROR, True  # may carry a token or a traceback: never forwarded


def execute_tool_calls(calls: list[ToolCallBlock], tools: Tools,
                       cancel: CancelFlag | None = None) -> list[ToolResultBlock]:
    """One result per call, in the order of the calls. A refused or failing call does not stop the rest.

    The cancel flag is looked at before each call (ported from the old project's interrupt handling):
    once it is set, the remaining calls are answered with CANCELLED_TOOL and not run. A call that is
    already running finishes and keeps its real result. The caller reads the flag afterwards to stop.
    """
    allowed = {spec.name for spec in tools.specs()}
    results: list[ToolResultBlock] = []
    for index, call in enumerate(calls):
        if cancel is not None and cancel.is_set():
            return results + refuse_tool_calls(calls[index:], CANCELLED_TOOL)
        decision, reason = check_tool_call(call, allowed)
        if decision == "allow":
            content, is_error = run_tool(tools, call)
        else:
            content, is_error = reason, True
        results.append(ToolResultBlock(call.id, content, is_error))
    return results


def refuse_tool_calls(calls: list[ToolCallBlock], reason: str) -> list[ToolResultBlock]:
    """Answer every call with the same error without running any, so calls and results stay paired."""
    return [ToolResultBlock(call.id, reason, is_error=True) for call in calls]
