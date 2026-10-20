"""Read-only organisation viewer for the CLI board."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from ..state.models import OrgEmployeeView, OrgRoleView, OrgSnapshotView
from ..state.store import BoardStateStore
from .render_utils import truncate_text


class OrgViewerWidget(Static):
    """Render a read-only org structure: role tree, employees, and work-item projection."""

    def __init__(self, state: BoardStateStore) -> None:
        super().__init__(id="org-viewer")
        self.state = state
        self.org: OrgSnapshotView | None = None

    def set_org(self, org: OrgSnapshotView | None) -> None:
        self.org = org
        self.refresh()

    def render(self) -> RenderableType:
        focused = self.state.pane_focus == "main" and self.state.view_mode == "org"
        if self.org is None:
            return Panel(
                Text("No org data. Press 5 to load organisation view.", style="dim"),
                title="Organisation [Focused]" if focused else "Organisation",
                border_style="cyan" if focused else "white",
            )

        parts: list[RenderableType] = []
        parts.append(self._render_roles())
        parts.append(self._render_employees())

        title = "Organisation [Focused]" if focused else "Organisation"
        return Panel(Group(*parts), title=title, border_style="cyan" if focused else "white")

    # ── Roles ──

    def _render_roles(self) -> Text:
        org = self.org
        text = Text()
        text.append(f"Roles ({org.role_count})\n", style="bold #cbd5e1")

        if not org.role_tree:
            text.append("  No roles configured.\n", style="dim")
            return text

        for node in org.role_tree:
            self._render_role_node(text, node, depth=0)

        return text

    def _render_role_node(self, text: Text, role: OrgRoleView, depth: int) -> None:
        indent = "  " * (depth + 1)
        connector = ""
        if depth > 0:
            connector = "\u251c\u2500 " if True else "\u2514\u2500 "  # ├─

        text.append(f"{indent}{connector}", style="dim")
        text.append(role.role_id, style="bold #38bdf8")
        resp = truncate_text(role.responsibility, 30)
        if resp:
            text.append(f"  \"{resp}\"", style="dim")
        text.append(f"  {role.employee_count} emp", style="dim italic")
        text.append("\n")

        for child in role.children:
            self._render_role_node(text, child, depth=depth + 1)

    # ── Employees ──

    def _render_employees(self) -> Text:
        org = self.org
        text = Text("\n")
        text.append(f"Employees ({org.employee_count})\n", style="bold #cbd5e1")

        if not org.employees:
            text.append("  No employees registered.\n", style="dim")
            return text

        for emp in org.employees[:20]:
            text.append(f"  {truncate_text(emp.name, 14):<14s}", style="bold white")
            text.append(f"  {truncate_text(emp.role_id, 14):<14s}", style="#38bdf8")
            text.append(f"  {emp.seniority:<8s}", style="dim")
            if emp.domains:
                text.append(f"  [{', '.join(emp.domains[:4])}]", style="#a78bfa")
            text.append("\n")

        if len(org.employees) > 20:
            text.append(f"  \u2026 +{len(org.employees) - 20} more\n", style="dim")

        return text
