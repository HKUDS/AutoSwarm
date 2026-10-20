"""In-memory board state for the Textual app."""

from __future__ import annotations

import time
from dataclasses import replace

from opc.presentation.kanban import DEFAULT_KANBAN_COLUMNS

from .models import (
    BoardAlert,
    BoardMetrics,
    BoardSnapshot,
    BoardTaskView,
    ContextTab,
    DensityMode,
    PaneFocus,
    RuntimeTaskState,
    SessionSummaryView,
    ViewMode,
)

_RUNTIME_ACTIVE = {"running", "reflecting", "tool_active"}
_STATUS_ACTIVE = {"running", "idle", "blocked", "awaiting_peer", "awaiting_review"}
_STATUS_BLOCKED = {"blocked", "awaiting_peer", "awaiting_review"}


class BoardStateStore:
    """Keeps a filtered, runtime-enriched view of board state."""

    def __init__(self) -> None:
        self.snapshot = BoardSnapshot(project_id="default")
        self.runtime_by_task: dict[str, RuntimeTaskState] = {}
        self.search_query: str = ""
        self.show_done: bool = True
        self.selected_task_id: str | None = None
        self.column_order = [column.column_id for column in DEFAULT_KANBAN_COLUMNS]
        self.view_mode: ViewMode = "kanban"
        self.pane_focus: PaneFocus = "main"
        self.context_tab: ContextTab = "detail"
        self.density_mode: DensityMode = "compact"

    def replace_snapshot(self, snapshot: BoardSnapshot) -> None:
        self.snapshot = snapshot
        if snapshot.column_order:
            self.column_order = list(snapshot.column_order)
        self._ensure_selection()

    def set_search_query(self, query: str) -> None:
        self.search_query = str(query or "").strip()
        self._ensure_selection()

    def toggle_show_done(self) -> bool:
        self.show_done = not self.show_done
        self._ensure_selection()
        return self.show_done

    def toggle_density(self) -> DensityMode:
        self.density_mode = "comfortable" if self.density_mode == "compact" else "compact"
        return self.density_mode

    def set_view_mode(self, mode: ViewMode) -> ViewMode:
        self.view_mode = mode
        if mode in {"focus", "pipeline", "org"}:
            self.pane_focus = "main"
        self._ensure_selection()
        return self.view_mode

    def cycle_view_mode(self, delta: int = 1) -> ViewMode:
        order: tuple[ViewMode, ...] = ("kanban", "list", "focus", "pipeline", "org")
        idx = order.index(self.view_mode) if self.view_mode in order else 0
        self.view_mode = order[(idx + delta) % len(order)]
        if self.view_mode in {"focus", "pipeline", "org"}:
            self.pane_focus = "main"
        return self.view_mode

    def set_pane_focus(self, focus: PaneFocus) -> PaneFocus:
        self.pane_focus = focus
        return self.pane_focus

    def cycle_pane_focus(self, delta: int = 1) -> PaneFocus:
        order: list[PaneFocus] = ["session-rail", "main", "context"]
        if self.view_mode in {"focus", "pipeline", "org"}:
            order = ["main", "context"]
        current = self.pane_focus if self.pane_focus in order else "main"
        idx = order.index(current)
        self.pane_focus = order[(idx + delta) % len(order)]
        return self.pane_focus

    def set_context_tab(self, tab: ContextTab) -> ContextTab:
        self.context_tab = tab
        return self.context_tab

    def cycle_context_tab(self, delta: int = 1) -> ContextTab:
        order: tuple[ContextTab, ...] = ("detail", "session", "activity")
        idx = order.index(self.context_tab)
        self.context_tab = order[(idx + delta) % len(order)]
        return self.context_tab

    def clear_runtime(self) -> None:
        self.runtime_by_task.clear()

    def apply_task_status(self, task_id: str, status: str, *, column_id: str | None = None) -> None:
        for index, task in enumerate(self.snapshot.tasks):
            if task.task_id != task_id:
                continue
            self.snapshot.tasks[index] = replace(
                task,
                status=status,
                column_id=column_id or task.column_id,
                updated_at=time.time(),
            )
            break
        self._ensure_selection()

    def apply_runtime_update(
        self,
        task_id: str,
        *,
        status: str,
        current_tool: str | None = None,
        iteration: int | None = None,
        tool_elapsed_ms: int | None = None,
        last_tool_summary: str | None = None,
        context_tokens: int | None = None,
        context_window: int | None = None,
        context_remaining_pct: int | None = None,
        turn_cost_usd: float | None = None,
        session_cost_usd: float | None = None,
        pending_permission_count: int | None = None,
        drain_mode: str | None = None,
    ) -> None:
        runtime = self.runtime_by_task.setdefault(task_id, RuntimeTaskState())
        runtime.status = status
        runtime.current_tool = current_tool
        if iteration is not None:
            runtime.iteration = iteration
        if tool_elapsed_ms is not None:
            runtime.tool_elapsed_ms = int(tool_elapsed_ms)
        if last_tool_summary is not None:
            runtime.last_tool_summary = str(last_tool_summary)
        if context_tokens is not None:
            runtime.context_tokens = int(context_tokens)
        if context_window is not None:
            runtime.context_window = int(context_window)
        if context_remaining_pct is not None:
            runtime.context_remaining_pct = int(context_remaining_pct)
        if turn_cost_usd is not None:
            runtime.turn_cost_usd = float(turn_cost_usd)
        if session_cost_usd is not None:
            runtime.session_cost_usd = float(session_cost_usd)
        if pending_permission_count is not None:
            runtime.pending_permission_count = int(pending_permission_count)
        if drain_mode is not None:
            runtime.drain_mode = str(drain_mode)
        runtime.updated_at = time.time()

    def append_progress(self, task_id: str, text: str) -> None:
        runtime = self.runtime_by_task.setdefault(task_id, RuntimeTaskState())
        runtime.push_progress(text)

    def runtime_for(self, task_id: str) -> RuntimeTaskState | None:
        return self.runtime_by_task.get(task_id)

    def all_tasks(self) -> list[BoardTaskView]:
        return list(self.snapshot.tasks)

    def filtered_tasks(self) -> list[BoardTaskView]:
        results: list[BoardTaskView] = []
        for task in self.snapshot.tasks:
            if not self.show_done and task.column_id == "done":
                continue
            if self.search_query and not self._matches_query(
                task.title,
                task.description,
                task.status,
                task.assigned_to,
                " ".join(task.assignee_ids),
                " ".join(task.tags),
            ):
                continue
            results.append(task)
        return results

    def linear_tasks(self) -> list[BoardTaskView]:
        return sorted(
            self.filtered_tasks(),
            key=lambda task: (
                self.column_order.index(task.column_id) if task.column_id in self.column_order else len(self.column_order),
                -float(task.updated_at),
                task.title.casefold(),
            ),
        )

    def tasks_by_column(self) -> dict[str, list[BoardTaskView]]:
        grouped = {column_id: [] for column_id in self.column_order}
        for task in self.filtered_tasks():
            grouped.setdefault(task.column_id, []).append(task)
        for tasks in grouped.values():
            tasks.sort(key=lambda task: (-float(task.updated_at), task.title.casefold()))
        return grouped

    def board_counts(self) -> dict[str, int]:
        grouped = self.tasks_by_column()
        return {column_id: len(grouped.get(column_id, [])) for column_id in self.column_order}

    def filtered_session_summaries(self) -> list[SessionSummaryView]:
        task_ids = {task.task_id for task in self.filtered_tasks()}
        source = self.snapshot.session_summaries or [self._summary_from_task(task) for task in self.snapshot.tasks]
        results: list[SessionSummaryView] = []
        for summary in source:
            if summary.task_id not in task_ids:
                continue
            if self.search_query and not self._matches_query(
                summary.title,
                summary.status,
                summary.assigned_to,
                " ".join(summary.tags),
                summary.priority or "",
            ):
                continue
            results.append(summary)
        results.sort(
            key=lambda item: (
                0 if self._is_live_summary(item) else 1 if item.column_id != "done" else 2,
                -float(item.updated_at),
                item.title.casefold(),
            )
        )
        return results

    def session_groups(self) -> dict[str, list[SessionSummaryView]]:
        groups = {
            "Live": [],
            "Queue": [],
            "Archive": [],
        }
        for summary in self.filtered_session_summaries():
            if self._is_live_summary(summary):
                groups["Live"].append(summary)
            elif summary.column_id == "done":
                groups["Archive"].append(summary)
            else:
                groups["Queue"].append(summary)
        return groups

    def selected_task(self) -> BoardTaskView | None:
        if not self.selected_task_id:
            return None
        for task in self.filtered_tasks():
            if task.task_id == self.selected_task_id:
                return task
        return None

    def selected_summary(self) -> SessionSummaryView | None:
        if not self.selected_task_id:
            return None
        for summary in self.filtered_session_summaries():
            if summary.task_id == self.selected_task_id:
                return summary
        return None

    def selected_runtime(self) -> RuntimeTaskState | None:
        if not self.selected_task_id:
            return None
        return self.runtime_by_task.get(self.selected_task_id)

    def select_task(self, task_id: str | None) -> BoardTaskView | None:
        self.selected_task_id = task_id
        return self._ensure_selection()

    def move_selection(self, *, column_delta: int = 0, row_delta: int = 0) -> BoardTaskView | None:
        grouped = self.tasks_by_column()
        if not any(grouped.values()):
            self.selected_task_id = None
            return None

        selected = self.selected_task()
        if selected is None:
            return self._ensure_selection()

        current_column_id = selected.column_id if selected.column_id in self.column_order else self.column_order[0]
        current_column_index = self.column_order.index(current_column_id)
        current_column_tasks = grouped.get(current_column_id, [])
        current_row_index = next(
            (index for index, item in enumerate(current_column_tasks) if item.task_id == selected.task_id),
            0,
        )

        target_column_index = current_column_index
        if column_delta:
            step = 1 if column_delta > 0 else -1
            candidate_index = current_column_index
            while 0 <= candidate_index + step < len(self.column_order):
                candidate_index += step
                candidate_tasks = grouped.get(self.column_order[candidate_index], [])
                if candidate_tasks:
                    target_column_index = candidate_index
                    break

        target_column_id = self.column_order[target_column_index]
        target_tasks = grouped.get(target_column_id, [])
        if not target_tasks:
            return selected

        if row_delta:
            target_row_index = max(0, min(len(target_tasks) - 1, current_row_index + row_delta))
        else:
            target_row_index = min(current_row_index, len(target_tasks) - 1)

        self.selected_task_id = target_tasks[target_row_index].task_id
        return target_tasks[target_row_index]

    def move_linear_selection(self, delta: int) -> BoardTaskView | None:
        tasks = self.linear_tasks()
        if not tasks:
            self.selected_task_id = None
            return None
        if not self.selected_task_id:
            self.selected_task_id = tasks[0].task_id
            return tasks[0]
        current_index = next((idx for idx, task in enumerate(tasks) if task.task_id == self.selected_task_id), 0)
        target = max(0, min(len(tasks) - 1, current_index + delta))
        self.selected_task_id = tasks[target].task_id
        return tasks[target]

    def move_session_selection(self, delta: int) -> SessionSummaryView | None:
        summaries = self.filtered_session_summaries()
        if not summaries:
            self.selected_task_id = None
            return None
        if not self.selected_task_id:
            self.selected_task_id = summaries[0].task_id
            return summaries[0]
        current_index = next((idx for idx, item in enumerate(summaries) if item.task_id == self.selected_task_id), 0)
        target = max(0, min(len(summaries) - 1, current_index + delta))
        self.selected_task_id = summaries[target].task_id
        return summaries[target]

    def metrics(self) -> BoardMetrics:
        now = time.time()
        filtered_tasks = self.filtered_tasks()
        all_tasks = self.snapshot.tasks
        runtime_updates = [runtime.updated_at for runtime in self.runtime_by_task.values()]
        stale_count = 0
        running_count = 0
        blocked_count = 0
        failed_count = 0
        active_session_count = 0

        for task in all_tasks:
            runtime = self.runtime_for(task.task_id)
            if task.session_id:
                active_session_count += 1
            if task.status in _STATUS_BLOCKED:
                blocked_count += 1
            if task.status in {"failed", "cancelled"}:
                failed_count += 1
            if task.status in _STATUS_ACTIVE or (runtime and runtime.status in _RUNTIME_ACTIVE):
                running_count += 1

            freshness = max(float(task.updated_at or 0.0), float(runtime.updated_at if runtime else 0.0))
            if freshness and not task.is_terminal and now - freshness > 600:
                stale_count += 1

        counts = self.board_counts()
        metrics = BoardMetrics(
            total_tasks=len(all_tasks) + int(self.snapshot.hidden_task_count),
            visible_tasks=len(all_tasks),
            filtered_tasks=len(filtered_tasks),
            hidden_task_count=int(self.snapshot.hidden_task_count),
            todo_count=counts.get("todo", 0),
            in_progress_count=counts.get("in-progress", 0),
            done_count=counts.get("done", 0),
            running_count=running_count,
            blocked_count=blocked_count,
            failed_count=failed_count,
            pending_checkpoint_count=int(self.snapshot.pending_checkpoint_count),
            active_session_count=active_session_count,
            stale_task_count=stale_count,
            last_refreshed_at=float(self.snapshot.last_refreshed_at),
            last_runtime_update=max(runtime_updates) if runtime_updates else None,
        )
        metrics.alert_count = len(self.alerts())
        return metrics

    def alerts(self) -> list[BoardAlert]:
        alerts: dict[str, BoardAlert] = {alert.alert_id: alert for alert in self.snapshot.alerts}
        now = time.time()
        for task in self.snapshot.tasks:
            runtime = self.runtime_for(task.task_id)
            if task.pending_checkpoint is not None:
                alerts[f"checkpoint:{task.task_id}"] = BoardAlert(
                    alert_id=f"checkpoint:{task.task_id}",
                    level="warn",
                    title="Checkpoint pending",
                    message=f"{task.title} is waiting for human feedback.",
                    task_id=task.task_id,
                    created_at=float(task.updated_at or task.created_at),
                )
            if task.status in _STATUS_BLOCKED:
                alerts[f"blocked:{task.task_id}"] = BoardAlert(
                    alert_id=f"blocked:{task.task_id}",
                    level="warn",
                    title="Task paused",
                    message=f"{task.title} is paused in `{task.status}`.",
                    task_id=task.task_id,
                    created_at=float(task.updated_at or task.created_at),
                )
            if task.status in {"failed", "cancelled"}:
                alerts[f"terminal:{task.task_id}"] = BoardAlert(
                    alert_id=f"terminal:{task.task_id}",
                    level="error",
                    title="Task ended abnormally",
                    message=f"{task.title} finished with status `{task.status}`.",
                    task_id=task.task_id,
                    created_at=float(task.updated_at or task.created_at),
                )
            freshness = max(float(task.updated_at or 0.0), float(runtime.updated_at if runtime else 0.0))
            if freshness and not task.is_terminal and now - freshness > 600:
                alerts[f"stale:{task.task_id}"] = BoardAlert(
                    alert_id=f"stale:{task.task_id}",
                    level="warn",
                    title="Stale task",
                    message=f"{task.title} has been quiet for more than 10 minutes.",
                    task_id=task.task_id,
                    created_at=freshness,
                )
        level_weight = {"error": 0, "warn": 1, "info": 2}
        return sorted(
            alerts.values(),
            key=lambda alert: (level_weight.get(alert.level, 99), -float(alert.created_at)),
        )

    def _summary_from_task(self, task: BoardTaskView) -> SessionSummaryView:
        return SessionSummaryView(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            column_id=task.column_id,
            session_id=task.session_id,
            updated_at=float(task.updated_at),
            created_at=float(task.created_at),
            assigned_to=task.assigned_to,
            priority=task.priority,
            pending_checkpoint=task.pending_checkpoint is not None,
            linked_task_count=task.linked_task_count,
            tags=list(task.tags),
            runtime_task_id=task.runtime_task_id,
            execution_turn_id=task.execution_turn_id or task.runtime_task_id,
        )

    def _is_live_summary(self, summary: SessionSummaryView) -> bool:
        runtime = self.runtime_for(summary.task_id)
        return (
            summary.pending_checkpoint
            or summary.column_id == "in-progress"
            or summary.status in _STATUS_ACTIVE
            or (runtime is not None and runtime.status in _RUNTIME_ACTIVE)
        )

    def _matches_query(self, *parts: str) -> bool:
        if not self.search_query:
            return True
        haystack = " ".join(part for part in parts if part).casefold()
        return self.search_query.casefold() in haystack

    def _ensure_selection(self) -> BoardTaskView | None:
        selected = self.selected_task()
        if selected is not None:
            return selected
        grouped = self.tasks_by_column()
        for column_id in self.column_order:
            tasks = grouped.get(column_id, [])
            if tasks:
                self.selected_task_id = tasks[0].task_id
                return tasks[0]
        self.selected_task_id = None
        return None
