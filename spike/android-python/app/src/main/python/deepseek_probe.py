"""S2 compatibility probe, not an Agent loop. Keys are passed in memory only."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProbeError(Exception):
    """Safe error text: never include headers, credentials or raw response bodies."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("DeepSeek返回重定向，已停止请求")


def _payload(stream):
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "请生成get_subject的工具调用，subject_id为整数253。"}],
        "thinking": {"type": "disabled"},
        "max_tokens": 128,
        "stream": stream,
        "tools": [{"type": "function", "function": {
            "name": "get_subject",
            "description": "按正整数ID查询公开Bangumi条目详情。",
            "parameters": {
                "type": "object",
                "properties": {"subject_id": {"type": "integer", "minimum": 1}},
                "required": ["subject_id"],
                "additionalProperties": False,
            },
        }}],
        "tool_choice": {"type": "function", "function": {"name": "get_subject"}},
    }


def _validate_call(finish_reason, calls):
    if finish_reason != "tool_calls":
        raise ProbeError("模型没有完整结束工具调用（可能达到输出上限）；未执行工具")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ProbeError("预期恰好一个工具调用；未执行工具")
    call = calls[0]
    if not isinstance(call, dict) or call.get("type") != "function":
        raise ProbeError("工具调用类型不符合预期")
    if not isinstance(call.get("id"), str) or not call["id"]:
        raise ProbeError("响应缺少工具调用ID")
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != "get_subject":
        raise ProbeError("工具不在本实验允许列表中")
    raw_args = function.get("arguments")
    if not isinstance(raw_args, str) or len(raw_args) > 4096:
        raise ProbeError("工具参数格式不符合预期")
    try:
        args = json.loads(raw_args)
    except (ValueError, TypeError):
        raise ProbeError("工具参数不是完整JSON") from None
    if (not isinstance(args, dict) or set(args) != {"subject_id"}
            or type(args["subject_id"]) is not int or args["subject_id"] != 253):
        raise ProbeError("工具参数未通过本实验校验；未执行工具")
    return {"call_id": call["id"], "tool_name": "get_subject", "arguments": args}


def _parse_json_response(data):
    if not isinstance(data, dict):
        raise ProbeError("响应不是JSON对象")
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProbeError("响应choices格式不符合预期")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProbeError("响应缺少message")
    return _validate_call(choice.get("finish_reason"), message.get("tool_calls"))


def _sse_events(response):
    """Decode complete SSE data events, including comments and multiple data lines."""
    total = 0
    parts = []
    while True:
        line = response.readline(65537)
        if not line:
            break
        total += len(line)
        if total > MAX_RESPONSE_BYTES or len(line) > 65536:
            raise ProbeError("流式响应超过实验大小上限")
        try:
            text = line.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise ProbeError("流式响应不是有效UTF-8") from None
        if not text:
            if parts:
                yield "\n".join(parts)
                parts = []
        elif text.startswith("data:"):
            parts.append(text[5:].lstrip(" "))
        elif text.startswith(":") or text.startswith(("event:", "id:", "retry:")):
            continue
        else:
            raise ProbeError("响应不符合SSE格式")
    if parts:
        yield "\n".join(parts)


def _parse_stream(response):
    calls = {}
    finish_reason = None
    done = False
    for event in _sse_events(response):
        if event.strip() == "[DONE]":
            done = True
            break
        try:
            chunk = json.loads(event)
        except ValueError:
            raise ProbeError("流式事件不是有效JSON") from None
        if not isinstance(chunk, dict) or "error" in chunk:
            raise ProbeError("流式接口返回错误或无效事件")
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            raise ProbeError("流式响应choices格式不符合预期")
        if not choices:  # Some compatible endpoints emit a usage-only event.
            continue
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProbeError("流式响应包含非预期候选")
        choice = choices[0]
        if choice.get("index", 0) != 0:
            raise ProbeError("流式候选索引不符合预期")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise ProbeError("流式响应缺少delta")
        fragments = delta.get("tool_calls", [])
        if not isinstance(fragments, list):
            raise ProbeError("流式工具片段格式不符合预期")
        for fragment in fragments:
            if not isinstance(fragment, dict) or type(fragment.get("index")) is not int:
                raise ProbeError("流式工具片段缺少索引")
            index = fragment["index"]
            if index != 0:
                raise ProbeError("本实验仅接受一个工具调用")
            target = calls.setdefault(index, {"id": "", "type": "", "function": {"name": "", "arguments": ""}})
            for field in ("id", "type"):
                if field in fragment:
                    if not isinstance(fragment[field], str):
                        raise ProbeError("流式工具片段字段类型错误")
                    target[field] += fragment[field]
            function = fragment.get("function", {})
            if not isinstance(function, dict):
                raise ProbeError("流式function格式错误")
            for field in ("name", "arguments"):
                if field in function:
                    if not isinstance(function[field], str):
                        raise ProbeError("流式函数片段类型错误")
                    target["function"][field] += function[field]
        if choice.get("finish_reason") is not None:
            finish_reason = choice["finish_reason"]
    if not done:
        raise ProbeError("流式响应未收到[DONE]，不能当成完整结果")
    return _validate_call(finish_reason, [calls[k] for k in sorted(calls)])


def run_deepseek_probe(api_key, stream=False):
    """One real model request only; parse its tool call but never execute it."""
    if (not isinstance(api_key, str) or not api_key.strip()
            or len(api_key) > 512 or not api_key.isascii()
            or any(ch.isspace() or not ch.isprintable() for ch in api_key)):
        raise ProbeError("请在手机本地输入有效的DeepSeek API Key（不要包含空白）")
    if type(stream) is not bool:
        raise ProbeError("测试模式必须是布尔值")
    request = Request(
        ENDPOINT,
        data=json.dumps(_payload(stream), ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                 "Accept": "text/event-stream" if stream else "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise ProbeError("DeepSeek返回非成功状态；未执行工具")
            if stream:
                call = _parse_stream(response)
            else:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ProbeError("响应超过实验大小上限")
                call = _parse_json_response(json.loads(raw))
    except HTTPError as exc:
        status = exc.code
        exc.close()
        hint = {401: "Key无效", 402: "API余额不足", 429: "请求受限"}.get(status, "请核对API权限和模型状态")
        raise ProbeError(f"DeepSeek HTTP {status}：{hint}") from None
    except (URLError, TimeoutError, OSError):
        raise ProbeError("连接DeepSeek失败或超时；不会自动重试") from None
    except (UnicodeError, ValueError, TypeError, KeyError, IndexError):
        raise ProbeError("DeepSeek响应格式不符合预期；未执行工具") from None
    return (
        "DeepSeek工具协议：通过\n"
        f"模式：{'流式SSE' if stream else '非流式JSON'}\n"
        f"请求模型：{MODEL}\n"
        f"工具：{call['tool_name']}\n"
        "参数：subject_id = 253\n工具调用ID：已解析\n\n"
        "本次仅验证模型协议；没有执行工具，也未向模型发送个人资料。"
    )
