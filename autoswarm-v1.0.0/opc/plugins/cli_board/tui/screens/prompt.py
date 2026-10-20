"""Reusable modal prompt screen."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea


@dataclass(frozen=True)
class PromptField:
    key: str
    label: str
    value: str = ""
    placeholder: str = ""
    password: bool = False
    multiline: bool = False


class PromptScreen(ModalScreen[dict[str, str] | None]):
    """Simple form dialog used by the CLI board."""

    DEFAULT_CSS = """
    PromptScreen {
        align: center middle;
    }

    .prompt-dialog {
        width: 88;
        max-width: 90%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    .prompt-actions {
        align-horizontal: right;
        height: auto;
        margin-top: 1;
    }

    .prompt-field {
        margin-top: 1;
    }

    .prompt-title {
        text-style: bold;
    }

    .prompt-help {
        color: $text-muted;
        margin-top: 1;
    }

    .prompt-textarea {
        height: 6;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        title: str,
        fields: list[PromptField],
        help_text: str = "",
        confirm_label: str = "Confirm",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.fields = fields
        self.help_text = help_text
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="prompt-dialog"):
            yield Static(self.title_text, id="prompt-title", classes="prompt-title")
            for field in self.fields:
                yield Label(field.label, classes="prompt-field")
                if field.multiline:
                    yield TextArea(
                        field.value,
                        id=f"field-{field.key}",
                        classes="prompt-textarea",
                        tab_behavior="indent",
                    )
                else:
                    yield Input(
                        value=field.value,
                        placeholder=field.placeholder,
                        password=field.password,
                        id=f"field-{field.key}",
                    )
            if self.help_text:
                yield Static(self.help_text, classes="prompt-help")
            with Horizontal(classes="prompt-actions"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="primary")

    def on_mount(self) -> None:
        if self.fields:
            first_id = f"field-{self.fields[0].key}"
            try:
                widget = self.query_one(f"#{first_id}")
                self.set_focus(widget)
            except Exception:
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "confirm":
            self.dismiss(self._collect_values())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_field_ids = [f"field-{f.key}" for f in self.fields if not f.multiline]
        if event.input.id not in input_field_ids:
            return
        # Find position among ALL fields (not just Input fields)
        all_field_ids = [f"field-{f.key}" for f in self.fields]
        idx = all_field_ids.index(event.input.id)
        if idx == len(all_field_ids) - 1:
            self.dismiss(self._collect_values())
            return
        next_id = all_field_ids[idx + 1]
        try:
            next_widget = self.query_one(f"#{next_id}")
            self.set_focus(next_widget)
        except Exception:
            self.dismiss(self._collect_values())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _collect_values(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for field in self.fields:
            widget_id = f"field-{field.key}"
            if field.multiline:
                widget = self.query_one(f"#{widget_id}", TextArea)
                result[field.key] = widget.text
            else:
                widget = self.query_one(f"#{widget_id}", Input)
                result[field.key] = widget.value
        return result
