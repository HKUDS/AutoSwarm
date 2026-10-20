"""Top metrics bar for the CLI board."""

from __future__ import annotations

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.store import BoardStateStore
from .render_utils import badge, format_clock, humanize_age, priority_style, status_style, truncate_text


class MetricsBarWidget(Static):
    """Show board-wide health, activity, and viewport state."""

    def __init__(self, state: BoardStateStore, *, exec_mode: str = "task", company_profile: str = "corporate") -> None:
        super().__init__(id="metrics-bar")
        self.state = state
        self.exec_mode = exec_mode
        self.company_profile = company_profile
        self.pipeline_done: int = 0
        self.pipeline_total: int = 0

    def render(self) -> RenderableType:
        metrics = self.state.metrics()
        selected = self.state.selected_task()
        alerts = self.state.alerts()[:3]
        item_label = "work item" if self.state.snapshot.mode == "company" else "task"

        board_text = Text()
        board_text.append(f"{self.state.snapshot.project_id}\n", style="bold white")
        board_text.append("view ", style="dim")
        board_text.append(self.state.view_mode.upper(), style="bold cyan")
        board_text.append("  focus ", style="dim")
        board_text.append(self.state.pane_focus, style="bold white")
        board_text.append("  density ", style="dim")
        board_text.append(self.state.density_mode, style="bold magenta")
        board_text.append("\nmode ", style="dim")
        board_text.append(self.exec_mode, style="bold #22c55e")
        board_text.append("/", style="dim")
        board_text.append(self.company_profile, style="bold #22c55e")
        if self.state.search_query:
            board_text.append("\nfilter ", style="dim")
            board_text.append(truncate_text(self.state.search_query, 24), style="yellow")

        flow_text = Text()
        flow_text.append("todo ", style="dim")
        flow_text.append(str(metrics.todo_count), style="bold white")
        flow_text.append("  active ", style="dim")
        flow_text.append(str(metrics.in_progress_count), style="bold #22c55e")
        flow_text.append("  done ", style="dim")
        flow_text.append(str(metrics.done_count), style="bold #10b981")
        flow_text.append("\nrun ", style="dim")
        flow_text.append(str(metrics.running_count), style="bold #38bdf8")
        flow_text.append("  chk ", style="dim")
        flow_text.append(str(metrics.pending_checkpoint_count), style="bold #f59e0b")
        if self.pipeline_total > 0:
            ratio = min(1.0, self.pipeline_done / self.pipeline_total)
            bar_w = 8
            filled = int(ratio * bar_w)
            flow_text.append("\nproj ", style="dim")
            flow_text.append("\u2588" * filled, style="bold #22c55e")
            flow_text.append("\u2591" * (bar_w - filled), style="dim")
            flow_text.append(f" {self.pipeline_done}/{self.pipeline_total}", style="white")

        health_text = Text()
        health_text.append("last refresh ", style="dim")
        health_text.append(format_clock(metrics.last_refreshed_at), style="bold white")
        health_text.append("  stale ", style="dim")
        health_text.append(str(metrics.stale_task_count), style="bold yellow")
        if metrics.last_runtime_update:
            health_text.append("\nruntime heartbeat ", style="dim")
            health_text.append(humanize_age(metrics.last_runtime_update), style="bold #22c55e")
        else:
            health_text.append("\nruntime heartbeat ", style="dim")
            health_text.append("idle", style="bold #64748b")

        if alerts:
            alert_lines: list[Text] = []
            for alert in alerts:
                line = Text()
                line += badge(alert.level.upper(), status_style(alert.level))
                line.append(f" {truncate_text(alert.title, 18)}", style="bold white")
                line.append(f"\n{truncate_text(alert.message, 42)}", style="dim")
                alert_lines.append(line)
            attention_renderable: RenderableType = Group(*alert_lines)
        else:
            attention_renderable = Text("No active alerts.\nBoard health looks stable.", style="dim")

        selection_text = Text()
        if selected is None:
            selection_text.append(
                f"No {item_label} selected.\nUse the session rail or board to pick a {item_label}.",
                style="dim",
            )
        else:
            selection_text.append(f"{truncate_text(selected.title, 28)}\n", style="bold white")
            selection_text += badge(selected.status.upper(), status_style(selected.status))
            if selected.priority:
                selection_text.append(" ")
                selection_text += badge(selected.priority.upper(), priority_style(selected.priority))
            if selected.pending_checkpoint:
                selection_text.append(" ")
                selection_text += badge("REVIEW", status_style("warn"))
            runtime = self.state.runtime_for(selected.task_id)
            if runtime and runtime.current_tool:
                selection_text.append(f"\n\u2699{truncate_text(runtime.current_tool, 16)}", style="dim")
            selection_text.append(f"  {humanize_age(selected.updated_at)}", style="dim")

        panels = [
            Panel(board_text, title="Board", border_style="cyan"),
            Panel(flow_text, title="Projection", border_style="green"),
            Panel(health_text, title="Health", border_style="magenta"),
            Panel(attention_renderable, title="Attention", border_style="yellow"),
            Panel(selection_text, title="Selection", border_style="blue"),
        ]
        return Columns(panels, expand=True, equal=True)
