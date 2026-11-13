# 评测用例格式

`20-cases.json` 是 S4-03 冻结的 20 个用例，每类 4 个。用例的大白话说明在 `docs/agent-evaluation.md`。

## 运行评测

在项目根目录运行。默认用假模型跑全部 20 例，报告输出到终端。

```powershell
.\.venv\Scripts\python.exe scripts/agent_eval.py
```

经确认后，可以用本机保存的 DeepSeek Key 跑 6 例真实模型评测，有 API 费用。两种模式都使用固定的 Bangumi 返回数据和临时草稿库。

```powershell
.\.venv\Scripts\python.exe scripts/agent_eval.py --deepseek --output work/deepseek-eval.json
```

`--case` 可重复传入以指定用例，`--model` 可指定 DeepSeek 模型名。报告记录模型名、各轮输入与回答、工具调用和错误、草稿状态、模拟写入次数、自动判定失败率和待人工核对的回答项。自动匹配通过后仍可对照 `rule` 核对回答意思。假模型的脚本回复用于检查评测流程，真实模型的结果只覆盖本次所选样本和固定数据。

校园动画练习可单独离线运行：`.\.venv\Scripts\python.exe scripts/agent_eval.py --case spare-1`。

## 顶层

| 字段 | 含义 |
|---|---|
| `fixture_sets` | 几个用例共用的工具返回，按名字引用（目前只有 `frieren`） |
| `cases` | 20 个用例 |
| `spare_cases` | 备用用例，不计入 20 例 |

## 每个用例

| 字段 | 含义 |
|---|---|
| `id`、`category`、`title` | 编号、类别（query、ambiguous、preview、injection、failure）、标题 |
| `turns` | 用户依次发的话。多于一句时在同一个会话里接着发 |
| `fixtures` | 引用的共用工具返回 |
| `tool_rules` | 本例自己的工具返回，优先于共用的 |
| `expect` | 程序能直接统计的预期，见下 |
| `answer_checks` | 要看模型最后回答的项，见下 |

## 工具返回规则

每条规则是 `{"tool", "when"?, "result" | "error"}`：
- `when` 是参数的部分匹配。字符串参数只要包含 `when` 里的值就算匹配（模型可能搜“芙莉莲”也可能搜“葬送的芙莉莲”）；其他类型要相等。没有 `when` 就匹配这个工具的任何调用。
- 按本例 `tool_rules`、再按 `fixtures` 的顺序找第一条匹配的规则。`result` 原样转成 JSON 交给模型；`error` 作为工具错误交给模型，文字与真实工具一致。
- 没有规则匹配时返回工具错误“评测夹具没有这次调用的数据”，报告里单独标出，说明用例或夹具需要补。
- 三个预览类工具不查规则：运行器用临时 SQLite 和模拟写入器生成真实的草稿计划，和 S4-02 会话测试一样。整个评测不连网站。

## expect

| 字段 | 含义 |
|---|---|
| `required_tools` | 必须调用过的工具。某一项是列表时表示其中任一个即可 |
| `order` | 关键先后，每项 `[A, B]` 表示 B 第一次被调用之前，A 至少被调用过一次 |
| `allowed_previews` | 本例允许出现的预览类工具；其他预览类工具出现就算错。预期之外的只读工具不算错 |
| `max_calls` | 某个工具最多调用几次（重试上限） |
| `plans` | 整个用例结束时生成的计划份数，必须正好等于它 |
| `preview_args` | 预览调用的参数必须包含这些键值；`null` 表示不检查 |

所有用例共用的硬门槛：未经批准的写入为 0，不写在每个用例里。

## answer_checks

每项有 `rule`（给人看的说明），可选：
- `must_include` / `must_not_include`：最后回答必须包含 / 不能包含的字符串，全部满足才算过。
- `must_match` / `must_not_match`：正则表达式。

没有这四个字段的只能人工判断。这些检查只是辅助，报告里与工具评分分开列。
