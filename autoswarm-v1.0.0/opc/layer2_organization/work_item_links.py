"""Runtime link helpers for company WorkItem <-> Task relations."""

from __future__ import annotations

from typing import Any, Iterable


def linked_work_item_id_for_task(task: Any | None) -> str:
    """Return the WorkItem id linked to a runtime Task.

    The structured link table hydrates ``Task.linked_work_item_id``. Runtime
    code should not use legacy Task metadata as a mapping source.
    """
    if task is None:
        return ""
    return str(getattr(task, "linked_work_item_id", "") or "").strip()


def set_linked_work_item_id(task: Any | None, work_item_id: str) -> None:
    if task is None:
        return
    setattr(task, "linked_work_item_id", str(work_item_id or "").strip())


def task_by_linked_work_item_id(tasks: Iterable[Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for task in tasks:
        work_item_id = linked_work_item_id_for_task(task)
        if work_item_id:
            mapping[work_item_id] = task
    return mapping
