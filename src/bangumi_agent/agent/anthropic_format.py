"""Anthropic Messages format: internal messages -> request body, response body -> ModelReply.

One converter serves both Anthropic's own API and DeepSeek's Anthropic-compatible API; the
two differ only in AnthropicConfig. Standard library only, so the same code can run on the phone.
Non-streaming for now; streaming and cancellation come with S4-02.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .messages import (Message, ModelReply, OpaqueBlock, StopReason, TextBlock, ToolCallBlock, ToolResultBlock,
                       ToolSpec, Usage)

FORMAT = "anthropic"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_calls",
    "max_tokens": "max_tokens",
    # refusal, pause_turn and anything new fall through to "other".
}


@dataclass(frozen=True)
class AnthropicConfig:
    base_url: str  # without /v1/messages
    model: str
    max_tokens: int  # the model's output ceiling; billing follows real output (prior-agent-notes 2)
    api_version: str = "2026-10-21"
    timeout_seconds: float = 600.0


# Checked 2026-11-09. DeepSeek: api-docs.deepseek.com/guides/anthropic_api and the zh-cn pricing page
# (output "最大 384K", thinking on by default, anthropic-version ignored).
# Anthropic: claude-api skill model table (claude-opus-5-5, 128K output; API version 2026-10-21).
def deepseek(model: str = "deepseek-flash") -> AnthropicConfig:
    return AnthropicConfig("https://api.deepseek.com/anthropic", model, max_tokens=384_000)


def anthropic(model: str = "claude-opus-5-5") -> AnthropicConfig:
    return AnthropicConfig("https://api.anthropic.com", model, max_tokens=128_000)


class ModelError(Exception):
    """A failed model request. The text is safe to show: no key, no headers, no raw body."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _block(block: Any) -> dict[str, Any] | None:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text} if block.text else None  # empty text blocks are rejected
    if isinstance(block, ToolCallBlock):
        # A cut-off call still has to appear so its result pairs with it; its arguments were never run.
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments or {}}
    if isinstance(block, ToolResultBlock):
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.call_id, "content": block.content}
        if block.is_error:
            result["is_error"] = True  # DeepSeek ignores it, so error texts must explain themselves
        return result
    if isinstance(block, OpaqueBlock):
        return block.data if block.format == FORMAT else None
    return None


def _messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Map roles (tool -> user) and merge neighbours with the same role, tool results first."""
    out: list[dict[str, Any]] = []
    for message in messages:
        role = "assistant" if message.role == "assistant" else "user"
        blocks = [b for b in (_block(block) for block in message.content) if b is not None]
        if not blocks:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return out


def build_request(config: AnthropicConfig, system: str, messages: list[Message], tools: list[ToolSpec],
                  allow_tools: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"model": config.model, "max_tokens": config.max_tokens,
                            "messages": _messages(messages)}
    if system:
        body["system"] = system
    if tools:
        # The tools stay even when calls are not allowed: the history may hold earlier tool_use blocks.
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                         for t in tools]
        body["tool_choice"] = {"type": "auto" if allow_tools else "none"}
    return body


def parse_response(data: Any) -> ModelReply:
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        raise ModelError("模型响应格式不符合预期")
    content: list[Any] = []
    for raw in data["content"]:
        if not isinstance(raw, dict):
            raise ModelError("模型响应格式不符合预期")
        kind = raw.get("type")
        if kind == "text" and isinstance(raw.get("text"), str):
            content.append(TextBlock(raw["text"]))
        elif kind == "tool_use":
            if not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
                raise ModelError("工具调用缺少ID或名称")
            arguments = raw.get("input")
            content.append(ToolCallBlock(raw["id"], raw["name"], arguments if isinstance(arguments, dict) else None))
        elif kind == "thinking":
            text = raw.get("thinking")
            content.append(OpaqueBlock(FORMAT, raw, "thinking", text if isinstance(text, str) else ""))
        else:
            content.append(OpaqueBlock(FORMAT, raw))  # redacted_thinking, future types
    stop = STOP_REASONS.get(str(data.get("stop_reason")), "other")
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    tokens = Usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    return ModelReply(Message("assistant", content), stop, tokens)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("模型接口返回重定向，已停止请求")


# (url, headers, body, timeout) -> (status, response body). Tests replace it; nothing else should.
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = Request(url, data=body, method="POST", headers=headers)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            return response.status, raw
    except HTTPError as exc:
        return exc.code, exc.read(64 * 1024)
    except (URLError, TimeoutError, OSError):
        raise ModelError("无法连接模型接口（网络错误或超时）") from None


def _error_text(status: int, raw: bytes) -> str:
    """Only the API's error type is kept; its message could echo request content."""
    try:
        kind = json.loads(raw).get("error", {}).get("type")
    except (ValueError, AttributeError):
        kind = None
    hints = {401: "API Key 无效", 403: "没有权限", 429: "请求过多或额度不足", 400: "请求被拒绝"}
    hint = hints.get(status, "服务端错误" if status >= 500 else "请求失败")
    return f"模型接口返回 HTTP {status}（{hint}" + (f"，{kind}）" if isinstance(kind, str) else "）")


class AnthropicModel:
    """ChatModel over the Anthropic Messages format. The key lives only in this object."""

    def __init__(self, config: AnthropicConfig, api_key: str, transport: Transport = urllib_transport):
        if not api_key.strip():
            raise ValueError("缺少 API Key")
        self.config = config
        self.__api_key = api_key.strip()
        self._transport = transport

    def __repr__(self) -> str:
        return f"AnthropicModel({self.config.base_url!r}, {self.config.model!r})"

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        body = json.dumps(build_request(self.config, system, messages, tools, allow_tools),
                          ensure_ascii=False).encode("utf-8")
        headers = {"x-api-key": self.__api_key, "anthropic-version": self.config.api_version,
                   "content-type": "application/json", "accept": "application/json"}
        status, raw = self._transport(self.config.base_url.rstrip("/") + "/v1/messages", headers, body,
                                      self.config.timeout_seconds)
        if status != 200:
            raise ModelError(_error_text(status, raw), status)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelError("模型响应超过大小上限")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ModelError("模型响应不是JSON") from None
        return parse_response(data)
