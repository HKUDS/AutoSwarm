"""Native runtime tool strategy rendering."""

from __future__ import annotations


_FILE_READ_TOOLS = {"file_read", "list_dir", "glob", "grep", "file_search"}
_FILE_EDIT_TOOLS = {"file_write", "file_edit", "apply_patch"}
_SHELL_TOOLS = {"shell_exec"}
_PYTHON_TOOLS = {"python_exec"}
_WEB_TOOLS = {"web_search", "web_fetch"}
_BROWSER_TOOLS = {
    "browser_navigate",
    "browser_navigate_back",
    "browser_click",
    "browser_snapshot",
    "browser_type",
    "browser_wait_for",
    "browser_scroll",
    "browser_select_option",
    "browser_take_screenshot",
    "browser_close",
}
_TODO_TOOLS = {"todo_write", "todo_read"}
_SUBAGENT_TOOLS = {"agent_spawn", "agent_wait", "agent_send", "agent_list"}
_COMPANY_COLLABORATION_TOOLS = {
    "inbox",
    "send_dm",
    "ask_peer_and_wait",
    "reply_message",
    "broadcast_issue",
    "delegate_work",
    "modify_work_item",
    "delete_work_item",
    "manager_board_read",
    "manager_board_update",
    "manager_board_release",
    "manager_board_rollup",
    "start_meeting",
    "respond_meeting",
    "propose_runtime_replan",
    "propose_task_adjustment",
    "route_work",
    "find_and_ask_expert",
    "read_inbox",
}


class NativeToolStrategyBuilder:
    """Render concise tool-selection guidance for the current native tool surface."""

    def __init__(self, allowed_tools: list[str] | None, *, company_mode: bool = False) -> None:
        self.allowed_tools = {
            str(item or "").strip()
            for item in list(allowed_tools or [])
            if str(item or "").strip()
        }
        self.company_mode = bool(company_mode)

    def render(self) -> str:
        if not self.allowed_tools:
            return "No explicit tool surface was supplied for this runtime."

        lines = [
            "Use the current tool schema as the source of truth for exact arguments.",
            f"Available tool count: {len(self.allowed_tools)}",
            f"Available tools: {self._preview_tools()}",
            "",
            "Selection strategy:",
        ]
        rules = self._rules()
        if rules:
            lines.extend(f"- {rule}" for rule in rules)
        else:
            lines.append("- Use the provided tools directly when they advance the task.")
        return "\n".join(lines).strip()

    def _preview_tools(self) -> str:
        ordered = sorted(self.allowed_tools)
        preview = ", ".join(ordered[:24])
        suffix = "" if len(ordered) <= 24 else f", +{len(ordered) - 24} more"
        return f"{preview}{suffix}"

    def _rules(self) -> list[str]:
        rules: list[str] = []
        tools = self.allowed_tools
        has_file_read = bool(tools & _FILE_READ_TOOLS)
        has_file_edit = bool(tools & _FILE_EDIT_TOOLS)
        if has_file_read:
            rules.append("Use dedicated read/search/list tools for workspace inspection instead of shell text commands.")
        if has_file_edit:
            rules.append("Use dedicated edit/write/patch tools for file changes; verify the resulting diff or file content when useful.")
        if tools & _SHELL_TOOLS:
            rules.append("Use shell execution for commands, builds, tests, package scripts, and process control.")
        if tools & _PYTHON_TOOLS:
            rules.append("Use Python execution for calculations, data processing, and focused local experiments.")
        if tools & (_WEB_TOOLS | _BROWSER_TOOLS):
            rules.append("Use web/browser tools only when current external information or direct page interaction is needed.")
        if tools & _TODO_TOOLS:
            rules.append("Use the task ledger for multi-step work; keep one item in progress and update it as work changes.")
        if tools & _SUBAGENT_TOOLS:
            rules.append("Use subagents only for bounded parallel work, isolation, verification, or clearly separate scopes.")
        if self.company_mode and tools & _COMPANY_COLLABORATION_TOOLS:
            rules.append("Use company collaboration tools only for the active work-item coordination surface and only when available this turn.")
        if self._has_parallel_read_surface():
            rules.append("Run independent read/search/context-gathering tool calls in parallel when there is no dependency between them.")
        return rules

    def _has_parallel_read_surface(self) -> bool:
        read_like_count = len(self.allowed_tools & (_FILE_READ_TOOLS | _WEB_TOOLS | _BROWSER_TOOLS))
        return read_like_count >= 2
