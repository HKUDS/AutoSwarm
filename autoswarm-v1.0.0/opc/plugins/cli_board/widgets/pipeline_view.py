"""Read-only work-item projection visualisation for company-mode runs."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import PipelineSnapshot, PipelineWorkItemView
from ..state.store import BoardStateStore
from .render_utils import status_style, truncate_text

_STATUS_SYMBOL = {
    "done": ("\u2713", "bold #22c55e"),       # ✓ green
    "running": ("\u25cf", "bold #38bdf8"),     # ● blue
    "idle": ("\u25cf", "bold #38bdf8"),        # ● blue
    "pending": ("\u25cb", "dim"),              # ○ gray
    "failed": ("\u2717", "bold #ef4444"),      # ✗ red
    "cancelled": ("\u2717", "bold #9333ea"),   # ✗ purple
    "blocked": ("\u25a0", "bold #f59e0b"),     # ■ yellow
    "awaiting_peer": ("\u25a0", "bold #f59e0b"),
    "awaiting_review": ("\u25a0", "bold #f59e0b"),
}


def _fmt_elapsed(sec: float) -> str:
    if sec <= 0:
        return "--"
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec // 60)}m"
    return f"{int(sec // 3600)}h{int((sec % 3600) // 60)}m"


class PipelineViewWidget(Static):
    """Render the work-item projection as an ASCII dependency view."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="pipeline-view")
        self.state = state
        self.pipeline: PipelineSnapshot | None = None

    def set_pipeline(self, pipeline: PipelineSnapshot | None) -> None:
        self.pipeline = pipeline
        self.refresh()

    def render(self) -> RenderableType:
        focused = self.state.pane_focus == "main" and self.state.view_mode == "pipeline"
        if self.pipeline is None or not self.pipeline.work_items:
            return Panel(
                Text("No work-item projection data. Select a company-mode task and press 4.", style="dim"),
                title="Projection [Focused]" if focused else "Projection",
                border_style="cyan" if focused else "white",
            )

        pipe = self.pipeline
        parts: list[RenderableType] = []

        # Header
        header = Text()
        header.append(truncate_text(pipe.parent_title, 50), style="bold white")
        if pipe.profile:
            header.append(f"  {pipe.profile}", style="dim italic")
        parts.append(header)

        # Work-item rows — linear chain rendering
        parts.append(self._render_work_item_chain(pipe.work_items))

        # Progress footer
        footer = self._render_footer(pipe)
        parts.append(footer)

        title = "Projection [Focused]" if focused else "Projection"
        return Panel(Group(*parts), title=title, border_style="cyan" if focused else "white")

    def _render_work_item_chain(self, work_items: list[PipelineWorkItemView]) -> Text:
        """Render work items as a vertical list with dependency arrows.

        Using vertical layout for reliable terminal rendering — horizontal
        box-drawing DAGs break on narrow terminals.
        """
        text = Text()
        # Build dependency lookup for rendering connectors
        projection_ids = {item.projection_id for item in work_items}

        for i, item in enumerate(work_items):
            sym, sym_style = _STATUS_SYMBOL.get(item.status, ("\u25cb", "dim"))
            is_selected = (
                self.state.selected_task_id is not None
                and item.task_id == self.state.selected_task_id
            )

            # Connector line from previous work item
            if i > 0:
                if item.dependencies:
                    # Show which projections this depends on
                    dep_labels = [d for d in item.dependencies if d in projection_ids]
                    if dep_labels:
                        text.append(f"\n  \u2502 after: {', '.join(dep_labels)}", style="dim")
                text.append("\n  \u2502\n  \u25bc\n", style="dim")

            # Work-item box — single-line compact rendering
            border_style = "bold cyan" if is_selected else "dim"
            text.append("  \u250c\u2500 ", style=border_style)
            text.append(f"{item.projection_id}", style="bold #38bdf8" if is_selected else "bold white")

            # Parallel group indicator
            if item.parallel_group:
                text.append(f"  \u2261{item.parallel_group}", style="dim")  # ≡

            # Gate indicator
            if item.has_gate:
                gate_label = item.gate_type or "gate"
                text.append(f"  \u229e {gate_label}", style="bold #f59e0b")  # ⊞

            text.append(" \u2500\u2510\n", style=border_style)

            # Status line
            text.append("  \u2502 ", style=border_style)
            text.append(f"{sym} {item.status}", style=sym_style)
            text.append(f"  {truncate_text(item.title, 30)}", style="white")
            text.append("\n", style="")

            # Detail line: assignee, elapsed, tool
            detail_parts: list[str] = []
            if item.assigned_to:
                detail_parts.append(item.assigned_to)
            if item.elapsed_sec > 0 and item.status != "pending":
                detail_parts.append(_fmt_elapsed(item.elapsed_sec))
            if item.current_tool:
                detail_parts.append(f"\u2699{item.current_tool}")  # ⚙
            if item.tool_elapsed_ms > 0:
                detail_parts.append(f"{item.tool_elapsed_ms}ms")
            if item.context_remaining_pct > 0:
                detail_parts.append(f"ctx {item.context_remaining_pct}%")
            if item.turn_cost_usd > 0:
                detail_parts.append(f"${item.turn_cost_usd:.4f}")

            if detail_parts:
                text.append("  \u2502 ", style=border_style)
                text.append("  ".join(detail_parts), style="dim")
                text.append("\n", style="")

            if item.last_tool_summary:
                text.append("  \u2502 ", style=border_style)
                text.append(truncate_text(item.last_tool_summary, 44), style="dim")
                text.append("\n", style="")

            text.append("  \u2514", style=border_style)
            text.append("\u2500" * 40, style=border_style)
            text.append("\u2518\n", style=border_style)

        return text

    @staticmethod
    def _render_footer(pipe: PipelineSnapshot) -> Text:
        text = Text("\n")
        # Progress bar
        ratio = min(1.0, pipe.done_count / pipe.total_count) if pipe.total_count > 0 else 0.0
        bar_width = 20
        filled = int(ratio * bar_width)
        text.append("Progress: ", style="dim")
        text.append("\u2588" * filled, style="bold #22c55e")
        text.append("\u2591" * (bar_width - filled), style="dim")
        text.append(f" {pipe.done_count}/{pipe.total_count} projected steps", style="white")
        text.append(f"    Elapsed: {_fmt_elapsed(pipe.elapsed_sec)}", style="dim")
        return text
