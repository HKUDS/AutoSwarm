"""Command palette for the CLI board."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


@dataclass(frozen=True)
class PaletteCommand:
    command_id: str
    label: str
    description: str = ""
    keys: str = ""


class CommandPaletteScreen(ModalScreen[str | None]):
    """Small command palette with filtering and keyboard navigation."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
    }

    .palette-dialog {
        width: 96;
        max-width: 95%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    #palette-filter {
        margin-top: 1;
    }

    #palette-list {
        margin-top: 1;
        max-height: 18;
    }

    .palette-help {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("enter", "submit", "Run"),
    ]

    def __init__(self, *, title: str, commands: list[PaletteCommand]) -> None:
        super().__init__()
        self.title_text = title
        self.commands = list(commands)
        self.cursor = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="palette-dialog"):
            yield Static(self.title_text, id="palette-title")
            yield Input(placeholder="Type to filter commands", id="palette-filter")
            yield Static(id="palette-list")
            yield Static("Enter to run, Esc to close, Up/Down to navigate.", classes="palette-help")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#palette-filter", Input))
        self._refresh_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "palette-filter":
            return
        self.cursor = 0
        self._refresh_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-filter":
            self.action_submit()

    def action_cursor_down(self) -> None:
        commands = self._filtered_commands()
        if not commands:
            return
        self.cursor = min(len(commands) - 1, self.cursor + 1)
        self._refresh_list()

    def action_cursor_up(self) -> None:
        commands = self._filtered_commands()
        if not commands:
            return
        self.cursor = max(0, self.cursor - 1)
        self._refresh_list()

    def action_submit(self) -> None:
        commands = self._filtered_commands()
        if not commands:
            self.dismiss(None)
            return
        self.dismiss(commands[self.cursor].command_id)

    def action_close(self) -> None:
        self.dismiss(None)

    def _refresh_list(self) -> None:
        self.query_one("#palette-list", Static).update(self._render_list())

    def _render_list(self) -> RenderableType:
        commands = self._filtered_commands()
        if not commands:
            return Text("No commands match the current query.", style="dim")
        rows: list[Text] = []
        for index, command in enumerate(commands):
            selected = index == self.cursor
            row = Text(style="black on #22d3ee" if selected else "white")
            row.append(command.label, style="bold" if not selected else "bold black on #22d3ee")
            if command.keys:
                row.append(f"  {command.keys}", style="dim" if not selected else "black on #22d3ee")
            if command.description:
                row.append(f"\n{command.description}", style="dim" if not selected else "black on #22d3ee")
            rows.append(row)
        return Group(*rows)

    def _filtered_commands(self) -> list[PaletteCommand]:
        query = self.query_one("#palette-filter", Input).value.strip().casefold()
        if not query:
            return self.commands
        filtered = [
            command
            for command in self.commands
            if query in " ".join([command.label, command.description, command.keys]).casefold()
        ]
        if self.cursor >= len(filtered):
            self.cursor = max(0, len(filtered) - 1)
        return filtered
