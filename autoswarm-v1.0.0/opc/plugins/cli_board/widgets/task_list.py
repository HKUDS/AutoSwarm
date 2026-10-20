"""Dense task list view."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.store import BoardStateStore
from .render_utils import humanize_age, priority_style, status_style, truncate_text


class TaskListWidget(Static):
    """Render a dense linear task view."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="task-list")
        self.state = state

    def render(self) -> RenderableType:
        tasks = self.state.linear_tasks()
        rows: list[RenderableType] = [self._header_row()]
        item_label = "work items" if self.state.snapshot.mode == "company" else "tasks"
        if not tasks:
            rows.append(Text(f"No {item_label} match the current filter.", style="dim"))
        for task in tasks:
            rows.append(self._task_row(task))
        title = "Work Item List" if self.state.snapshot.mode == "company" else "Task List"
        if self.state.pane_focus == "main":
            title += " [Focused]"
        return Panel(Group(*rows), title=title, border_style="cyan" if self.state.pane_focus == "main" else "white")

    def _header_row(self) -> Text:
        row = Text(style="bold #94a3b8")
        row.append(("WORK ITEM" if self.state.snapshot.mode == "company" else "TASK").ljust(10))
        row.append("TITLE".ljust(30))
        row.append("STATUS".ljust(16))
        row.append("OWNER".ljust(16))
        row.append("AGE")
        return row

    def _task_row(self, task) -> Text:
        selected = task.task_id == self.state.selected_task_id
        row = Text(style="black on #22d3ee" if selected else "white")
        row.append((task.display_id or task.task_id[:8]).ljust(10), style="bold")
        row.append(truncate_text(task.title, 28).ljust(30))
        row.append(task.status[:14].upper().ljust(16), style=status_style(task.status))
        row.append(truncate_text(task.assigned_to or "-", 14).ljust(16), style="dim" if not selected else "black on #22d3ee")
        row.append(humanize_age(task.updated_at), style="dim" if not selected else "black on #22d3ee")
        if task.priority:
            row.append(" ")
            row.append(task.priority[0].upper(), style=priority_style(task.priority))
        if task.pending_checkpoint:
            row.append(" !", style=status_style("warn"))
        return row
