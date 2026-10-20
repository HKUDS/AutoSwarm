"""Session transcript pane."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import TaskDetailView
from ..state.store import BoardStateStore
from .render_utils import format_clock, role_style, status_style, truncate_text


class SessionPaneWidget(Static):
    """Render transcript and live progress for the selected task."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="session-pane")
        self.state = state
        self.detail: TaskDetailView | None = None

    def set_detail(self, detail: TaskDetailView | None) -> None:
        self.detail = detail
        self.refresh()

    def render(self) -> RenderableType:
        focused = self.state.pane_focus == "context" and self.state.context_tab == "session"
        company_mode = self.state.snapshot.mode == "company"
        title = "Runtime Session" if company_mode else "Session"
        if self.detail is None:
            return Panel(
                Text("No Runtime Session selected." if company_mode else "No session selected.", style="dim"),
                title=f"{title} [Focused]" if focused else title,
                border_style="cyan" if focused else "white",
            )

        messages: list[RenderableType] = []
        for message in self.detail.transcript[-14:]:
            block = Text()
            block.append(f"[{format_clock(message.created_at)}] ", style="dim")
            block.append(message.sender_name, style=role_style(message.role))
            block.append("\n")
            # Show content with natural line breaks — let the panel wrap
            content = message.content.strip()
            lines = content.split("\n")
            for line in lines[:20]:
                block.append(f"{line}\n", style="white")
            if len(lines) > 20:
                block.append(f"\u2026 ({len(lines) - 20} more lines)\n", style="dim")
            messages.append(block)

        progress_entries = list(self.detail.progress_entries)
        runtime = self.state.runtime_for(self.detail.task.task_id)
        if runtime and runtime.progress_entries:
            progress_entries.extend(runtime.progress_entries[-10:])

        if progress_entries:
            progress_title = "Execution Progress Timeline" if company_mode else "Progress Timeline"
            progress = Text(f"\n{progress_title}\n", style="bold #cbd5e1")
            for entry in progress_entries[-10:]:
                progress.append(f"• {truncate_text(entry, 90)}\n", style="dim")
            messages.append(progress)

        if runtime and (
            runtime.current_tool
            or runtime.context_window > 0
            or runtime.turn_cost_usd > 0
            or runtime.pending_permission_count > 0
        ):
            tail = Text("\nRuntime\n", style="bold #cbd5e1")
            tail.append(f"status {runtime.status}", style=status_style(runtime.status))
            if runtime.current_tool:
                tail.append(f"  tool {runtime.current_tool}", style="dim")
            if runtime.iteration:
                tail.append(f"  iter {runtime.iteration}", style="dim")
            if runtime.tool_elapsed_ms > 0:
                tail.append(f"  {runtime.tool_elapsed_ms}ms", style="dim")
            if runtime.last_tool_summary:
                tail.append(f"\nsummary {truncate_text(runtime.last_tool_summary, 90)}", style="dim")
            if runtime.context_window > 0:
                tail.append(
                    f"\ncontext {runtime.context_tokens}/{runtime.context_window} ({runtime.context_remaining_pct}% left)",
                    style="dim",
                )
            if runtime.turn_cost_usd > 0 or runtime.session_cost_usd > 0:
                tail.append(
                    f"\ncost turn=${runtime.turn_cost_usd:.4f} session=${runtime.session_cost_usd:.4f}",
                    style="dim",
                )
            if runtime.pending_permission_count > 0:
                tail.append(f"\napprovals pending {runtime.pending_permission_count}", style="bold yellow")
            messages.append(tail)

        if not messages:
            messages.append(Text("No transcript recorded yet.", style="dim"))

        return Panel(
            Group(*messages),
            title=f"{title} [Focused]" if focused else title,
            border_style="cyan" if focused else "white",
        )
