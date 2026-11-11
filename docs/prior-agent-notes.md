# 旧项目经验笔记（Handwrite-ClaudeCode）

旧项目：`E:\myproject\Handwrite-ClaudeCode`，跟随教程手写的 Claude Code 风格 Agent（2026-08-29～10-07）。本笔记供 S4 各卡及之后做 Agent 相关工作时参考，哪张卡用到哪部分见文末“适用到哪些卡”。

旧项目后期不再写测试，测试停在旧状态，**以代码为准**，不参考旧测试。

第 1～5 节和“新项目的决定”由用户凭记忆写出，再对照提交记录和代码补充（2026-11-09，语音输入）。助手只整理格式、修正识别错误；标“助手核对”的是助手对照代码后补的更正或说明。

**推进对话不用打开旧项目目录。** 后续卡要用的旧代码已经摘在文末“移植包”里（2026-11-10 由维护对话从旧项目提交 `3540c98` 摘出）。移植包不够用时告诉用户，由维护对话补，不自己去读旧项目。

## 1. 一轮循环做了什么

1. 用户发一条消息，开启循环。
2. 模型拿到工具表和用户消息后思考，判断要不要调用工具。
3. 模型请求调用工具。有些工具要用户同意后才能运行。
4. 工具结果以**用户消息**的类型返回给模型。每个结果带一个 ID，对应调用时的那个 ID，这样能确定哪条结果回应的是哪次调用。
5. 模型根据工具结果判断：再次调用工具，或者直接结束。
6. 工具调用出错、业务上的各种错误，也都返回给模型，让它根据错误信息判断怎么改进。

最开始是普通打印，后来改成流式响应，内容出来时用户可以随时中止。

**权限**：工具分四类：只读、写入、shell、子 Agent。只读工具直接执行，其他工具需要用户手动同意。检查结果一共四种：直接执行、拒绝、参数不合法（invalid）、交给用户确认。用户拒绝时，返回给模型的是“用户拒绝了这次工具调用，请不要重复尝试同一种操作，请改用更安全的做法”。

> 助手核对：`permissions.py` 的 `check_tool_call` 检查顺序是 ① 工具有没有注册 → ② 当前 Agent 能不能用这个工具 → ③ 参数是否合法 → ④ 按类型决定执行、确认或拒绝。参数检查在注册检查之后。shell 工具不是一律要确认：白名单里的安全命令直接执行，硬性禁止的命令直接拒绝，其余才问用户。

## 2. 工具调用前后 messages 里加了什么

对话从用户的第一条提问开始，然后是模型的回答、模型的工具调用请求、工具结果，再到模型的下一次回答。具体来说：

- **模型的回复**：角色是助手（assistant），内容是模型回复的整个 content，里面可以同时有文字块和工具调用块。
- **工具结果**：模型一次请求多个工具时，所有结果放在**同一条**用户消息里，按工具调用的顺序排列。
- **工具结果块的字段**：类型（`tool_result`）、对应调用的 ID（`tool_use_id`）、内容（`content`），出错时再加 `is_error: true`。
- **什么时候加 `is_error`**：参数不合法或被拒绝时（原因直接写进内容）、用户拒绝时、子 Agent 被中断时、工具执行出错时；某个工具执行时被中断，后面的工具全部不执行，也都标上。

> 助手核对：“某个工具被中断”指工具**正在执行**时按了 Ctrl+C（`execute_tool_uses` 里捕获的 `KeyboardInterrupt`），不是流式输出时的中断；流式输出的中断在 `run_agent_loop` 里另外处理，半截回复不写进历史。

**多种格式怎么兼容**（用户的疑问）：工具结果放在用户消息里是 Anthropic 格式的规定；OpenAI 风格格式里，工具结果是单独的 `tool` 角色消息。新项目要支持多家，是在代码内部统一成一种格式，还是每种格式各写一套业务代码？待讨论，见文末“新项目待决定”。

## 3. 什么情况会结束循环

按模型返回的停止原因（`stop_reason`）分：

- 正常结束（`end_turn`）：返回最终文字。
- 调用工具（`tool_use`）：执行工具后继续下一轮。
- 输出达到最大 token（`max_tokens`）：见第 4 节。
- 未知原因：直接报错。

另外：达到最大轮次时结束；用户中断时抛出 `GenerationInterrupted`。

**最大轮次**：原项目主循环设为空（不限制），子 Agent 是 10 轮。

**两种轮次**：循环是双层的。用户发一条消息算一个任务，任务结束后整个会话并不结束，用户还可以发新任务或补充要求。`task_rounds` 记当前任务的轮次，每个新任务重新计数；`total_rounds` 记整个会话的轮次。最大轮次限制的是 `task_rounds`。

## 4. 踩过的坑

- **is_error 和两类错误**：曾混淆过业务报错和工具调用本身的报错。最后的做法是：工具代码里不处理这些错误，抛出的异常统一由外部接住，再由外部分成业务内可预测的错误（`ToolError`）和不可预测的错误，转换在 `run_tool`。不可预测的错误会把**完整 traceback** 返回给模型。
- **max_tokens 截断工具调用**：输出达到最大 token 时，工具调用被截断，导致工具调用和结果没有成对，引发各种报错。处理改过两次：
  - `66f8ce6`：截断的工具调用不执行，每个调用都回一条错误结果“工具参数可能不完整，请重新生成”。
  - `b31d377`（沿用至今）：照常走权限检查并执行，补上结果后结束本轮，不让模型继续，用户要再输入一句话才会接着做。
- **工具表要维护三份**：最开始每写一个新工具，要写工具本身的代码、写一份工具注册，调用时还有一份工具名称和函数的对照表。后来合成了一份（`tool_registry.py` 的 `ToolSpec`）。

## 5. 哪些不能照搬

- **通信协议**：旧项目用 `anthropic` SDK 调 DeepSeek 的 Anthropic 兼容接口。新项目要支持多家厂商，而且业务代码要在手机上跑，S2 只验证过标准库 `urllib`，所以要自己用 HTTP 写。
- **错误信息**：新项目的报错里可能带着令牌，必须先处理，不能把 traceback 直接返回给模型。
- **输出方式**：旧项目一直在终端里运行，所以用 print。新项目要在手机界面上处理，不能这样。
- **写入确认**：新项目的写入走预览 → 用户确认 → 执行，最后才把结果交给模型。只读工具直接执行。模型拿不到执行写入的工具，所以旧项目“按工具类型问用户”的做法不需要。
- **工具声明**：直接用现成的 MCP 工具。
- **暂不做**：子 Agent 暂时不加；上下文压缩、记忆、流式和中断等做到对应的卡时再复习。

## 新项目的决定

1. **内部消息格式**（2026-11-09 用户已定）：
   - 自己定义一套简单格式，不直接用某一家的格式，结构借鉴内容块：`Message`（role 为 user / assistant / tool）由 `TextBlock`、`ToolCallBlock`（id、name、已解析成字典的 arguments）、`ToolResultBlock`（call_id、content、is_error）组成；模型回复为 `ModelReply`（message、统一的停止原因 end / tool_calls / max_tokens / other、token 用量）。循环只和这些类型打交道。
   - 每种接口格式写一个转换层，只做两件事：内部消息 → 请求；响应 → `ModelReply`。工具参数的 JSON 解析失败按参数不完整处理。
   - **第一个转换层写 Anthropic 格式**，用它接 DeepSeek（DeepSeek 同时支持 OpenAI 和 Anthropic 两种格式）。OpenAI 格式的转换层以后再加。
   - 注意：手机实验（S2）只用 `urllib` 跑通过 DeepSeek 的 OpenAI 风格接口，Anthropic 格式接口在手机上还没测过，S4-01 要补一次。DeepSeek 推理模型的思考内容在后续请求里要不要带回，开工时按官方文档核对。
2. **max_tokens**（2026-11-09 用户已定）：保留，但不做成用户设置，不用它来限制回复。旧项目设成 4096，对参数很长的工具极不友好，重新生成也照样超限。
   - 设为所用模型允许的最大值；上限只是封顶，按实际输出计费，调高不会多花钱。具体数值开工时按官方文档核对。
   - 截断了文字回复：保留已输出的部分，告诉用户回答被截断、可以说“继续”。
   - 截断了工具调用：不执行（被截断的参数可能缺字段），返回“参数不完整”的错误让模型重新生成；连续两次都被截断就停下来告诉用户，避免旧项目那样重试还是超限。
   - 本项目现有工具参数都很短，风险小；以后要注意 S8-02 发日志时正文放在参数里，以及推理模型的思考内容也计入上限。
3. **最大轮次**（2026-11-09 用户已定）：保留，作为安全网，不作为用户必须调的设置。聊天界面里用户可以随时停，但有三种情况靠不上用户：用户没在看（锁屏、切走，后台一直调工具花钱）、模型原地打转、评测需要有限步数。
   - 默认 20 轮，按任务计数（`task_rounds`），可配置；是否在界面上开放给用户，等 S5 再定。
4. **到达最大轮次时**（用户的想法）：最后一次请求不提供工具，只让模型总结已经做了什么、还差什么；循环返回“可继续”的状态。用户选择继续就再给一批轮次，像随时暂停、随时继续，而不是直接失败。“继续”按钮由 S4-02 和 S5 接入。

## 适用到哪些卡

| 卡 | 用到的经验 | 旧代码在哪 |
|---|---|---|
| S4-01 工具循环（done） | 第 1～4 节：循环、消息配对、结束方式、两类错误、max_tokens 截断 | 已重写为 `agent/loop.py` |
| S4-R 工具执行层（done） | 检查、执行、统一出错文案 | 已移植为 `agent/tool_execution.py`，见移植包 A |
| S4-02 取消、待确认、上下文限额（done） | 中断、半截回复不写入历史、会话状态 | 移植包 B、C，已移植为 `agent/session.py` 等 |
| S4-07 上下文压缩 | 上下文压缩 | 移植包 D |
| S4-03 评测 | 假模型的写法 | 本项目 `tests/test_agent_loop.py` 的 `FakeModel` 已是同一写法；移植包 E |
| S4-05 第二家提供商 | 第 5 节通信协议，以及内部统一格式 | 没有可搬的代码 |
| S4-06 信任模式 | 第 1 节按工具类型分档的权限 | 移植包 F |

## 移植包：后续卡直接用的旧代码

每段是旧项目的原代码（有删节，删掉的是 print 和与本项目无关的分支），后面写搬过来要改什么。旧代码用的是 `anthropic` SDK 的字典消息；本项目对应的内部类型在 `agent/messages.py`：

| 旧项目 | 本项目 |
|---|---|
| `{"role": "user", "content": [tool_result, ...]}` | `Message("tool", [ToolResultBlock, ...])` |
| `ToolUseBlock`（`id`、`name`、`input`） | `ToolCallBlock`（`id`、`name`、`arguments`，解析失败为 `None`） |
| `response.stop_reason`：`end_turn` / `tool_use` / `max_tokens` | `ModelReply.stop`：`end` / `tool_calls` / `max_tokens` / `other` |
| `client.messages.create(...)` / `.stream(...)` | `ChatModel.complete(system, messages, tools, allow_tools)`；流式接口还没有 |
| `response.usage.input_tokens` | `ModelReply.usage.input_tokens` |

### A. 工具执行层（S4-R 已移植）

旧项目三个函数与本项目 `agent/tool_execution.py` 的对应：

| 旧项目 | 本项目 | 移植时改了什么 |
|---|---|---|
| `permissions.check_tool_call(tool, tool_input, allowed_tool_names)` → `(decision, reason)`，decision 为 allow / deny / invalid / confirm | `check_tool_call(call, allowed)` → `(Decision, 文案)`，目前只有 allow / deny / invalid | “没注册”和“当前 Agent 不能用”合成一条 deny：本项目的允许列表就是 `tools.specs()` 里的名字。参数是否符合 schema 由 MCP 工具自己校验（失败时抛 `ToolError`），这里只挡 `arguments is None`。confirm 留给 S4-06 |
| `real_agent.run_tool(tool, tool_input)` → `(output, is_error)` | `run_tool(tools, call)` → `(content, is_error)` | 意外异常不再把 traceback 交给模型，只回 `HIDDEN_ERROR` |
| `real_agent.execute_tool_uses(blocks, allowed)` → `(results, interrupted)` | `execute_tool_calls(calls, tools)` → `list[ToolResultBlock]` | 去掉 print、`ask_user` 和中断处理；中断在 S4-02 按移植包 B 加回来 |
| 没有 | `refuse_tool_calls(calls, reason)` | 新增：不执行、给每个调用回同一条错误。截断时用，S4-02 取消后剩下的调用也可以用 |

检查顺序沿用旧项目：先看名字能不能用，再看参数。`run_task` 现在只调用 `execute_tool_calls` 和 `refuse_tool_calls`。

### B. 工具执行中的中断（S4-02）

旧 `execute_tool_uses` 的中断部分。要点：某个工具执行时被中断，它和它后面的调用都**照样各回一条结果**（标 `is_error`），保持调用和结果配对；然后由调用方结束本轮。

```python
def execute_tool_uses(tool_use_blocks, allowed_tool_names):
    tool_results = []
    interrupted_during_tool = False

    for block in tool_use_blocks:
        try:
            if interrupted_during_tool:
                output = "前一个工具调用已被中断，本工具没有执行。"
                is_error = True
            else:
                ...  # 检查并执行，即移植包 A
        except KeyboardInterrupt:
            output = (
                "用户中断了工具执行。工具可能已经产生了部分外部效果，"
                "继续前应先检查当前状态。"
            )
            is_error = True
            interrupted_during_tool = True

        tool_results.append(...)  # 每个调用都有结果

    return tool_results, interrupted_during_tool
```

调用方（旧 `run_agent_loop`）先把结果写进历史，再抛出中断：

```python
tool_results, interrupted_during_tool = execute_tool_uses(tool_use_blocks, allowed_tool_names)
messages.append({"role": "user", "content": tool_results})
if interrupted_during_tool:
    raise GenerationInterrupted
```

搬过来要改：
- 手机界面没有 Ctrl+C。取消改成调用方传进来的一个标记（例如 `threading.Event`），每执行一个调用前检查一次。已知限制：阻塞中的 HTTP 请求不能立即取消（STATUS 的 future_requirements 已记）。
- 旧项目对“写入可能已经发出”只回了一句提示文字。本项目要求进入核对：只读核对远端状态，不盲目重试（S3-03 的 unknown 处理）。这部分旧项目没有，是 S4-02 里用户要写的。
- 本项目模型拿不到执行写入的工具，所以循环里被取消的只会是只读工具和生成预览；“取消后不再写”主要约束的是确认界面那一侧。

### C. 流式输出的中断、会话状态和外层循环（S4-02）

流式输出时中断：**半截回复不写进历史**，轮次已经计数。

```python
state.task_rounds += 1
state.total_rounds += 1
try:
    with client.messages.stream(model=MODEL, max_tokens=..., system=system,
                                tools=available_tools, messages=messages) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        response = stream.get_final_message()
except KeyboardInterrupt as error:
    # 半截回复没有写入对话历史
    raise GenerationInterrupted from error

messages.append({"role": "assistant", "content": response.content})  # 只有完整回复才写入
```

会话状态和外层循环：

```python
@dataclass
class AgentState:
    total_rounds: int = 0          # 整个会话
    task_rounds: int = 0           # 当前任务，上限限制的是它
    last_response_tokens: int = 0
    total_tokens: int = 0

def main():
    messages = []
    state = AgentState()
    while True:
        user_input = input("请输入问题或任务描述：").strip()
        # 只在收到新任务时清零；中断后的补充要求仍属于当前任务。
        state.task_rounds = 0
        messages.append({"role": "user", "content": user_input})

        while True:
            try:
                run_agent_loop(messages=messages, ..., state=state)
                break
            except GenerationInterrupted:
                follow_up = input("补充要求（直接回车则让模型重新评估）：").strip()
                if follow_up:
                    retry_instruction = follow_up
                else:
                    retry_instruction = (
                        "上一次生成在完成前被中断，未完成内容没有保存在历史中。"
                        "请从头重新评估当前任务，先考虑可行方案，"
                        "再选择合适的方法继续。"
                    )
                messages.append({"role": "user", "content": retry_instruction})
```

搬过来要改：
- 本项目的 `run_task` 每次调用是一个任务，自己从 0 计轮次并在 `LoopResult.rounds` 里返回，所以 `task_rounds` 已经有了；会话层只需要累计 `total_rounds` 和 token 用量（`ModelReply.usage`，目前 `run_task` 没有往外传）。
- 外层循环的 `input()` 换成界面发来的消息；“继续”（`round_limit`、`text_cut` 之后）就是追加一条用户消息再调一次 `run_task`，`tests/test_agent_loop.py` 的 `test_continuing_starts_a_fresh_count` 已经这样做。
- 本项目现在是非流式请求，没有“半截回复”。接入流式之前，取消只能发生在两次请求之间和工具之间；接入流式后沿用“半截回复不写入历史”。
- 取消不必用异常表示，可以是 `LoopResult` 的一种状态，由用户在 S4-02 设计事件和终止条件时决定。

### D. 上下文压缩（S4-07）

原定是 S4-02 的附加项，2026-11-11 改为单独的 S4-07 卡。S4-02 已做的部分：限额基线在 `agent/session.py`（`DEFAULT_CONTEXT_LIMIT`、`_ObservedModel.complete` 发请求前检查、`SessionState.last_request_tokens`）。

```python
COMPRESSION_TRIGGER_TOKENS = 30_000   # 下轮输入 + 预留输出达到此值时压缩
MODEL_MAX_OUTPUT_TOKENS = 4096
KEEP_RECENT_MESSAGES = 4              # 至少保留最近 4 条原始消息

COMPRESSION_PROMPT = """
你正在压缩一段 Agent 的较早对话历史。

请只输出一段简洁、准确、可以供 Agent 继续工作的摘要，不要继续执行任务。

摘要必须保留：
1. 用户最初的目标和后来补充的要求。
2. 用户明确提出的限制、偏好和禁止事项。
3. 已经查明的关键事实和已经作出的决定。
4. 已经读取或修改过的重要文件及其作用。
5. 已经执行的重要命令、结果和错误。
6. 已经完成的工作。
7. 尚未完成的事项和下一步应该做什么。

不要编造历史中没有的信息。
可以删除寒暄、重复解释、已经被证明无效的尝试和冗长工具输出。
"""

def contains_tool_result(message) -> bool:
    content = message["content"]
    if isinstance(content, str):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)

def compress_messages(history, next_request_tokens):
    """超过触发线时，将较旧历史压缩成一条摘要消息。返回（新历史，摘要调用消耗的 token）。"""
    if next_request_tokens < COMPRESSION_TRIGGER_TOKENS:
        return history, 0
    if len(history) <= KEEP_RECENT_MESSAGES:
        return history, 0

    cut_index = len(history) - KEEP_RECENT_MESSAGES
    # 不要把 assistant 的 tool_use 和紧随其后的 user tool_result 分开。
    if contains_tool_result(history[cut_index]):
        cut_index -= 1
    if cut_index <= 0:
        return history, 0

    old_messages = history[:cut_index]
    recent_messages = history[cut_index:]

    summary_response = client.messages.create(
        model=SUMMARY_MODEL, max_tokens=MODEL_MAX_OUTPUT_TOKENS,
        system=COMPRESSION_PROMPT, messages=old_messages,
    )
    summary_text = "\n".join(b.text for b in summary_response.content if b.type == "text").strip()
    if not summary_text:
        raise RuntimeError("上下文压缩失败：模型没有返回摘要文本。")

    summary_message = {
        "role": "user",
        "content": (
            "以下是较早对话的压缩摘要。请把它当作已发生的历史，"
            "继续完成尚未结束的任务：\n\n" + summary_text
        ),
    }
    return [summary_message, *recent_messages], response_token_count(summary_response)
```

在循环里的位置：每次正式请求之前先计数、再压缩，压缩完成后才给轮次加 1；`messages[:] = compressed_messages` 原地替换，调用方手里的列表跟着变。

搬过来要改：
- **token 来源已定（S4-02）：** 旧项目每次请求前调 SDK 的 `client.messages.count_tokens`；本项目不用计数端点，用上一次 `ModelReply.usage.input_tokens + output_tokens` 估计下一次的输入（下一次的输入大致是它再加上新的工具结果），存在 `SessionState.last_request_tokens`。DeepSeek 有没有计数端点没有查，也不需要。
- 触发线 30 000 和预留 4096 是按旧项目设的。本项目 max_tokens 设为模型上限（DeepSeek 384K），不能再用“输入 + max_tokens”来判断，要按模型的上下文窗口另定。
- `contains_tool_result(message)` 在本项目就是 `message.role == "tool"`。
- 摘要请求用 `ChatModel.complete(COMPRESSION_PROMPT, old_messages, tools.specs(), allow_tools=False)`：旧消息里有工具调用，工具表要照样带上。
- 旧消息里的 `OpaqueBlock`（thinking）随旧消息一起被摘要替换；保留下来的最近几条里的 thinking 块必须原样留着，否则 DeepSeek 可能 400。
- 提示词第 4、5 条说的是文件和命令，换成本项目的内容：查到的条目和 ID、生成过的预览及其是否已确认。**摘要不能让模型以为某次写入已经执行**：写入结果只以业务层记录为准。
- S4-02 的基线“上下文过大时明确停止、要求新对话”保留，压缩加在它前面：压缩失败或压不下去时仍按基线停止，两者分别测试。
- 写入状态由程序按业务记录重新写进历史（`session.py` 的 `_report_plan` 写的“程序记录”消息），不靠摘要复述。

### E. 假模型（S4-02 流式、S4-03）

本项目 `tests/test_agent_loop.py` 的 `FakeModel`（按顺序吐出预先写好的回复，并记下每次请求）和旧项目 `FakeClient` 是同一写法，直接用它，不必参考旧测试。

旧测试里有一个本项目还没有的手法，S4-02 测取消时可以照做：在预先写好的回复列表里放一个特殊标记，假模型拿到它就在“生成中途”抛出中断，后面再跟一条正常回复，用来检查两件事：半截回复没有进历史；中断后追加的补充要求出现在下一次请求里。

```python
def stream(self, **kwargs):
    self.calls.append(copy.deepcopy(kwargs))
    result = self.responses.pop(0)
    if result is KeyboardInterrupt:      # 列表里的标记
        return InterruptingStream()      # 迭代到一半就 raise KeyboardInterrupt
    return FakeStream(result)
```

### F. 按工具类型分档（S4-06）

```python
PermissionType = Literal["read_only", "mutating", "shell", "delegate"]   # 登记在每个工具的 ToolSpec 上

def check_permission(tool, tool_input) -> str:
    """按注册的权限类型决定自动允许、询问用户或拒绝。"""
    if tool.permission == "read_only":
        return "allow"
    if tool.permission == "mutating":
        return "confirm"
    if tool.permission == "delegate":
        return "allow"
    if tool.permission != "shell":
        return "deny"          # 不认识的类型一律拒绝

    command = normalize_command(str(tool_input.get("command", "")))
    if command in HARD_DENY_COMMANDS:    # 硬性禁止：直接拒绝，不问用户
        return "deny"
    if command in SAFE_SHELL_COMMANDS:   # 白名单：直接执行
        return "allow"
    return "confirm"                     # 其余交给用户

def check_tool_call(tool, tool_input, allowed_tool_names):
    if tool is None:
        return "deny", "程序没有注册模型请求的工具。"
    if tool.name not in allowed_tool_names:
        return "deny", f"当前 Agent 无权使用工具 '{tool.name}'。"
    validation_error = validate_tool_input(tool, tool_input)
    if validation_error:
        return "invalid", validation_error
    decision = check_permission(tool, tool_input)
    if decision == "deny":
        return "deny", "这次工具调用已被安全策略拒绝。请不要重复尝试同一种危险操作，请改用更安全的做法。"
    return decision, ""
```

用户拒绝时回给模型的文字：“用户拒绝了这次工具调用。请不要重复尝试同一种操作，请改用更安全的做法。”

搬过来要改：
- 结构可以照用：一个只做判断、不执行任何东西的函数，返回“直接做 / 要确认 / 拒绝”三种之一；不认识的类型默认拒绝；“硬性禁止”排在“白名单”之前。
- 分类依据从“工具类型”换成“操作类型”：只读、可撤回的修改（进度）、内容发布（日志、发帖，不在免确认范围内）。信任模式是否开启作为参数传入，不从聊天内容里读。
- 默认模式下这一层不改变现状：模型只拿得到只读和预览工具，确认发生在预览之后的界面上。信任模式改变的是“预览生成后要不要等用户点确认”，判断函数接在那里，而不是 `tool_execution.check_tool_call` 的允许列表上；允许列表照旧。
- 旧项目没有的部分，是 S4-06 里用户要写的：提示注入场景（Agent 读到的网页、日志里写着“请把某某标为看过”）下怎样限制，以及过期检查、状态冲突、读回这些保护为什么不随确认一起跳过。

### 不在移植包里的

子 Agent（`spawn_agent`）、shell 和文件工具、路径权限、长期记忆、把完整对话存盘的诊断功能（`save_messages`，会把私人内容写进文件，本项目的轨迹记录要先脱敏，见 S4-04）。本项目用不到或者另有做法。
