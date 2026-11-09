"""S4-01 minimal tool loop. run_task is written by the user; tests/test_agent_loop.py is its spec."""
from dataclasses import dataclass
from typing import Literal

from .messages import ChatModel, Message, Tools, ToolResultBlock, TextBlock, ToolError
from .messages import tool_calls_of, text_of

DEFAULT_MAX_ROUNDS = 20

# Texts the loop puts into tool results or the history. The tests compare against these names.
INCOMPLETE_ARGUMENTS = "工具参数不完整（输出被截断或不是合法JSON），本次没有执行，请重新生成这次调用"
UNKNOWN_TOOL = "没有名为 {name} 的工具，或当前不允许调用它"
HIDDEN_ERROR = "工具执行失败，原始错误已隐藏"
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
            results = []
            allowed = {spec.name for spec in tools.specs()}
            for call in calls:
                if call.arguments is None:
                    results.append(ToolResultBlock(call.id,INCOMPLETE_ARGUMENTS,is_error=True))
                    continue
                if call.name not in allowed:
                    result = UNKNOWN_TOOL.format(name=call.name)
                    results.append(ToolResultBlock(call.id,result,is_error=True))
                else:
                    try:
                        result = tools.run(call.name,call.arguments)
                        results.append(ToolResultBlock(call.id,result,is_error=False))
                    except ToolError as e:
                        results.append(ToolResultBlock(call.id, str(e), is_error=True))   
                    except Exception:
                        results.append(ToolResultBlock(call.id, HIDDEN_ERROR, is_error=True))   
            messages.append(Message("tool",results))
        elif model_response.stop == "max_tokens":
            calls = tool_calls_of(model_response.message)
            if len(calls) != 0:
                results = []
                for call in calls:
                    results.append(ToolResultBlock(call.id,INCOMPLETE_ARGUMENTS,is_error=True))
                messages.append(Message("tool",results))
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
        
    