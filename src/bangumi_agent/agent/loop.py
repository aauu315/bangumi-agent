"""S4-01 minimal tool loop. run_task is written by the user; tests/test_agent_loop.py is its spec.

The loop counts rounds and decides when to stop. Checking and running the tool calls is in
tool_execution.py (S4-R).
"""
from dataclasses import dataclass
from typing import Literal

from .messages import ChatModel, Message, Tools, TextBlock
from .messages import tool_calls_of, text_of
# HIDDEN_ERROR and UNKNOWN_TOOL are not used here; they stay importable from this module for the tests.
from .tool_execution import HIDDEN_ERROR, INCOMPLETE_ARGUMENTS, UNKNOWN_TOOL
from .tool_execution import execute_tool_calls, refuse_tool_calls

__all__ = ["DEFAULT_MAX_ROUNDS", "HIDDEN_ERROR", "INCOMPLETE_ARGUMENTS", "SUMMARY_REQUEST", "UNKNOWN_TOOL",
           "LoopResult", "LoopStatus", "run_task"]

DEFAULT_MAX_ROUNDS = 20

# Text the loop itself puts into the history.
SUMMARY_REQUEST = "已到达本次任务的轮次上限，请不要再调用工具：总结已经完成的部分和还没完成的部分"

# done: the model finished; text_cut: the answer was cut off, the user can say "继续";
# round_limit: rounds ran out and the model summarised, the user can continue;
# repeated_cut: tool calls were cut off twice in a row; unexpected_stop: unknown stop reason.
LoopStatus = Literal["done", "text_cut", "round_limit", "repeated_cut", "unexpected_stop"]


@dataclass
class LoopResult:
    status: LoopStatus
    text: str    # text of the last assistant message ("" when there is none)
    rounds: int  # model requests made in this task


def run_task(model: ChatModel, tools: Tools, system: str, messages: list[Message],
             max_rounds: int = DEFAULT_MAX_ROUNDS) -> LoopResult:
    """Run one task. messages ends with the user's message and is extended in place."""

    if max_rounds < 1:
        raise ValueError(f"最大轮数应当是正整数")
    loop_rounds = 0
    cut_count = 0
    while True:
        if loop_rounds + 1 == max_rounds:
            messages.append(Message("user", [TextBlock(SUMMARY_REQUEST)]))
            model_response = model.complete(system,messages,tools.specs(),allow_tools = False)
            messages.append(model_response.message)
            loop_rounds += 1
            status = "round_limit"
            return LoopResult(status,text_of(model_response.message),loop_rounds)

        model_response = model.complete(system,messages,tools.specs(),allow_tools = True)
        messages.append(model_response.message)
        loop_rounds += 1
        if model_response.stop == "end":
            status = "done"
            return LoopResult(status,text_of(model_response.message),loop_rounds)
        elif model_response.stop == "tool_calls":
            cut_count = 0
            calls = tool_calls_of(model_response.message)
            # [*x] copies the results into a new list; Pylance rejects passing list[ToolResultBlock]
            # itself where list[Block] is expected.
            messages.append(Message("tool",[*execute_tool_calls(calls,tools)]))
        elif model_response.stop == "max_tokens":
            calls = tool_calls_of(model_response.message)
            if len(calls) != 0:
                # Cut-off calls are answered but never run: their arguments may be missing fields.
                messages.append(Message("tool",[*refuse_tool_calls(calls,INCOMPLETE_ARGUMENTS)]))
                cut_count += 1
                if cut_count == 2:
                    status = "repeated_cut"
                    return LoopResult(status,text_of(model_response.message),loop_rounds)
            else:
                status = "text_cut"
                return LoopResult(status,text_of(model_response.message),loop_rounds)
        else:
            status = "unexpected_stop"
            return LoopResult(status,text_of(model_response.message),loop_rounds)
        
    