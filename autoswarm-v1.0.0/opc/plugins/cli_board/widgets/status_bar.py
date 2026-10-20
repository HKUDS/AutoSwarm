"""Bottom status bar — context-aware action guide."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..state.store import BoardStateStore
from .render_utils import truncate_text


class StatusBarWidget(Static):
    """Show available actions based on current state, plus key status info."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="status-bar")
        self.state = state
        self.message = ""
        self.exec_mode = "task"
        self.company_profile = "corporate"

    def set_message(self, message: str) -> None:
        self.message = str(message or "").strip()
        self.refresh()

    def render(self) -> Text:
        selected = self.state.selected_task()
        metrics = self.state.metrics()
        text = Text()

        # Flash message (temporary status, shown prominently)
        if self.message:
            text.append(f"{self.message}  ", style="bold white")
            text.append("\u2502 ", style="dim")

        # Context-aware action hints
        actions = self._build_action_hints(selected)
        text.append(actions)

        # Separator + key status
        text.append("  \u2502 ", style="dim")
        text.append(self.state.view_mode, style="bold cyan")
        text.append("  ", style="dim")
        text.append(f"{self.exec_mode}/{self.company_profile}", style="bold #22c55e")

        if metrics.visible_tasks > 0:
            item_label = "work items" if self.state.snapshot.mode == "company" else "tasks"
            text.append(f"  {metrics.visible_tasks} {item_label}", style="dim")

        if selected:
            text.append("  \u2502 ", style="dim")
            text.append(truncate_text(selected.title, 24), style="bold #22c55e")
            runtime = self.state.runtime_for(selected.task_id)
            if runtime:
                if runtime.current_tool:
                    text.append("  \u2502 ", style="dim")
                    text.append(f"\u2699 {truncate_text(runtime.current_tool, 18)}", style="bold #f59e0b")
                if runtime.context_window > 0:
                    text.append("  \u2502 ", style="dim")
                    text.append(f"ctx {runtime.context_remaining_pct}%", style="bold #38bdf8")
                if runtime.turn_cost_usd > 0 or runtime.session_cost_usd > 0:
                    text.append("  \u2502 ", style="dim")
                    text.append(
                        f"${runtime.turn_cost_usd:.4f}/${runtime.session_cost_usd:.4f}",
                        style="bold #22c55e",
                    )
                if runtime.pending_permission_count > 0:
                    text.append("  \u2502 ", style="dim")
                    text.append(f"approvals {runtime.pending_permission_count}", style="bold #f59e0b")

        if self.state.search_query:
            text.append("  \u2502 ", style="dim")
            text.append(f"\u2315 {truncate_text(self.state.search_query, 16)}", style="yellow")

        return text

    def _build_action_hints(self, selected: object | None) -> Text:
        hints = Text()

        if selected is None:
            # No task selected
            hints.append("n", style="bold cyan")
            hints.append(" new  ", style="dim")
            hints.append("/", style="bold cyan")
            hints.append(" search  ", style="dim")
            hints.append("?", style="bold cyan")
            hints.append(" help", style="dim")
            return hints

        # Task selected — show relevant actions
        task = selected
        has_checkpoint = getattr(task, "pending_checkpoint", None) is not None
        status = getattr(task, "status", "")
        is_terminal = status in {"done", "failed", "cancelled"}

        hints.append("n", style="bold cyan")
        hints.append(" new  ", style="dim")

        if not is_terminal:
            hints.append("g", style="bold cyan")
            hints.append(" run  ", style="dim")
            hints.append("s", style="bold cyan")
            hints.append(" chat  ", style="dim")

        if has_checkpoint:
            hints.append("\u26a1", style="bold #f59e0b")
            hints.append(" ", style="")
            hints.append("a", style="bold #22c55e")
            hints.append("/", style="dim")
            hints.append("d", style="bold #ef4444")
            hints.append(" approve/deny  ", style="dim")
            hints.append("e", style="bold cyan")
            hints.append(" feedback  ", style="dim")

        if is_terminal:
            hints.append("t", style="bold cyan")
            hints.append(" retry  ", style="dim")

        hints.append("3", style="bold cyan")
        hints.append(" chat view  ", style="dim")
        hints.append("?", style="bold cyan")
        hints.append(" help", style="dim")

        return hints
