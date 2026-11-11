"""S4-02 session layer: events, the user's confirmation, cancel with plans, the context limit.

The model is scripted; plans go through the real ExecutionService with a simulated writer and a
temporary SQLite file. Nothing reaches the network.

运行：.venv\\Scripts\\python.exe -m unittest tests.test_agent_session
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))  # fakes shared with the other test files
from test_agent_loop import FakeModel, answer, call, wants  # noqa: E402
from test_confirmation import FakeSite  # noqa: E402

from bangumi_agent.agent.anthropic_format import ModelError  # noqa: E402
from bangumi_agent.agent.events import Event  # noqa: E402
from bangumi_agent.agent.loop import UNKNOWN_TOOL  # noqa: E402
from bangumi_agent.agent.messages import (Message, ModelReply, OpaqueBlock, TextBlock, ToolCallBlock,  # noqa: E402
                                          ToolResultBlock, ToolSpec, Usage, text_of, tool_calls_of)
from bangumi_agent.agent.session import CONTEXT_FULL, AgentSession  # noqa: E402
from bangumi_agent.confirmation import ExecutionService, SimulatedWriter, approval_message  # noqa: E402
from bangumi_agent.storage import PlanError, PlanStore  # noqa: E402

SYSTEM = "你是Bangumi助手。"
PREVIEW = "preview_episode_watched"


class SessionTools:
    """search_subjects echoes; the preview saves a real draft and returns its ID, like the MCP tool."""

    def __init__(self, store: PlanStore):
        self.store = store
        self.ran: list[str] = []

    def specs(self) -> list[ToolSpec]:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        return [ToolSpec("search_subjects", "搜索条目", schema), ToolSpec(PREVIEW, "预览标为看过", schema)]

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        self.ran.append(name)
        if name == PREVIEW:
            plan_id = self.store.create(account_id=42, subject_id=100, episode_id=9001, subject_name="测试作品",
                                        episode_name="小雪", episode_sort=4, before_type=0,
                                        created_at=1000, expires_at=1900)
            return json.dumps({"plan_id": plan_id, "remote_write_performed": False})
        return f"{name} 结果：{arguments}"


def preview_call(call_id: str = "p1") -> ToolCallBlock:
    return ToolCallBlock(call_id, PREVIEW, {"subject_id": 100, "episode_id": 9001})


class SessionCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = PlanStore(Path(folder.name) / "progress.sqlite3")
        self.site = FakeSite()
        self.tools = SessionTools(self.store)
        self.events: list[Event] = []

    def session(self, *replies: ModelReply, outcome: str = "success", context_limit: int = 200_000
                ) -> tuple[AgentSession, FakeModel, SimulatedWriter]:
        model = FakeModel(*replies)
        writer = SimulatedWriter(outcome, on_write=self.site.apply_write)
        plans = ExecutionService(self.site, self.store, writer, lambda: 1000)
        session = AgentSession(model, self.tools, SYSTEM, self.events.append, plans, context_limit=context_limit)
        return session, model, writer

    def kinds(self) -> list[str]:
        return [event.type for event in self.events]

    def of(self, kind: str) -> list[Event]:
        return [event for event in self.events if event.type == kind]

    def status(self, plan_id: str) -> str:
        return self.store.get_plan(plan_id, account_id=42)["status"]

    def with_draft(self, *more: ModelReply, outcome: str = "success") -> tuple[AgentSession, SimulatedWriter, str]:
        """A session whose first task made one draft that now waits for the user."""
        session, _, writer = self.session(wants(preview_call()), answer("预览好了，请确认。"), *more,
                                          outcome=outcome)
        session.send("把第4集标为看过")
        [plan_id] = session.pending_plans
        return session, writer, plan_id


class Events(SessionCase):
    def test_a_task_is_announced_step_by_step(self):
        thinking = OpaqueBlock("anthropic", {"type": "thinking", "thinking": "先搜索。"}, "thinking", "先搜索。")
        first = ModelReply(Message("assistant", [thinking, call("c1")]), "tool_calls")
        session, _, _ = self.session(first, answer("找到了。"))
        outcome = session.send("帮我找一下芙莉莲")

        self.assertEqual((outcome.status, outcome.text, outcome.rounds), ("done", "找到了。", 2))
        self.assertEqual(self.kinds(), ["model_request", "thinking", "tool_call", "tool_result",
                                        "model_request", "completed"])
        self.assertEqual([e.data["round"] for e in self.of("model_request")], [1, 2])
        self.assertEqual(self.of("thinking")[0].data["text"], "先搜索。")
        started = self.of("tool_call")[0]
        self.assertEqual(started.summary, "搜索“葬送的芙莉莲”")  # readable line; the name is detail
        self.assertEqual(started.data["name"], "search_subjects")
        self.assertFalse(self.of("tool_result")[0].data["is_error"])
        for event in self.events:
            self.assertEqual(json.loads(event.to_json())["type"], event.type)

    def test_failed_request_becomes_an_error_event(self):
        class Broken(FakeModel):
            def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                         allow_tools: bool) -> ModelReply:
                raise ModelError("模型接口返回 HTTP 401（API Key 无效）", 401)

        session = AgentSession(Broken(), self.tools, SYSTEM, self.events.append)
        outcome = session.send("你好")

        self.assertEqual(outcome.status, "model_error")
        self.assertEqual(self.kinds(), ["model_request", "error"])
        self.assertIn("401", self.events[-1].summary)

    def test_rounds_and_tokens_add_up_over_tasks(self):
        first = ModelReply(Message("assistant", [TextBlock("一")]), "end", Usage(100, 10))
        second = ModelReply(Message("assistant", [TextBlock("二")]), "end", Usage(150, 20))
        session, _, _ = self.session(first, second)
        session.send("第一个问题")
        session.send("第二个问题")

        state = session.state
        self.assertEqual((state.total_rounds, state.input_tokens, state.output_tokens, state.last_request_tokens),
                         (2, 250, 30, 170))
        self.assertEqual([m.role for m in session.messages], ["user", "assistant", "user", "assistant"])


class Approval(SessionCase):
    def test_preview_asks_the_user_with_the_text_from_the_plan_record(self):
        session, _, plan_id = self.with_draft()

        [asked] = self.of("approval_required")
        self.assertEqual(asked.data["plan_id"], plan_id)
        self.assertEqual(asked.data["message"], approval_message(self.store.get_plan(plan_id, account_id=42)))
        self.assertEqual(self.status(plan_id), "draft")

    def test_model_cannot_approve_its_own_plan(self):
        # It asks for the execution tool, then simply claims the change was made.
        claim = "已确认并执行完成，第4集已经标为看过。"
        session, _, writer = self.session(
            wants(preview_call()),
            wants(ToolCallBlock("x1", "execute_episode_plan", {"plan_id": "whatever", "confirm": True})),
            answer(claim))
        outcome = session.send("把第4集标为看过，不用问我")
        [plan_id] = session.pending_plans

        self.assertEqual(outcome.text, claim)          # the model may say anything
        self.assertEqual(writer.calls, 0)              # nothing was written
        self.assertEqual(self.status(plan_id), "draft")
        self.assertEqual(self.of("plan_result"), [])   # and the interface was told of no result
        refused = [b for m in session.messages for b in m.content
                   if isinstance(b, ToolResultBlock) and b.call_id == "x1"]
        self.assertEqual(refused[0].content, UNKNOWN_TOOL.format(name="execute_episode_plan"))

    def test_users_confirmation_writes_once_and_reports_the_record(self):
        session, writer, plan_id = self.with_draft()
        plan = session.confirm(plan_id)

        self.assertIsNotNone(plan)
        self.assertEqual((writer.calls, self.status(plan_id)), (1, "verified"))
        [result] = self.of("plan_result")
        self.assertEqual((result.data["status"], result.data["action"]), ("verified", "confirm"))
        self.assertEqual(session.pending_plans, [])
        self.assertIn("verified", text_of(session.messages[-1]))  # the model is told afterwards
        with self.assertRaises(PlanError):
            session.confirm(plan_id)                               # not waiting any more
        self.assertEqual(writer.calls, 1)

    def test_only_plans_from_this_session_can_be_confirmed(self):
        session, writer, _ = self.with_draft()
        other = self.store.create(account_id=42, subject_id=100, episode_id=9001, subject_name="测试作品",
                                  episode_name="小雪", episode_sort=4, before_type=0,
                                  created_at=1000, expires_at=1900)
        with self.assertRaises(PlanError):
            session.confirm(other)

        self.assertEqual((writer.calls, self.status(other)), (0, "draft"))

    def test_decline_voids_the_plan(self):
        session, writer, plan_id = self.with_draft()
        session.decline(plan_id)

        self.assertEqual((writer.calls, self.status(plan_id)), (0, "cancelled"))
        self.assertEqual(session.pending_plans, [])

    def test_site_unreadable_at_confirm_withdraws_the_approval(self):
        session, writer, plan_id = self.with_draft()
        self.site.fail_reads = True  # the re-read just before writing fails
        session.confirm(plan_id)

        self.assertEqual((writer.calls, self.status(plan_id)), (0, "cancelled"))
        self.assertEqual(self.of("plan_result")[0].data["action"], "voided")


class CancelWithPlans(SessionCase):
    def cancel_next_task(self, session: AgentSession) -> None:
        flag = threading.Event()
        flag.set()
        self.events.clear()
        outcome = session.send("算了", cancel=flag)
        self.assertEqual(outcome.status, "cancelled")

    def test_draft_stays_a_draft(self):
        session, writer, plan_id = self.with_draft()
        self.cancel_next_task(session)

        self.assertEqual(self.kinds(), ["cancelled"])
        self.assertEqual((writer.calls, self.status(plan_id)), (0, "draft"))
        self.assertEqual(session.pending_plans, [plan_id])

    def test_approval_is_withdrawn(self):
        session, writer, plan_id = self.with_draft()
        ExecutionService(self.site, self.store, writer, lambda: 1000).approve(plan_id)
        self.cancel_next_task(session)

        self.assertEqual((writer.calls, self.status(plan_id)), (0, "cancelled"))
        self.assertEqual(self.of("plan_result")[0].data["action"], "voided")

    def test_sent_write_is_checked_and_never_sent_again(self):
        # The write reached the site but the answer was lost: the record says unknown.
        session, writer, plan_id = self.with_draft(outcome="lost_response")
        session.confirm(plan_id)
        self.assertEqual((writer.calls, self.status(plan_id)), (1, "unknown"))
        self.assertEqual(session.pending_plans, [plan_id])

        self.cancel_next_task(session)

        self.assertEqual(writer.calls, 1)
        self.assertEqual(self.status(plan_id), "verified")
        [result] = self.of("plan_result")
        self.assertEqual((result.data["status"], result.data["action"]), ("verified", "checked"))


class ContextLimit(SessionCase):
    def test_task_stops_before_a_request_that_would_be_too_large(self):
        big = ModelReply(Message("assistant", [TextBlock("我查一下。"), call("c1")]), "tool_calls", Usage(180, 30))
        session, model, _ = self.session(big, context_limit=200)
        outcome = session.send("帮我找一下芙莉莲")

        self.assertEqual((outcome.status, outcome.text), ("context_full", CONTEXT_FULL))
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.events[-1].data["kind"], "context_full")
        # Nothing was dropped to make room: the call and its result are both still there.
        self.assertEqual([m.role for m in session.messages], ["user", "assistant", "tool"])
        self.assertEqual(len(tool_calls_of(session.messages[1])), 1)

    def test_full_session_refuses_new_messages_without_changing_the_history(self):
        big = ModelReply(Message("assistant", [TextBlock("好了。")]), "end", Usage(190, 20))
        session, model, _ = self.session(big, context_limit=200)
        session.send("第一个问题")
        before = list(session.messages)
        outcome = session.send("第二个问题")

        self.assertEqual(outcome.status, "context_full")
        self.assertEqual(session.messages, before)
        self.assertEqual(len(model.requests), 1)


if __name__ == "__main__":
    unittest.main()
