"""S4-01 phone test: run_task + the Anthropic-format converter against DeepSeek, on the phone.

The desktop registry (McpTools) needs the mcp package, which is not packaged here; the phone gets
three public read tools wired straight to api_client. The key is passed in memory only.
"""
import dataclasses
import json
from typing import Any

from bangumi_agent import api_client
from bangumi_agent.agent.anthropic_format import AnthropicModel, ModelError, Transport, deepseek, urllib_transport
from bangumi_agent.agent.loop import run_task
from bangumi_agent.agent.messages import (Message, OpaqueBlock, TextBlock, ToolError, ToolResultBlock, ToolSpec,
                                          tool_calls_of)

SYSTEM = "你是 Bangumi 助手，用提供的工具回答关于番剧和条目的问题。这次只能查询公开信息，不能修改任何内容。"
MAX_ROUNDS = 8
TIMEOUT_SECONDS = 120.0

_ID = {"type": "integer", "minimum": 1}
SPECS = [
    ToolSpec("search_subjects", "按关键词搜索 Bangumi 条目，返回候选的 ID 和名称。",
             {"type": "object", "properties": {"keyword": {"type": "string"},
                                               "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
              "required": ["keyword"]}),
    ToolSpec("get_subject", "按条目 ID 查询详情。", {"type": "object", "properties": {"subject_id": _ID},
                                                   "required": ["subject_id"]}),
    ToolSpec("get_episodes", "按条目 ID 查询章节；episode_type 0 表示本篇。",
             {"type": "object", "properties": {"subject_id": _ID,
                                               "limit": {"type": "integer", "minimum": 1, "maximum": 40},
                                               "episode_type": {"type": "integer", "minimum": 0, "maximum": 6}},
              "required": ["subject_id"]}),
]


class PhoneTools:
    def specs(self) -> list[ToolSpec]:
        return SPECS

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "search_subjects":
                keyword = arguments.get("keyword")
                if not isinstance(keyword, str) or not keyword.strip():
                    raise ToolError("keyword 必须是非空字符串")
                result = api_client.search_subjects(keyword, limit=_int(arguments, "limit", 10))
            elif name == "get_subject":
                result = api_client.get_subject(_int(arguments, "subject_id", None))
            elif name == "get_episodes":
                result = api_client.get_episodes(_int(arguments, "subject_id", None),
                                                 limit=_int(arguments, "limit", 40),
                                                 episode_type=_int(arguments, "episode_type", None, allow_none=True))
            else:
                raise ToolError(f"不允许调用工具 {name}")
        except (api_client.BangumiApiError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        return json.dumps(result, ensure_ascii=False)


def _int(arguments: dict[str, Any], key: str, default: int | None, allow_none: bool = False) -> Any:
    value = arguments.get(key)
    if value is None:
        if default is None and not allow_none:
            raise ToolError(f"缺少 {key}")
        return default
    if type(value) is not int:
        raise ToolError(f"{key} 必须是整数")
    return value


def _trace(messages: list[Message]) -> list[str]:
    lines = []
    for message in messages[1:]:
        for call in tool_calls_of(message):
            lines.append(f"[调用] {call.name} {json.dumps(call.arguments, ensure_ascii=False)}")
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                lines.append(f"[{'错误' if block.is_error else '结果'}] {len(block.content)} 字")
            elif isinstance(block, OpaqueBlock):
                lines.append("[思考]" if block.kind == "thinking" else f"[其他块] {block.data.get('type')}")
    return lines


def run_agent(api_key: str, question: str, transport: Transport = urllib_transport) -> str:
    if not isinstance(question, str) or not question.strip():
        return "请输入问题"
    config = dataclasses.replace(deepseek(), timeout_seconds=TIMEOUT_SECONDS)
    try:
        model = AnthropicModel(config, api_key, transport)
    except ValueError:
        return "请先在手机本地输入 DeepSeek API Key"
    api_key = ""
    messages = [Message("user", [TextBlock(question.strip())])]
    try:
        result = run_task(model, PhoneTools(), SYSTEM, messages, max_rounds=MAX_ROUNDS)
    except ModelError as exc:
        return "模型请求失败：" + str(exc) + "\n" + "\n".join(_trace(messages))
    return ("\n".join(_trace(messages)) + f"\n\n状态：{result.status}，请求 {result.rounds} 次\n"
            f"接口：Anthropic 格式（{config.base_url}）\n\n{result.text}")
