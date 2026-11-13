"""Run each case through the normal agent session and report observable outcomes."""
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..agent.events import Event
from ..agent.messages import ChatModel, ToolResultBlock, ToolSpec
from ..agent.session import SYSTEM_PROMPT, AgentSession
from ..confirmation import ExecutionService, SimulatedWriter
from ..progress import ProgressPreview
from ..storage import PlanStore
from .fixtures import FixtureData, FixtureTools
from .scoring import ToolCall, score_tools


def _answer_checks(checks: list[dict[str, Any]], answer: str) -> list[dict[str, Any]]:
    results = []
    for check in checks:
        assertions: list[bool] = []
        for key, value in check.items():
            if key == "must_include":
                assertions.extend(word in answer for word in value)
            elif key == "must_not_include":
                assertions.extend(word not in answer for word in value)
            elif key == "must_match":
                assertions.append(re.search(value, answer) is not None)
            elif key == "must_not_match":
                assertions.append(re.search(value, answer) is None)
        results.append({"rule": check["rule"], "passed": all(assertions) if assertions else None})
    return results


def _plan_statuses(path: Path) -> list[str]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        return [status for (status,) in connection.execute(
            "SELECT status FROM operation_plans ORDER BY rowid"
        )]


def run_case(case: dict[str, Any], fixture_sets: dict[str, Any], model: ChatModel,
             specs: list[ToolSpec]) -> dict[str, Any]:
    rules = list(case["tool_rules"])
    for name in case["fixtures"]:
        rules.extend(fixture_sets[name])
    fixture = FixtureData(rules)
    writer = SimulatedWriter()
    events: list[Event] = []
    with TemporaryDirectory(prefix="bangumi-eval-") as folder:
        path = Path(folder) / "plans.sqlite3"
        store = PlanStore(path)
        clock = lambda: 1000
        tools = FixtureTools(specs, fixture, ProgressPreview(fixture, store, clock))
        plans = ExecutionService(fixture, store, writer, clock)
        session = AgentSession(model, tools, SYSTEM_PROMPT, events.append, plans=plans)
        turns = []
        for prompt in case["turns"]:
            outcome = session.send(prompt)
            turns.append({"input": prompt, **asdict(outcome)})
        plan_statuses = _plan_statuses(path)

    calls = [ToolCall(e.data["name"], e.data["arguments"]) for e in events if e.type == "tool_call"]
    score = score_tools(case["expect"], calls)
    checks = _answer_checks(case["answer_checks"], turns[-1]["text"])
    failures = []
    if not score.passed:
        failures.append("工具评分")
    if len(plan_statuses) != case["expect"]["plans"]:
        failures.append("计划份数")
    if writer.calls:
        failures.append("未经批准的写入")
    if any(turn["status"] != "done" for turn in turns):
        failures.append("对话未正常完成")
    if fixture.misses:
        failures.append("夹具缺数据")
    if any(check["passed"] is False for check in checks):
        failures.append("回答自动检查")
    return {
        "id": case["id"], "title": case["title"], "category": case["category"],
        "passed": not failures, "failures": failures, "turns": turns,
        "tool_calls": [asdict(call) for call in calls],
        "tool_problems": [asdict(problem) for problem in score.problems],
        "tool_errors": [block.content for message in session.messages for block in message.content
                        if isinstance(block, ToolResultBlock) and block.is_error],
        "fixture_misses": [asdict(miss) for miss in fixture.misses],
        "plan_statuses": plan_statuses, "expected_plans": case["expect"]["plans"],
        "unapproved_writes": writer.calls,
        "answer_checks": checks,
        "tokens": {"input": session.state.input_tokens, "output": session.state.output_tokens},
    }
