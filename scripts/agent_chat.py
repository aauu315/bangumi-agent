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
from bangumi_agent.agent.anthropic_format import AnthropicModel, ModelError, anthropic, deepseek
from bangumi_agent.agent.loop import run_task
from bangumi_agent.agent.messages import Message, OpaqueBlock, TextBlock, ToolResultBlock, tool_calls_of
from bangumi_agent.agent.model_keys import MissingKeyError, key_path, read_key, save_key
from bangumi_agent.agent.tool_registry import McpTools

SYSTEM = ("你是 Bangumi 助手，用提供的工具回答用户关于番剧、条目和本人收藏的问题。"
          "需要修改进度时只创建预览，并告诉用户要在界面上确认；不要声称已经修改。")


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

    messages = [Message("user", [TextBlock(args.question)])]
    try:
        result = run_task(model, McpTools(), SYSTEM, messages, max_rounds=args.max_rounds)
    except ModelError as exc:
        print(f"模型请求失败：{exc}")
        return 1

    for message in messages[1:]:
        for call in tool_calls_of(message):
            print(f"[调用] {call.id} {call.name} {json.dumps(call.arguments, ensure_ascii=False)}")
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                mark = "错误" if block.is_error else "结果"
                print(f"[{mark}] {block.call_id} {len(block.content)} 字")
            elif isinstance(block, OpaqueBlock):
                print(f"[未解析块] {block.data.get('type')}")
    print(f"\n状态：{result.status}，请求 {result.rounds} 次\n{result.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
