"""Chat-focused task view — full conversation flow with action hints."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import TaskDetailView
from ..state.store import BoardStateStore
from .render_utils import badge, format_clock, humanize_age, status_style, truncate_text


class FocusTaskWidget(Static):
    """Render a chat-centric view for the selected task."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="focus-view")
        self.state = state
        self.detail: TaskDetailView | None = None

    def set_detail(self, detail: TaskDetailView | None) -> None:
        self.detail = detail
        self.refresh()

    def render(self) -> RenderableType:
        focused = self.state.pane_focus == "main" and self.state.view_mode == "focus"
        item_label = "work item" if self.state.snapshot.mode == "company" else "task"
        if self.detail is None:
            guide = Text()
            guide.append(f"Select a {item_label} and press ", style="dim")
            guide.append("3", style="bold cyan")
            guide.append(" to open chat view.\n\n", style="dim")
            guide.append("Or press ", style="dim")
            guide.append("n", style="bold cyan")
            guide.append(f" to create a new {item_label}.", style="dim")
            return Panel(guide, title="Chat View", border_style="cyan" if focused else "white")

        task = self.detail.task
        runtime = self.state.runtime_for(task.task_id)
        blocks: list[RenderableType] = []

        # ── Header: title + status + action hints (always visible) ──
        blocks.append(self._render_header(task, runtime))
        blocks.append(self._render_action_hints(task))

        # ── Conversation flow (main content) ──
        blocks.append(self._render_conversation())

        # ── Live progress tail ──
        blocks.append(self._render_progress(runtime))

        # ── Panel title includes task info ──
        status_sym = {
            "done": "\u2713", "running": "\u25cf", "idle": "\u25cf",
            "pending": "\u25cb", "failed": "\u2717", "cancelled": "\u2717",
            "blocked": "\u25a0",
        }.get(task.status, "\u25cb")
        title_text = f"Chat: {truncate_text(task.title, 36)}  {status_sym} {task.status}"
        if runtime and runtime.current_tool:
            title_text += f"  \u2699{truncate_text(runtime.current_tool, 16)}"

        return Panel(
            Group(*blocks),
            title=title_text,
            border_style="cyan" if focused else "white",
        )

    def _render_header(self, task: object, runtime: object) -> Text:
        text = Text()
        text.append(f"{getattr(task, 'display_id', '') or getattr(task, 'task_id', '')[:8]}", style="bold #38bdf8")
        text.append(f"  {getattr(task, 'title', '')}", style="bold white")
        if getattr(task, 'assigned_to', ''):
            text.append(f"  \u2022 {task.assigned_to}", style="dim")
        text.append(f"  {humanize_age(getattr(task, 'updated_at', 0))}", style="dim")

        # Badges on second line
        text.append("\n")
        text += badge(getattr(task, 'status', '').upper(), status_style(getattr(task, 'status', '')))
        if getattr(task, 'pending_checkpoint', None):
            text.append(" ")
            text += badge("REVIEW", status_style("warn"))
        if runtime and getattr(runtime, 'iteration', 0) > 0:
            text.append(f"  iter:{runtime.iteration}", style="dim")
        text.append("\n")
        return text

    def _render_conversation(self) -> Text:
        text = Text()
        transcript = self.detail.transcript if self.detail else []

        if not transcript:
            text.append("\nNo conversation yet.\n", style="dim")
            text.append("Press ", style="dim")
            text.append("g", style="bold cyan")
            text.append(" to run this work item, or " if self.state.snapshot.mode == "company" else " to run this task, or ", style="dim")
            text.append("s", style="bold cyan")
            text.append(" to send a message.\n", style="dim")
            return text

        text.append("\n")
        for msg in transcript[-20:]:
            timestamp = format_clock(msg.created_at)
            role = msg.role
            sender = msg.sender_name

            # Role-based styling
            if role == "user":
                name_style = "bold #38bdf8"
            elif role in {"assistant", "subagent"}:
                name_style = "bold #22c55e"
            elif role == "system":
                name_style = "bold #f59e0b"
            else:
                name_style = "bold white"

            text.append(f"  [{timestamp}] ", style="dim")
            text.append(sender, style=name_style)
            text.append("\n")

            # Message content — show full text, let terminal wrap naturally
            content = msg.content.strip()
            lines = content.split("\n")
            for line in lines[:30]:
                text.append(f"  {line}\n", style="white")
            if len(lines) > 30:
                text.append(f"  \u2026 ({len(lines) - 30} more lines)\n", style="dim")
            text.append("\n")

        return text

    def _render_progress(self, runtime: object) -> Text:
        text = Text()
        entries = list(self.detail.progress_entries) if self.detail else []
        if runtime and getattr(runtime, "progress_entries", None):
            entries.extend(runtime.progress_entries[-6:])

        if not entries:
            return text

        text.append("\u2500" * 50 + "\n", style="dim")
        for entry in entries[-6:]:
            text.append(f"  {truncate_text(entry, 90)}\n", style="dim italic")

        return text

    def _render_action_hints(self, task: object) -> Text:
        text = Text("\n")
        has_checkpoint = getattr(task, "pending_checkpoint", None) is not None
        is_terminal = getattr(task, "status", "") in {"done", "failed", "cancelled"}

        text.append("s", style="bold cyan")
        text.append(" reply", style="dim")

        if has_checkpoint:
            text.append("  \u2502 ", style="dim")
            text.append("\u26a1 ", style="bold #f59e0b")
            text.append("a", style="bold #22c55e")
            text.append(" approve  ", style="dim")
            text.append("d", style="bold #ef4444")
            text.append(" deny  ", style="dim")
            text.append("e", style="bold cyan")
            text.append(" feedback", style="dim")

        if not is_terminal:
            text.append("  \u2502 ", style="dim")
            text.append("g", style="bold cyan")
            text.append(" run", style="dim")
        else:
            text.append("  \u2502 ", style="dim")
            text.append("t", style="bold cyan")
            text.append(" retry", style="dim")

        text.append("  \u2502 ", style="dim")
        text.append("1", style="bold cyan")
        text.append(" board", style="dim")

        return text
