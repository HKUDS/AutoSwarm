"""Context dock tab header."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..state.store import BoardStateStore


class ContextTabsWidget(Static):
    """Render the context dock tab strip."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="context-tabs")
        self.state = state

    def render(self) -> Text:
        focused = self.state.pane_focus == "context"
        session_label = "Runtime Session" if self.state.snapshot.mode == "company" else "Session"
        tabs = [
            ("detail", "Detail"),
            ("session", session_label),
            ("activity", "Activity"),
        ]
        text = Text()
        for tab_id, label in tabs:
            selected = self.state.context_tab == tab_id
            style = "bold black on #22d3ee" if selected else "bold #94a3b8"
            if focused and selected:
                style = "bold black on #38bdf8"
            text.append(f" {label} ", style=style)
            text.append(" ")
        return text
