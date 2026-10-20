"""Seat-scoped execution adapter for actor-runtime company mode."""

from __future__ import annotations

from typing import Any, Protocol

from opc.core.models import CompanyMemberSession, Task, TaskResult
from opc.layer2_organization.session_scoping import (
    external_resume_allowed_for_scope,
    task_session_scope_id,
)


class SeatExecutor(Protocol):
    """Common execution contract for seat-backed turns."""

    async def prepare_seat(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
        role: Any | None = None,
    ) -> None: ...

    async def run_turn(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> TaskResult: ...

    async def checkpoint(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> dict[str, Any]: ...

    async def interrupt(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> None: ...

    async def shutdown(
        self,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> None: ...


class EngineSeatExecutor:
    """Seat executor backed by the existing AutoSwarm engine task runners."""

    def __init__(self, host: Any) -> None:
        self.host = host

    async def prepare_seat(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
        role: Any | None = None,
    ) -> None:
        _ = role
        if member_session is None:
            return
        adapter_state = dict(member_session.adapter_session_state or {})
        if not adapter_state:
            return
        task.metadata = dict(task.metadata or {})
        task.context_snapshot = dict(task.context_snapshot or {})
        resume_scope_id = str(
            adapter_state.get("external_resume_session_scope_id", "")
            or adapter_state.get("session_scope_id", "")
            or ""
        ).strip()
        assigned_agent = str(task.assigned_external_agent or "").strip()
        state_agent = str(
            adapter_state.get("external_resume_agent_type")
            or adapter_state.get("selected_execution_agent")
            or ""
        ).strip()
        if assigned_agent:
            agent_entry = adapter_state.get(assigned_agent)
            if isinstance(agent_entry, dict):
                adapter_state = {**adapter_state, **dict(agent_entry)}
                entry_token = str(
                    agent_entry.get("external_resume_session_id")
                    or agent_entry.get("resume_session_id")
                    or agent_entry.get("provider_session_id")
                    or ""
                ).strip()
                if entry_token:
                    adapter_state["external_resume_session_id"] = entry_token
                state_agent = assigned_agent
        if not external_resume_allowed_for_scope(task, resume_scope_id=resume_scope_id):
            adapter_state.pop("external_resume_session_id", None)
            adapter_state.pop("external_resume_session_scope_id", None)
            adapter_state.pop("external_resume_agent_type", None)
        external_resume_session_id = str(adapter_state.get("external_resume_session_id", "") or "").strip()
        if external_resume_session_id and assigned_agent and state_agent == assigned_agent:
            task.metadata["external_resume_session_id"] = external_resume_session_id
            task.metadata["external_resume_session_scope_id"] = (
                resume_scope_id or task_session_scope_id(task)
            )
            task.metadata["external_resume_agent_type"] = assigned_agent
        else:
            task.metadata.pop("external_resume_session_id", None)
            task.metadata.pop("external_resume_session_scope_id", None)
            task.metadata.pop("external_resume_agent_type", None)
        if adapter_state:
            task.context_snapshot["seat_adapter_session_state"] = dict(adapter_state)

    async def run_turn(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> TaskResult:
        _ = member_session
        return await self.host._execute_task(task)

    async def checkpoint(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> dict[str, Any]:
        _ = member_session
        return {
            "task_id": str(task.id or "").strip(),
            "seat_id": str((task.metadata or {}).get("delegation_seat_id", "") or "").strip(),
            "role_session_id": str((task.metadata or {}).get("delegation_role_session_id", "") or "").strip(),
            "assigned_external_agent": str(task.assigned_external_agent or "").strip(),
            "external_resume_session_id": str((task.metadata or {}).get("external_resume_session_id", "") or "").strip(),
        }

    async def interrupt(
        self,
        task: Task,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> None:
        _ = member_session
        # The task coroutine owns its registry attempt token and removes it in
        # ``finally``.  Interrupt requests must not make a still-running
        # coroutine appear inactive.
        return None

    async def shutdown(
        self,
        *,
        member_session: CompanyMemberSession | None = None,
    ) -> None:
        _ = member_session
        return None
