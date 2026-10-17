"""Terminal output helpers for the Office UI CLI entrypoint."""

from __future__ import annotations

from typing import Literal


try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except Exception:  # pragma: no cover - rich is an app dependency; keep CLI resilient.
    box = None
    Console = None
    Panel = None
    Table = None


StatusKind = Literal["info", "success", "warning", "error"]

_STYLE_BY_KIND: dict[StatusKind, str] = {
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}

_console = Console() if Console else None


def status(message: str, *, kind: StatusKind = "info", detail: str | None = None) -> None:
    """Print a compact status line for startup/build steps."""
    if _console is None:
        suffix = f" {detail}" if detail else ""
        print(f"[{kind}] {message}{suffix}")
        return

    style = _STYLE_BY_KIND[kind]
    if detail:
        _console.print(f"[{style}]{message}[/] [dim]{detail}[/]")
    else:
        _console.print(f"[{style}]{message}[/]")


def error(message: str, *, detail: str | None = None) -> None:
    """Print an actionable startup error."""
    if _console is None or Panel is None:
        print(f"Error: {message}")
        if detail:
            print(detail)
        return

    body = message if not detail else f"{message}\n\n[dim]{detail}[/]"
    _console.print(Panel(body, title="Office UI Error", border_style="red", box=box.ROUNDED if box else None))


def server_banner(*, host: str, port: int, project_id: str | None = None) -> None:
    """Print the Office UI server banner once the HTTP listener is ready."""
    local_url = f"http://localhost:{port}"
    bind_url = f"http://{host}:{port}"
    project = project_id or "default"

    if _console is None or Panel is None or Table is None:
        print(f"\nAutoSwarm Office UI: {local_url}")
        print(f"Bind: {bind_url} | Project: {project} | Press Ctrl+C to stop\n")
        return

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("URL", f"[bold cyan]{local_url}[/]")
    table.add_row("Bind", bind_url)
    table.add_row("Project", project)
    table.add_row("Stop", "Ctrl+C")
    _console.print()
    _console.print(Panel(table, title="AutoSwarm Office UI", border_style="cyan", box=box.ROUNDED if box else None))
    _console.print()
