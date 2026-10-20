"""Session tree widget for the CLI board.

Displays tasks in a tree structure where parent tasks are expandable nodes
and child work items appear as leaves beneath them.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import BoardTaskView, SessionSummaryView
from ..state.store import BoardStateStore
from .render_utils import badge, humanize_age, priority_style, status_style, truncate_text

_STATUS_SYMBOL = {
    "done": ("\u2713", "bold #22c55e"),
    "running": ("\u25cf", "bold #38bdf8"),
    "idle": ("\u25cf", "bold #38bdf8"),
    "pending": ("\u25cb", "dim"),
    "failed": ("\u2717", "bold #ef4444"),
    "cancelled": ("\u2717", "bold #9333ea"),
    "blocked": ("\u25a0", "bold #f59e0b"),
    "awaiting_peer": ("\u25a0", "bold #f59e0b"),
    "awaiting_review": ("\u25a0", "bold #f59e0b"),
}


class SessionSidebarWidget(Static):
    """Render a session tree with parent-child relationships."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="session-sidebar")
        self.state = state

    def render(self) -> RenderableType:
        company_mode = self.state.snapshot.mode == "company"
        title = "Runtime Sessions" if company_mode else "Sessions"
        if self.state.pane_focus == "session-rail":
            title += " [Focused]"

        tree = self._build_tree()
        if not tree:
            return Panel(
                Text("No runtime sessions available." if company_mode else "No sessions available.", style="dim"),
                title=title,
                border_style="cyan" if self.state.pane_focus == "session-rail" else "white",
            )

        return Panel(
            Group(*tree),
            title=title,
            border_style="cyan" if self.state.pane_focus == "session-rail" else "white",
        )

    def _build_tree(self) -> list[RenderableType]:
        """Build a tree from tasks, grouping children under parents."""
        all_tasks = self.state.filtered_tasks()
        if not all_tasks:
            return []

        # Separate parent (visible/top-level) tasks from child tasks
        # Child tasks have origin_task_id pointing to their parent
        parent_tasks: list[BoardTaskView] = []
        children_by_parent: dict[str, list[BoardTaskView]] = {}

        # First pass: identify parents and children from the visible task list
        # Also look at linked executions from the snapshot data
        visible_ids = {t.task_id for t in all_tasks}

        for task in all_tasks:
            if task.origin_task_id and task.origin_task_id in visible_ids:
                children_by_parent.setdefault(task.origin_task_id, []).append(task)
            else:
                parent_tasks.append(task)

        # Also pull in hidden linked tasks from snapshot metadata
        for task in self.state.all_tasks():
            if task.task_id in visible_ids:
                continue
            if task.origin_task_id and task.origin_task_id in visible_ids:
                children_by_parent.setdefault(task.origin_task_id, []).append(task)

        # Sort parents: live first, then queue, then archive
        parent_tasks.sort(key=lambda t: (
            0 if self._is_live(t) else 1 if t.column_id != "done" else 2,
            -float(t.updated_at),
        ))

        lines: list[RenderableType] = []
        for parent in parent_tasks:
            children = children_by_parent.get(parent.task_id, [])
            children.sort(key=lambda t: (float(t.created_at), t.title))
            lines.append(self._render_parent(parent, children))

        return lines

    def _render_parent(self, task: BoardTaskView, children: list[BoardTaskView]) -> Text:
        selected = task.task_id == self.state.selected_task_id
        runtime = self.state.runtime_for(task.task_id)
        has_children = bool(children)
        sym, sym_style = _STATUS_SYMBOL.get(task.status, ("\u25cb", "dim"))

        text = Text()
        # Expand/collapse indicator
        if has_children:
            text.append("\u25bc ", style="bold white")  # ▼
        else:
            text.append("  ", style="")

        # Selection marker
        if selected:
            text.append("\u25c6 ", style="bold cyan")  # ◆
        else:
            text.append("  ", style="")

        # Status + title
        text.append(f"{sym} ", style=sym_style)
        title_style = "bold white" if selected else "white"
        text.append(truncate_text(task.title, 22), style=title_style)
        text.append(f"  {humanize_age(task.updated_at)}", style="dim")

        # Runtime info
        if runtime and runtime.current_tool:
            text.append(f"  \u2699{runtime.current_tool}", style="dim")

        # Badges on next line if relevant
        if task.pending_checkpoint or task.priority:
            text.append("\n    ")
            if task.priority:
                text += badge(task.priority.upper(), priority_style(task.priority))
                text.append(" ")
            if task.pending_checkpoint:
                text += badge("REVIEW", status_style("warn"))

        # Children
        for i, child in enumerate(children[:10]):
            is_last = i == len(children) - 1 or i == 9
            text.append("\n")
            text.append(self._render_child(child, is_last=is_last))

        if len(children) > 10:
            text.append(f"\n       \u2026 +{len(children) - 10} more", style="dim")

        text.append("\n")
        return text

    def _render_child(self, task: BoardTaskView, *, is_last: bool) -> Text:
        selected = task.task_id == self.state.selected_task_id
        runtime = self.state.runtime_for(task.task_id)
        sym, sym_style = _STATUS_SYMBOL.get(task.status, ("\u25cb", "dim"))

        connector = "  \u2514\u2500 " if is_last else "  \u251c\u2500 "  # └─ or ├─
        text = Text()
        text.append(connector, style="dim")

        if selected:
            text.append(f"{sym} ", style=sym_style)
            text.append(truncate_text(task.title, 18), style="bold cyan")
        else:
            text.append(f"{sym} ", style=sym_style)
            text.append(truncate_text(task.title, 18), style="white")

        # Assignee
        if task.assigned_to:
            text.append(f"  {truncate_text(task.assigned_to, 10)}", style="dim")

        # Elapsed
        text.append(f"  {humanize_age(task.updated_at)}", style="dim")

        # Current tool
        if runtime and runtime.current_tool:
            text.append(f"  \u2699{truncate_text(runtime.current_tool, 12)}", style="dim")

        return text

    @staticmethod
    def _is_live(task: BoardTaskView) -> bool:
        return task.status in {"running", "idle", "blocked", "awaiting_peer", "awaiting_review"} or task.pending_checkpoint is not None
