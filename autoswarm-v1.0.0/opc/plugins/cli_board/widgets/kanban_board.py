"""Main Kanban board render widget."""

from __future__ import annotations

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from opc.presentation.kanban import DEFAULT_KANBAN_COLUMNS

from ..state.store import BoardStateStore
from .render_utils import adaptive_summary, badge, humanize_age, priority_style, status_style, truncate_text


class KanbanBoardWidget(Static):
    """Render the board columns from the current state store."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="kanban-board")
        self.state = state

    _TASK_COLUMN_HINTS = {
        "todo": "Press n to create a task",
        "in-progress": "Select a task, press g to run",
        "done": "Completed tasks appear here",
    }
    _WORK_ITEM_COLUMN_HINTS = {
        "todo": "Press n to create a work item",
        "in-progress": "Select a work item, press g to run",
        "done": "Completed work items appear here",
    }

    def _company_mode(self) -> bool:
        return self.state.snapshot.mode == "company"

    def render(self) -> RenderableType:
        grouped = self.state.tasks_by_column()
        total_tasks = sum(len(v) for v in grouped.values())
        focused = self.state.pane_focus == "main" and self.state.view_mode == "kanban"

        # Empty board: show welcome guide instead of empty columns
        if total_tasks == 0:
            return self._render_welcome(focused)

        panels: list[Panel] = []
        for column in DEFAULT_KANBAN_COLUMNS:
            tasks = grouped.get(column.column_id, [])
            is_selected_column = any(task.task_id == self.state.selected_task_id for task in tasks)
            active_count = sum(1 for task in tasks if task.status not in {"done", "failed", "cancelled"})
            checkpoint_count = sum(1 for task in tasks if task.pending_checkpoint is not None)
            title = f"{column.name} {len(tasks)}"
            subtitle_bits = []
            if active_count:
                subtitle_bits.append(f"live {active_count}")
            if checkpoint_count:
                subtitle_bits.append(f"review {checkpoint_count}")
            if tasks:
                renderables: list[RenderableType] = [self._render_task(task) for task in tasks]
            else:
                hints = self._WORK_ITEM_COLUMN_HINTS if self._company_mode() else self._TASK_COLUMN_HINTS
                renderables = [Text(hints.get(column.column_id, ""), style="dim")]
            panels.append(
                Panel(
                    Group(*renderables),
                    title=title,
                    subtitle=" | ".join(subtitle_bits) if subtitle_bits else "",
                    border_style="cyan" if focused and is_selected_column else "white",
                    padding=(0, 1),
                )
            )
        return Columns(panels, expand=True, equal=True)

    def _render_welcome(self, focused: bool) -> RenderableType:
        item_label = "work item" if self._company_mode() else "task"
        text = Text()
        text.append("AutoSwarm CLI Board\n\n", style="bold #38bdf8")
        text.append("Get started:\n", style="bold white")
        text.append("  n", style="bold cyan")
        text.append(f"   Create a {item_label} - describe what you need\n", style="white")
        text.append("  E", style="bold cyan")
        text.append("   Switch mode \u2014 choose task or company\n", style="white")
        text.append("  ?", style="bold cyan")
        text.append("   Help \u2014 see all keyboard shortcuts\n\n", style="white")
        text.append("Quick start:\n", style="bold white")
        text.append("  1. Press ", style="dim")
        text.append("n", style="bold cyan")
        text.append(f", type your {item_label} description\n", style="dim")
        text.append("  2. Press ", style="dim")
        text.append("g", style="bold cyan")
        text.append(" to run it with an agent\n", style="dim")
        text.append("  3. Press ", style="dim")
        text.append("s", style="bold cyan")
        text.append(" to chat with the agent\n", style="dim")
        text.append("  4. Press ", style="dim")
        text.append("3", style="bold cyan")
        text.append(" to see full conversation\n", style="dim")
        return Panel(text, title="Welcome [Focused]" if focused else "Welcome",
                     border_style="cyan" if focused else "white")

    def _render_task(self, task) -> Text:
        runtime = self.state.runtime_for(task.task_id)
        adaptive = adaptive_summary(task.metadata)
        selected = task.task_id == self.state.selected_task_id
        compact = self.state.density_mode == "compact"

        text = Text()
        title_style = "bold black on #22d3ee" if selected else "bold white"
        meta_style = "black on #22d3ee" if selected else "dim"
        marker = "◆" if selected else "•"
        title = truncate_text(task.title, 28 if compact else 34)
        prefix = f"{task.display_id or task.task_id[:8]} "
        text.append(f"{marker} {prefix}{title}\n", style=title_style)

        badges = Text(style=meta_style)
        badges += badge(task.status.upper(), status_style(task.status))
        if task.priority:
            badges.append(" ")
            badges += badge(task.priority.upper(), priority_style(task.priority))
        if task.pending_checkpoint:
            badges.append(" ")
            badges += badge("REVIEW", status_style("warn"))
        if runtime and runtime.status not in {"idle", ""}:
            badges.append(" ")
            badges += badge(runtime.status.upper(), status_style(runtime.status))
        if task.linked_task_count:
            badges.append(f"  linked:{task.linked_task_count}", style=meta_style)
        text.append_text(badges)

        meta = Text("\n", style=meta_style)
        owner = truncate_text(task.assigned_to or "unassigned", 18 if compact else 20)
        meta.append(f"{owner}", style=meta_style)
        if runtime and runtime.current_tool:
            meta.append(f"  \u2699{truncate_text(runtime.current_tool, 14)}", style=meta_style)
        if runtime and runtime.iteration > 0:
            meta.append(f"  iter:{runtime.iteration}", style=meta_style)
        meta.append(f"  {humanize_age(task.updated_at)}", style=meta_style)
        text.append_text(meta)

        if not compact:
            # Latest progress message
            if runtime and runtime.progress_entries:
                last_progress = truncate_text(runtime.progress_entries[-1], 48)
                text.append(f"\n> {last_progress}", style="italic " + meta_style)
            elif adaptive["blocked_reason"]:
                text.append(
                    f"\n! {truncate_text(adaptive['blocked_reason'], 48)}",
                    style="italic " + meta_style,
                )
            elif task.description:
                text.append(f"\n{truncate_text(task.description, 52)}", style="white" if not selected else "black on #22d3ee")
            adaptive_bits = []
            if adaptive["invalidated"]:
                adaptive_bits.append("invalidated")
            if adaptive["gate_owner"]:
                adaptive_bits.append(f"gate:{truncate_text(adaptive['gate_owner'], 12)}")
            if adaptive["missing_signals"]:
                adaptive_bits.append(
                    f"signals:{truncate_text(', '.join(adaptive['missing_signals']), 18)}"
                )
            if adaptive["confidence_label"]:
                adaptive_bits.append(f"confidence:{adaptive['confidence_label']}")
            if adaptive_bits:
                text.append(
                    f"\n{truncate_text(' | '.join(adaptive_bits), 52)}",
                    style="italic " + meta_style,
                )

        text.append("\n")
        return text
