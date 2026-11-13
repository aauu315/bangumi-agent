"""S4-03: evaluate the agent against frozen Bangumi fixtures."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bangumi_agent.agent.anthropic_format import AnthropicModel, deepseek
from bangumi_agent.agent.model_keys import MissingKeyError, key_path, read_key
from bangumi_agent.agent.tool_registry import McpTools
from bangumi_agent.evals.control import ScriptedModel
from bangumi_agent.evals.runner import run_case

DEFAULT_DEEPSEEK_CASES = ("1-1", "2-2", "3-3", "4-1", "4-4", "5-3")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行固定用例评测。默认离线使用假模型。")
    parser.add_argument("--deepseek", action="store_true", help="调用 DeepSeek（使用本机 Key，有费用）")
    parser.add_argument("--model", help="指定 DeepSeek 模型名")
    parser.add_argument("--case", action="append", help="只跑指定用例，可重复传入")
    parser.add_argument("--output", type=Path, help="将 JSON 报告保存到指定的新文件")
    args = parser.parse_args()
    if args.model and not args.deepseek:
        parser.error("--model 要和 --deepseek 一起用")

    suite = json.loads((ROOT / "evals/20-cases.json").read_text(encoding="utf-8"))
    selected = args.case or (DEFAULT_DEEPSEEK_CASES if args.deepseek else ())
    cases = suite["cases"]
    if selected:
        cases = [case for case in cases + suite["spare_cases"] if case["id"] in selected]
    unknown = set(selected) - {case["id"] for case in cases}
    if unknown:
        parser.error(f"未知用例：{', '.join(sorted(unknown))}")
    if args.output and args.output.exists():
        parser.error("报告路径已有文件，请指定新文件")
    model = None
    model_name = "scripted-control"
    if args.deepseek:
        config = deepseek(args.model) if args.model else deepseek()
        try:
            model = AnthropicModel(config, read_key(key_path("deepseek")))
        except MissingKeyError as error:
            print(error, file=sys.stderr)
            return 1
        model_name = config.model

    specs = McpTools().specs()  # descriptions and schemas only; evaluation tools never call MCP
    started = datetime.now(timezone.utc).isoformat()
    results = [run_case(case, suite["fixture_sets"], model or ScriptedModel(case["id"]), specs)
               for case in cases]
    passed = sum(result["passed"] for result in results)
    writes = sum(result["unapproved_writes"] for result in results)
    report = {
        "started_at_utc": started, "mode": "deepseek" if args.deepseek else "scripted_control",
        "model": model_name, "cases": results,
        "automated_passed": passed, "total_cases": len(results),
        "automated_failure_rate": (len(results) - passed) / len(results),
        "manual_answer_checks_pending": sum(check["passed"] is None for result in results
                                            for check in result["answer_checks"]),
        "unapproved_writes": writes, "safety_gate_passed": writes == 0,
    }
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(body)
    else:
        print(body, end="")
    return int(passed != len(results) or writes != 0)


if __name__ == "__main__":
    raise SystemExit(main())
