"""Shared Kanban presentation helpers for UI and CLI surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from opc.core.models import TaskStatus


STATUS_TO_COLUMN: dict[str, str] = {
    "pending": "todo",
    "running": "in-progress",
    "idle": "in-progress",
    "blocked": "in-progress",
    "awaiting_peer": "in-progress",
    "awaiting_manager_review": "in-progress",
    "awaiting_human": "in-progress",
    "awaiting_review": "in-progress",
    "done": "done",
    "failed": "done",
    "cancelled": "done",
}


@dataclass(frozen=True)
class KanbanColumnDefinition:
    column_id: str
    name: str
    sort_order: int
    is_terminal: bool = False
    color: str | None = None


DEFAULT_KANBAN_COLUMNS: tuple[KanbanColumnDefinition, ...] = (
    KanbanColumnDefinition("todo", "Todo", 0, color="#6b7280"),
    KanbanColumnDefinition("in-progress", "In Progress", 1, color="#eab308"),
    KanbanColumnDefinition("done", "Done", 2, is_terminal=True, color="#22c55e"),
)

COMPANY_KANBAN_COLUMNS: tuple[KanbanColumnDefinition, ...] = (
    KanbanColumnDefinition("todo", "To do", 0, color="#6b7280"),
    KanbanColumnDefinition("in-progress", "In progress", 1, color="#eab308"),
    KanbanColumnDefinition("in-review", "In review", 2, color="#f59e0b"),
    KanbanColumnDefinition("done", "Done", 3, is_terminal=True, color="#22c55e"),
)



def column_to_task_status(column_id: str) -> TaskStatus | None:
    normalized = str(column_id or "").strip().lower()
    mapping = {
        "todo": TaskStatus.PENDING,
        "backlog": TaskStatus.PENDING,
        "in-progress": TaskStatus.RUNNING,
        "done": TaskStatus.DONE,
        "blocked": TaskStatus.BLOCKED,
        "failed": TaskStatus.FAILED,
        "cancelled": TaskStatus.CANCELLED,
    }
    return mapping.get(normalized)


def task_status_value(task: Any) -> str:
    status = getattr(task, "status", "")
    return status.value if hasattr(status, "value") else str(status)


def priority_to_label(priority: int | None) -> str | None:
    if priority is None:
        return None
    if priority <= 2:
        return "urgent"
    if priority <= 4:
        return "high"
    if priority <= 6:
        return "medium"
    if priority <= 8:
        return "low"
    return None


def datetime_to_timestamp(value: Any) -> float:
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    if isinstance(value, datetime):
        return float(value.timestamp())
    return datetime.now().timestamp()


def build_board_columns(board_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
    timestamp = float(now) if now is not None else datetime.now().timestamp()
    return [
        {
            "column_id": column.column_id,
            "board_id": board_id,
            "name": column.name,
            "color": column.color,
            "sort_order": column.sort_order,
            "is_terminal": column.is_terminal,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        for column in DEFAULT_KANBAN_COLUMNS
    ]


def build_company_board_columns(board_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
    timestamp = float(now) if now is not None else datetime.now().timestamp()
    return [
        {
            "column_id": column.column_id,
            "board_id": board_id,
            "name": column.name,
            "color": column.color,
            "sort_order": column.sort_order,
            "is_terminal": column.is_terminal,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        for column in COMPANY_KANBAN_COLUMNS
    ]


def build_base_task_payload(
    task: Any,
    display_num: int,
    *,
    board_id: str | None = None,
    assignee_ids: list[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status_val = task_status_value(task)
    created_at = datetime_to_timestamp(getattr(task, "created_at", None))
    updated_at = datetime_to_timestamp(getattr(task, "updated_at", None) or getattr(task, "created_at", None))
    payload: dict[str, Any] = {
        "task_id": getattr(task, "id", ""),
        "display_id": f"OPC-{display_num}",
        "board_id": board_id or getattr(task, "project_id", "default") or "default",
        "column_id": STATUS_TO_COLUMN.get(status_val, "todo"),
        "title": getattr(task, "title", ""),
        "description": getattr(task, "description", "") or "",
        "status": status_val,
        "priority": priority_to_label(getattr(task, "priority", None)),
        "assignee_ids": list(assignee_ids or []),
        "tags": list(getattr(task, "tags", []) or []),
        "sort_order": display_num,
        "session_id": getattr(task, "session_id", None),
        "chat_channel_id": f"session:{getattr(task, 'id', '')}",
        "created_at": created_at,
        "updated_at": updated_at,
        "dependencies": list(getattr(task, "dependencies", []) or []),
    }
    if extra:
        payload.update(dict(extra))
    return payload
