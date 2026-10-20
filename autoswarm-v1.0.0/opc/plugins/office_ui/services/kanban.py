"""Kanban task service shared by Office UI and CLI."""

from __future__ import annotations

import uuid
from typing import Any

from opc.core.models import TaskStatus
from opc.layer2_organization.work_item_transition import apply_task_status_transition
from opc.presentation.kanban import column_to_task_status

from .context import OfficeServiceContext
from .models import ServiceError, ServiceEvent, ServiceResult
from .session import SessionService


class KanbanService:
    TERMINAL_STATUSES: set[str] = {"done", "failed", "cancelled"}

    def __init__(self, context: OfficeServiceContext, session_service: SessionService) -> None:
        self.context = context
        self.session_service = session_service

    def _reject_system_driven(self) -> None:
        if self.context.mode_state.exec_mode in {"company", "org", "custom"}:
            raise ServiceError("company_mode_kanban_is_system_driven", "company_mode_kanban_is_system_driven")

    async def create_task(self, *, project_id: str, title: str, description: str = "", task_id: str | None = None, board_id: str | None = None, assignee_ids: list[str] | None = None) -> ServiceResult:
        self._reject_system_driven()
        return await self.session_service.create(
            project_id=project_id,
            title=title or "Untitled",
            description=description,
            task_id=task_id or str(uuid.uuid4()),
            exec_mode=self.context.mode_state.exec_mode,
            company_profile=self.context.mode_state.company_profile,
            preferred_agent=self.context.mode_state.task_preferred_agent,
            interface="office_ui",
            board_id=board_id,
            assignee_ids=assignee_ids or [],
        )

    async def update_task(self, *, project_id: str, task_id: str, updates: dict[str, Any]) -> ServiceResult:
        self._reject_system_driven()
        engine = await self.context.engine_for_project(project_id)
        if task_id and getattr(engine, "store", None):
            task = await engine.store.get_task(task_id)
            if task:
                if "title" in updates:
                    task.title = updates["title"]
                if "description" in updates:
                    task.description = updates["description"]
                if "tags" in updates:
                    task.tags = updates["tags"]
                await engine.store.save_task(task)
        payload = {"project_id": self.context.normalize_project_id(project_id), "task_id": task_id}
        return ServiceResult(payload, [ServiceEvent("kanban_updated", payload)])

    async def move_task(self, *, project_id: str, task_id: str, column_id: str) -> ServiceResult:
        self._reject_system_driven()
        engine = await self.context.engine_for_project(project_id)
        if task_id and column_id and getattr(engine, "store", None):
            task = await engine.store.get_task(task_id)
            if task:
                current = task.status.value if hasattr(task.status, "value") else str(task.status)
                new_status = column_to_task_status(column_id)
                if not new_status:
                    raise ServiceError("invalid_column", f"Invalid column/status: {column_id}")
                target = new_status.value if hasattr(new_status, "value") else str(new_status)
                if current in self.TERMINAL_STATUSES and target not in self.TERMINAL_STATUSES:
                    raise ServiceError("terminal_task", f"Cannot move {current} task back to {column_id}")
                if current != target:
                    task.status = new_status
                    await engine.store.save_task(task)
        payload = {"project_id": self.context.normalize_project_id(project_id), "task_id": task_id, "display_id": "", "column_name": column_id}
        return ServiceResult(payload, [ServiceEvent("board_task_moved", payload)])

    async def delete_task(self, *, project_id: str, task_id: str) -> ServiceResult:
        self._reject_system_driven()
        engine = await self.context.engine_for_project(project_id)
        if task_id and getattr(engine, "store", None):
            task = await engine.store.get_task(task_id)
            if task:
                await apply_task_status_transition(
                    engine.store,
                    task,
                    target_status_or_phase=TaskStatus.CANCELLED,
                    reason="kanban_delete_task",
                    release_claim=True,
                )
        payload = {"project_id": self.context.normalize_project_id(project_id), "task_id": task_id}
        return ServiceResult(payload, [ServiceEvent("kanban_updated", payload)])

    async def assign(self, *, project_id: str, task_id: str, agent_id: str) -> ServiceResult:
        self._reject_system_driven()
        engine = await self.context.engine_for_project(project_id)
        if task_id and agent_id and getattr(engine, "store", None):
            task = await engine.store.get_task(task_id)
            if task:
                agent = await self.context.agent_store._get_one(agent_id)
                role_id = agent.get("opc_role_id", agent_id) if agent else agent_id
                task.assigned_to = role_id
                await engine.store.save_task(task)
        return ServiceResult({"project_id": self.context.normalize_project_id(project_id), "task_id": task_id, "agent_id": agent_id})

    async def status(self, *, project_id: str, task_id: str, status: str) -> ServiceResult:
        return await self.move_task(project_id=project_id, task_id=task_id, column_id=status)
