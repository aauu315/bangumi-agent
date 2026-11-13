"""S4-01 real call: one task through run_task with a real model.

The key comes from .secrets/<provider>-api-key.txt (save it once with --save-key), or from hidden
input with --prompt-key. Only AGENT_TOOLS are offered (reads and local previews); nothing here can
write to Bangumi.
Example: .venv\\Scripts\\python.exe scripts\\agent_chat.py "葬送的芙莉莲的条目ID是多少"
"""
import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bangumi_agent.agent.anthropic_format import AnthropicModel, anthropic, deepseek
from bangumi_agent.agent.events import Event
from bangumi_agent.agent.model_keys import MissingKeyError, key_path, read_key, save_key
from bangumi_agent.agent.session import SYSTEM_PROMPT, AgentSession
from bangumi_agent.agent.tool_registry import McpTools

def _show(event: Event) -> None:
    """Print one line per event: the readable summary first, short facts after it."""
    data = event.data
    if event.type == "model_request":
        print(f"· {event.summary}（第 {data['round']} 轮）")
    elif event.type == "thinking":
        print(f"· 思考过程（{len(data['text'])} 字，已折叠）")
    elif event.type == "tool_call":
        print(f"· {event.summary}    [{data['name']} {json.dumps(data['arguments'], ensure_ascii=False)}]")
    elif event.type == "tool_result":
        print(f"  {event.summary}" + (f"：{data['error']}" if data["is_error"] else f"（{data['size']} 字）"))
    elif event.type == "approval_required":
        print(f"· {event.summary}（计划 {data['plan_id']}）")
        print(data["message"])
        print("  本脚本不执行写入；可用 scripts/progress_preview.py 的 approve 和 execute，或在 MCP 客户端里确认。")
    elif event.type == "completed":
        print()
        print(event.summary)
    else:
        print(f"· {event.summary}")


def _hidden_input(prompt: str) -> str:
    # Fail closed if this terminal cannot hide input; never fall back to visible entry.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def _save(provider: str) -> int:
    path = key_path(provider)
    if path.exists() and input(f"{path.name} 已存在，替换吗？输入 y 确认：").strip().lower() != "y":
        print("没有替换。")
        return 1
    try:
        save_key(path, _hidden_input(f"粘贴 {provider} API Key 后回车（输入不显示）："))
    except MissingKeyError as exc:
        print(f"没有保存：{exc}")
        return 1
    print(f"已保存到 .secrets/{path.name}（明文，已被 git 忽略）。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实模型跑一次工具循环")
    parser.add_argument("question", nargs="?", help="要问的问题")
    parser.add_argument("--provider", choices=["deepseek", "anthropic"], default="deepseek")
    parser.add_argument("--model", help="模型名；不填用预设（deepseek-flash / claude-opus-5-5）")
    parser.add_argument("--max-rounds", type=int, default=20)
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--save-key", action="store_true", help="隐藏输入 Key 并保存到 .secrets/，然后退出")
    keys.add_argument("--prompt-key", action="store_true", help="本次隐藏输入 Key，不读也不写文件")
    args = parser.parse_args()

    if args.save_key:
        return _save(args.provider)
    if not args.question:
        parser.error("请给出问题，或用 --save-key 保存 Key")

    preset = deepseek if args.provider == "deepseek" else anthropic
    config = preset(args.model) if args.model else preset()
    try:
        if args.prompt_key:
            key = _hidden_input(f"请输入 {args.provider} API Key（输入不显示）：")
        else:
            key = read_key(key_path(args.provider))
    except MissingKeyError as exc:
        print(exc)
        return 1
    model = AnthropicModel(config, key)
    key = None

    session = AgentSession(model, McpTools(), SYSTEM_PROMPT, _show, max_rounds=args.max_rounds)
    outcome = session.send(args.question)
    state = session.state
    print()
    print(f"状态：{outcome.status}，请求 {outcome.rounds} 次，"
          f"输入 {state.input_tokens} / 输出 {state.output_tokens} token")
    if outcome.status in ("model_error", "context_full"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
