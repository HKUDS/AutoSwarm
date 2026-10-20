from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from opc.core.models import AgentMessage, ExternalSession
from opc.database.store import OPCStore


class CliRuntimeStoreHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_runtime_event_and_session_filters(self) -> None:
        await self.store.save_runtime_session(
            runtime_session_id="rt-1",
            project_id="proj1",
            session_id="sess-1",
            task_id="task-1",
            status="running",
            metadata={"summary": "active"},
        )
        await self.store.save_runtime_session(
            runtime_session_id="rt-2",
            project_id="proj1",
            session_id="sess-2",
            task_id="task-2",
            status="completed",
            metadata={"summary": "done"},
        )
        await self.store.save_runtime_event("rt-1", "tool_started", {"tool_name": "shell_exec"})
        await self.store.save_runtime_event("rt-1", "tool_completed", {"result_summary": "ok"})

        by_project = await self.store.list_runtime_sessions(project_id="proj1", limit=10)
        by_task = await self.store.list_runtime_sessions(project_id="proj1", task_id="task-1", limit=10)
        by_session = await self.store.list_runtime_sessions(project_id="proj1", session_id="sess-2", limit=10)
        events = await self.store.list_runtime_events("rt-1", limit=10)

        self.assertEqual({row["runtime_session_id"] for row in by_project}, {"rt-1", "rt-2"})
        self.assertEqual([row["runtime_session_id"] for row in by_task], ["rt-1"])
        self.assertEqual([row["runtime_session_id"] for row in by_session], ["rt-2"])
        self.assertEqual([event["event_type"] for event in events], ["tool_started", "tool_completed"])
        self.assertEqual(events[0]["payload"]["tool_name"], "shell_exec")

    async def test_external_session_filters(self) -> None:
        await self.store.save_external_session(
            ExternalSession(
                agent_type="codex",
                project_id="proj1",
                session_id="provider-1",
                opc_session_id="sess-1",
                task_id="task-1",
                workspace_path="workspace",
                run_mode="interactive",
                status="running",
                updated_at=datetime.now(),
            )
        )
        await self.store.save_external_session(
            ExternalSession(
                agent_type="claude_code",
                project_id="proj1",
                session_id="provider-2",
                opc_session_id="sess-2",
                task_id="task-2",
                status="completed",
                updated_at=datetime.now(),
            )
        )

        running = await self.store.list_external_sessions(project_id="proj1", status="running")
        by_task = await self.store.list_external_sessions(project_id="proj1", task_id="task-2")
        by_opc_session = await self.store.list_external_sessions(project_id="proj1", opc_session_id="sess-1")

        self.assertEqual([item.agent_type for item in running], ["codex"])
        self.assertEqual([item.session_id for item in by_task], ["provider-2"])
        self.assertEqual([item.task_id for item in by_opc_session], ["task-1"])

    async def test_external_session_done_closes_replaced_working_placeholder(self) -> None:
        await self.store.save_external_session(
            ExternalSession(
                agent_type="codex",
                project_id="proj1",
                session_id="codex:proj1:task-1",
                opc_session_id="role-session-1",
                task_id="task-1",
                status="working",
                updated_at=datetime.now(),
            )
        )
        await self.store.save_external_session(
            ExternalSession(
                agent_type="codex",
                project_id="proj1",
                session_id="codex:proj1:task-1",
                opc_session_id="other-role-session",
                task_id="task-1",
                status="working",
                updated_at=datetime.now(),
            )
        )

        await self.store.save_external_session(
            ExternalSession(
                agent_type="codex",
                project_id="proj1",
                session_id="provider-session-1",
                opc_session_id="role-session-1",
                task_id="task-1",
                status="done",
                updated_at=datetime.now(),
            )
        )

        role_rows = await self.store.list_external_sessions(
            project_id="proj1",
            task_id="task-1",
            opc_session_id="role-session-1",
        )
        other_role_rows = await self.store.list_external_sessions(
            project_id="proj1",
            task_id="task-1",
            opc_session_id="other-role-session",
        )

        self.assertEqual({item.status for item in role_rows}, {"done"})
        self.assertEqual({item.session_id for item in role_rows}, {"codex:proj1:task-1", "provider-session-1"})
        self.assertEqual([item.status for item in other_role_rows], ["working"])

    async def test_agent_messages_for_task_scope(self) -> None:
        await self.store.save_message(
            AgentMessage(
                msg_id="msg-1",
                msg_type="request_review",
                from_agent="founder",
                to_agents=["reviewer"],
                subject="Review",
                body="Please review.",
                task_id="task-1",
            )
        )
        await self.store.save_message(
            AgentMessage(
                msg_id="msg-2",
                msg_type="inform",
                from_agent="reviewer",
                to_agents=["founder"],
                subject="Other",
                body="Other task.",
                task_id="task-2",
            )
        )

        messages = await self.store.list_agent_messages_for_tasks(["task-1"], limit=10)

        self.assertEqual([message.msg_id for message in messages], ["msg-1"])
        self.assertEqual(messages[0].subject, "Review")
