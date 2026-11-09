"""S4-01 minimal tool loop. run_task is written by the user; tests/test_agent_loop.py is its spec."""
from dataclasses import dataclass
from typing import Literal

from .messages import ChatModel, Message, Tools

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
    raise NotImplementedError("S4-01：由用户实现")
