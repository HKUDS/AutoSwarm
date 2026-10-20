"""Help modal for the CLI board."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen[None]):
    """Display keyboard shortcuts."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    .help-dialog {
        width: 96;
        max-width: 95%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    .help-copy {
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "close", "Close"), ("enter", "close", "Close")]

    def compose(self) -> ComposeResult:
        help_text = (
            "Navigation\n"
            "  Arrow keys / h j k l: move selection\n"
            "  Tab / Shift+Tab: cycle pane focus\n"
            "  Enter: open focus view or advance context tab\n"
            "  Space: toggle density\n"
            "\n"
            "Views\n"
            "  1: kanban   2: list   3: focus   4: projection   5: org\n"
            "  E: switch execution mode (task / company / custom)\n"
            "\n"
            "Task Actions\n"
            "  n: create task          g: run selected task\n"
            "  s: reply in session     m: move between columns\n"
            "  a / d: approve / deny checkpoint\n"
            "  e: checkpoint feedback (approve/deny with message)\n"
            "  c: done  x: cancel  t: retry\n"
            "\n"
            "Session Management\n"
            "  R: rename session       D: delete session\n"
            "\n"
            "Search and Tools\n"
            "  /: search filter        f: toggle done visibility\n"
            "  r: refresh board        Ctrl+K or :: command palette\n"
            "  ?: this help            q: quit"
        )
        with Vertical(classes="help-dialog"):
            yield Static("AutoSwarm CLI Board Help", id="help-title")
            yield Static(help_text, classes="help-copy")

    def action_close(self) -> None:
        self.dismiss(None)
