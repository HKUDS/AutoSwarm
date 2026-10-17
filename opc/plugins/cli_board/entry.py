"""Command-line entrypoint for the AutoSwarm CLI board."""

from __future__ import annotations

import importlib
from typing import Any

from rich.console import Console

_console = Console()


def _require_textual() -> None:
    try:
        importlib.import_module("textual")
    except ImportError as exc:  # pragma: no cover - depends on local environment
        _console.print(
            "[red]CLI board requires the optional Textual dependency.[/red]\n"
            "Install it with one of these commands:\n"
            "  [bold]pip install 'autoswarm[cli-board]'[/bold]\n"
            "  [bold]pip install textual>=8.1.1[/bold]"
        )
        raise SystemExit(1) from exc


def launch_board(
    *,
    project_id: str | None = None,
    refresh_interval: float = 2.0,
    attach: bool = False,
    readonly: bool = False,
    initial_view: str = "kanban",
    initial_session_id: str | None = None,
    initial_work_item_id: str | None = None,
    initial_role_id: str | None = None,
    initial_target: str | None = None,
) -> Any:
    """Launch the interactive Textual board."""
    _require_textual()
    from opc.plugins.cli_board.tui.app import CliBoardApp

    app = CliBoardApp(
        project_id=project_id,
        refresh_interval=refresh_interval,
        attach=attach,
        readonly=readonly,
        initial_view=initial_view,
        initial_session_id=initial_session_id,
        initial_work_item_id=initial_work_item_id,
        initial_role_id=initial_role_id,
        initial_target=initial_target,
    )
    return app.run()
