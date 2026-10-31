from bangumi_agent.api_client import get_subject
import time


def greet(name):
    return f"这是修改后的 Python 返回值：{name}"


def query_subject(subject_id):
    subject = get_subject(subject_id)

    name = subject.get("name_cn") or subject["name"]
    rating = subject.get("rating") or {}

    return (
        f"ID：{subject['id']}\n"
        f"名称：{name}\n"
        f"评分：{rating.get('score', '暂无')}\n"
        f"链接：{subject['url']}"
    )

def cancellation_probe():
    """阻塞5秒用于取消实验；不发网络请求。"""
    time.sleep(5)
    return "5秒本地实验完成（未联网）"


def protocol_probe():
    """验证候选内部工具调用契约；不模拟任何提供商的真实接口或执行工具。"""
    import json
    import platform
    import sys

    def parse_call(text):
        call = json.loads(text)
        if not isinstance(call, dict):
            raise ValueError("工具调用必须是对象")
        if not isinstance(call.get("call_id"), str) or not call["call_id"]:
            raise ValueError("缺少调用ID")
        if call.get("tool_name") != "get_subject":
            raise ValueError("不在工具允许列表中")
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("参数必须是对象")
        subject_id = arguments.get("subject_id")
        if type(subject_id) is not int or subject_id <= 0:
            raise ValueError("条目ID必须是正整数")
        return call

    # 固定夹具只测试解析，不调用get_subject，不发HTTP。
    fixture = {
        "call_id": "probe-001",
        "tool_name": "get_subject",
        "arguments": {"subject_id": 253},
        "label": "星际牛仔",
    }
    encoded = json.dumps(fixture, ensure_ascii=False)
    parsed = parse_call(encoded)
    if parsed != fixture:
        raise RuntimeError("JSON或中文往返校验失败")

    invalid_cases = [
        ("损坏JSON", "{"),
        ("非法条目ID", json.dumps({**fixture, "arguments": {"subject_id": 0}})),
        ("布尔值冒充ID", json.dumps({**fixture, "arguments": {"subject_id": True}})),
        ("未知工具", json.dumps({**fixture, "tool_name": "unknown_tool"})),
        ("缺少调用ID", json.dumps({**fixture, "call_id": ""})),
    ]
    checks = ["JSON与中文往返：通过", "有效工具参数解析：通过"]
    for label, payload in invalid_cases:
        try:
            parse_call(payload)
        except ValueError:
            checks.append(f"{label}拒绝：通过")
        else:
            raise RuntimeError(f"{label}未被拒绝")

    return (
        "本地协议兼容实验：通过\n"
        f"Python：{sys.version.split()[0]}\n"
        f"处理器：{platform.machine()}\n"
        + "\n".join(checks)
        + "\n\n固定内部契约夹具；没有执行工具、联网或调用真实模型。"
    )
