from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opc.core.models import SessionMessageRecord, SessionRecord, Task, TaskResult, TaskStatus
from opc.engine import OPCEngine
from opc.layer5_memory.memory_manager import MemoryManager


class SharedRoleSessionIdTests(unittest.TestCase):
    def test_final_decider_reuses_root_session(self) -> None:
        self.assertEqual(
            OPCEngine._shared_company_role_session_id(
                "app14",
                "ceo",
                final_decider_role_id="ceo",
            ),
            "app14",
        )

    def test_same_role_reuses_same_session_id(self) -> None:
        first = OPCEngine._shared_company_role_session_id("app14", "cmo")
        second = OPCEngine._shared_company_role_session_id("app14", "cmo")
        self.assertEqual(first, "app14:role:cmo")
        self.assertEqual(first, second)


class _MemoryStoreStub:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.session_messages: list[SessionMessageRecord] = []

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    async def save_session(self, session: SessionRecord) -> None:
        self.sessions[session.session_id] = session

    async def save_session_link(self, _link: object) -> None:
        return None

    async def save_session_message(self, message: SessionMessageRecord) -> None:
        self.session_messages.append(message)

    async def save_session_part(self, _part: object) -> None:
        return None


class MemoryManagerCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_session_message_does_not_trigger_history_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryManager(Path(tmpdir), project_id="proj", store=_MemoryStoreStub())
            compactor = SimpleNamespace(maybe_compact_after_message=AsyncMock())
            memory.set_history_compactor(compactor)

            await memory.append_session_message(
                "sess-1",
                "assistant",
                text="hello",
                project_id="proj",
                metadata={"role_id": "cmo"},
            )

            compactor.maybe_compact_after_message.assert_not_awaited()

    async def test_parent_child_result_preserves_structured_source_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _MemoryStoreStub()
            memory = MemoryManager(Path(tmpdir), project_id="0009", store=store)
            task = Task(
                id="task-cto-result",
                title="CTO Result",
                assigned_to="cto",
                project_id="0009",
                session_id="child-session",
                parent_session_id="parent-session",
                metadata={"work_item_projection_id": "cto::analysis"},
            )

            await memory.record_child_session_result(
                "parent-session",
                "child-session",
                task=task,
                result_content="done",
                result_delivery_id=(
                    "result:task:task-cto-result:turn:canonical-cto-turn:attempt:0"
                ),
                source_result_message_id="source-result-message",
                canonical_turn_id="canonical-cto-turn",
            )

            metadata = store.session_messages[-1].metadata
            self.assertEqual(
                metadata["result_delivery_id"],
                "result:task:task-cto-result:turn:canonical-cto-turn:attempt:0",
            )
            self.assertEqual(metadata["source_result_message_id"], "source-result-message")
            self.assertEqual(metadata["source_task_id"], "task-cto-result")
            self.assertEqual(metadata["child_session_id"], "child-session")
            self.assertEqual(metadata["canonical_turn_id"], "canonical-cto-turn")


class SharedRoleSessionExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_fallback_delivery_identity_is_per_result_not_per_provider_session(self) -> None:
        task = Task(
            id="shared-role-task",
            title="Shared role",
            assigned_to="cto",
            retry_count=0,
        )
        first = TaskResult(
            status=TaskStatus.AWAITING_HUMAN,
            content="first pause",
            artifacts={"provider_session_id": "provider-session-reused"},
        )
        second = TaskResult(
            status=TaskStatus.DONE,
            content="second result",
            artifacts={"provider_session_id": "provider-session-reused"},
        )

        first_identity = OPCEngine._ensure_result_delivery_identity_for_commit(task, first)
        second_identity = OPCEngine._ensure_result_delivery_identity_for_commit(task, second)

        self.assertNotEqual(
            first_identity["result_delivery_id"],
            second_identity["result_delivery_id"],
        )
        self.assertNotEqual(
            first.artifacts["result_execution_id"],
            second.artifacts["result_execution_id"],
        )
        self.assertNotIn("provider-session-reused", first_identity["result_delivery_id"])

    def test_fallback_delivery_identity_is_stable_after_result_persistence(self) -> None:
        task = Task(id="external-task", title="External", assigned_to="cto")
        result = TaskResult(
            status=TaskStatus.DONE,
            content="done",
            artifacts={"runtime_session_id": "runtime-session-reused"},
        )
        first_identity = OPCEngine._ensure_result_delivery_identity_for_commit(task, result)
        persisted_artifacts = dict(result.artifacts)
        reloaded = TaskResult(
            status=TaskStatus.DONE,
            content="done",
            artifacts=persisted_artifacts,
        )

        replay_identity = OPCEngine._ensure_result_delivery_identity_for_commit(task, reloaded)

        self.assertEqual(replay_identity, first_identity)
        self.assertEqual(reloaded.artifacts, persisted_artifacts)

    async def test_company_shared_role_session_keeps_results_local(self) -> None:
        engine = OPCEngine()
        engine.store = SimpleNamespace(
            get_task=AsyncMock(return_value=None),
            save_task=AsyncMock(),
        )
        engine.memory = SimpleNamespace(
            record_assistant_turn=AsyncMock(),
            record_child_session_result=AsyncMock(),
            record_task_completion_async=AsyncMock(),
        )
        engine._run_task_once = AsyncMock(
            return_value=TaskResult(
                status=TaskStatus.DONE,
                content="done",
                artifacts={"result_delivery_id": "delivery-shared-role"},
            )
        )
        engine._apply_runtime_state_to_task = lambda task, result: None

        task = Task(
            id="task-cmo-review",
            title="Review Turn: cmo",
            assigned_to="cmo",
            status=TaskStatus.PENDING,
            project_id="new16",
            session_id="app14:role:cmo",
            parent_session_id="app14",
            metadata={
                "shared_role_session": True,
                "execution_mode": "company_mode",
                "work_item_projection_id": "review::demo",
                "employee_assignment": {"employee_id": "emp-cmo", "role_id": "cmo"},
            },
        )

        await engine._execute_task(task)

        engine.memory.record_assistant_turn.assert_awaited_once()
        engine.memory.record_child_session_result.assert_not_awaited()
        metadata = engine.memory.record_assistant_turn.await_args.kwargs["metadata"]
        self.assertEqual(metadata["result_delivery_id"], "delivery-shared-role")
        self.assertEqual(metadata["source_task_id"], "task-cmo-review")
        self.assertEqual(metadata["child_session_id"], "app14:role:cmo")

    async def test_child_result_and_parent_mirror_share_delivery_identity(self) -> None:
        engine = OPCEngine()
        engine.store = SimpleNamespace(
            get_task=AsyncMock(return_value=None),
            save_task=AsyncMock(),
        )
        engine.memory = SimpleNamespace(
            record_assistant_turn=AsyncMock(
                return_value=SimpleNamespace(message_id="source-result-message")
            ),
            record_child_session_result=AsyncMock(),
            record_task_completion_async=AsyncMock(),
        )
        engine._run_task_once = AsyncMock(
            return_value=TaskResult(
                status=TaskStatus.DONE,
                content="done",
                artifacts={
                    "canonical_turn_id": "runtime-generated-turn",
                    "result_delivery_id": (
                        "result:task:task-cto-result:turn:runtime-generated-turn:attempt:0"
                    ),
                    "runtime_session_id": "runtime-session-1",
                },
            )
        )
        engine._apply_runtime_state_to_task = lambda task, result: None

        task = Task(
            id="task-cto-result",
            title="CTO Result",
            assigned_to="cto",
            status=TaskStatus.PENDING,
            project_id="0009",
            session_id="child-session",
            parent_session_id="parent-session",
            metadata={
                "execution_mode": "company_mode",
                "work_item_projection_id": "cto::analysis",
            },
        )

        await engine._execute_task(task)

        child_metadata = engine.memory.record_assistant_turn.await_args.kwargs["metadata"]
        parent_call = engine.memory.record_child_session_result.await_args.kwargs
        self.assertEqual(
            child_metadata["result_delivery_id"],
            "result:task:task-cto-result:turn:runtime-generated-turn:attempt:0",
        )
        self.assertEqual(
            parent_call["result_delivery_id"],
            child_metadata["result_delivery_id"],
        )
        self.assertEqual(parent_call["source_result_message_id"], "source-result-message")
        self.assertEqual(parent_call["canonical_turn_id"], "runtime-generated-turn")
