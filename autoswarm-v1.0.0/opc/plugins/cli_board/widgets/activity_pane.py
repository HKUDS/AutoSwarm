"""Activity and alert pane."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import TaskDetailView
from ..state.store import BoardStateStore
from .render_utils import badge, format_clock, status_style, truncate_text


class ActivityPaneWidget(Static):
    """Render board alerts and recent task activity."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="activity-pane")
        self.state = state
        self.detail: TaskDetailView | None = None

    def set_detail(self, detail: TaskDetailView | None) -> None:
        self.detail = detail
        self.refresh()

    def render(self) -> RenderableType:
        rows: list[RenderableType] = []
        runtime = None
        if self.detail is not None:
            runtime = self.state.runtime_for(self.detail.task.task_id)
            summary = Text()
            summary.append(self.detail.task.title, style="bold white")
            summary.append("\n")
            summary += badge(self.detail.task.status.upper(), status_style(self.detail.task.status))
            if runtime and runtime.status not in {"idle", ""}:
                summary.append(" ")
                summary += badge(runtime.status.upper(), status_style(runtime.status))
            if runtime and runtime.current_tool:
                summary.append(f"\nactive tool: {runtime.current_tool}", style="dim")
            rows.append(summary)

        alerts = self.state.alerts()[:6]
        if alerts:
            alert_text = Text("\nBoard alerts\n", style="bold #cbd5e1")
            for alert in alerts:
                alert_text += badge(alert.level.upper(), status_style(alert.level))
                alert_text.append(f" {truncate_text(alert.title, 26)}", style="bold white")
                alert_text.append(f"\n{truncate_text(alert.message, 76)}\n", style="dim")
            rows.append(alert_text)

        if runtime and runtime.progress_entries:
            recent = Text("\nLive runtime tail\n", style="bold #cbd5e1")
            for entry in runtime.progress_entries[-10:]:
                recent.append(f"[{format_clock(runtime.updated_at)}] {truncate_text(entry, 76)}\n", style="dim")
            rows.append(recent)

        if not rows:
            rows.append(Text("No activity yet.", style="dim"))

        title = "Activity"
        if self.state.pane_focus == "context" and self.state.context_tab == "activity":
            title += " [Focused]"
        return Panel(
            Group(*rows),
            title=title,
            border_style="cyan" if self.state.pane_focus == "context" and self.state.context_tab == "activity" else "white",
        )
