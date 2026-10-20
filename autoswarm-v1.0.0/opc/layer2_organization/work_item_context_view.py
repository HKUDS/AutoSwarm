"""Owner-aware read adapter over work_item / task metadata.

Company-mode historically embedded ~150+ context keys on ``Task.metadata``
and now stores WorkItem-owned fields on ``DelegationWorkItem.metadata``.
This adapter inverts old task-first reads: WorkItem-owned
keys are read from ``work_item.metadata`` first, and task-side fallback is
only allowed for keys explicitly marked as legacy fallback in
``metadata_ownership``. When no work item is in scope, it degrades to the
task-mode behavior and serves task metadata directly.

This module exposes a thin synchronous view that prefers work_item.metadata
and degrades to task.metadata. It's used by:

* ``opc.plugins.office_ui.snapshot_builder.work_item_to_kanban`` — the
  kanban renderer previously read ``linked_task.metadata`` for
  ``progress_log`` / ``work_item_role_name`` / ``employee_prompt_context``
  / ``employee_delta_context``. This path switches it to the view
  so it transparently prefers the work_item-side mirror once the mirror
  starts dual-writing.
* ``opc.layer1_perception.context_assembler`` and
  ``opc.layer3_agent.external_broker`` — This path migrates those
  consumers the same way. Because the view degrades cleanly to task.metadata
  when no work_item is linked (task-mode path), task-mode callers
  continue to function without any mode branching.

**Design constraints (things the view is explicitly NOT):**

* **No async / no I/O.** Construct from an in-scope work_item + task pair.
  The snapshot builder and context assembler already have both; adding
  async store reads would double the round-trips per render.
* **No cache.** Two dict reads is fast enough — measure before optimising.
* **No ``.set()`` writer.** Writes still go through the store via
  ``update_delegation_work_item`` / ``save_task``. The view is a read
  surface only.
* **Defensive copies for list/dict.** Callers must not be able to
  accidentally mutate the source dict through the view.
"""
from __future__ import annotations

from typing import Any

from opc.layer2_organization.metadata_ownership import supports_legacy_task_fallback


class WorkItemContextView:
    """Read-only view preferring WorkItem-owned metadata.

    Constructor snapshots both dicts at construction. In company mode, task
    fallback is a legacy compatibility path controlled by the owner matrix.
    In task mode (no WorkItem), all task metadata remains visible.
    """

    __slots__ = ("_wi_meta", "_task_meta", "_has_work_item")

    def __init__(self, work_item: Any = None, task: Any = None):
        wi_meta = getattr(work_item, "metadata", None) if work_item is not None else None
        task_meta = getattr(task, "metadata", None) if task is not None else None
        self._wi_meta: dict = dict(wi_meta) if isinstance(wi_meta, dict) else {}
        self._task_meta: dict = dict(task_meta) if isinstance(task_meta, dict) else {}
        self._has_work_item = isinstance(wi_meta, dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Return metadata according to the owner matrix.

        Returns ``None`` if the key exists but is None on the work_item side;
        this preserves dict.get semantics and prevents accidental fallback
        over an explicit WorkItem value.
        """
        if key in self._wi_meta:
            return self._wi_meta[key]
        if self._has_work_item and not supports_legacy_task_fallback(key):
            return default
        return self._task_meta.get(key, default)

    def get_list(self, key: str) -> list:
        """Return a fresh list copy. Empty list for missing / non-list keys."""
        v = self.get(key, None)
        if isinstance(v, (list, tuple)):
            return list(v)
        return []

    def get_dict(self, key: str) -> dict:
        """Return a fresh dict copy. Empty dict for missing / non-dict keys."""
        v = self.get(key, None)
        if isinstance(v, dict):
            return dict(v)
        return {}

    def has(self, key: str) -> bool:
        """True iff the key is visible through this owner-aware view."""
        return key in self._wi_meta or (
            key in self._task_meta
            and (not self._has_work_item or supports_legacy_task_fallback(key))
        )

    def __repr__(self) -> str:
        return (
            "WorkItemContextView("
            f"wi_keys={len(self._wi_meta)}, task_keys={len(self._task_meta)})"
        )
