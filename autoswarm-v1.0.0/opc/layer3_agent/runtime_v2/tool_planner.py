"""Tool planning helpers for Native Runtime V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opc.layer4_tools.registry import ToolDefinition, ToolRegistry


_READ_ONLY_TOOL_NAMES = {
    "file_read",
    "file_search",
    "list_dir",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
    "todo_read",
    "agent_list",
    "agent_wait",
}

_NON_CONCURRENT_TOOL_NAMES = {
    "shell_exec",
    "file_write",
    "file_edit",
    "apply_patch",
    "python_exec",
    "git_commit",
    "agent_spawn",
    "agent_wait",
    "agent_send",
}


@dataclass
class ToolBatch:
    concurrency_safe: bool
    calls: list[dict[str, Any]]


class ToolPlanner:
    """Determine tool execution ordering and concurrency."""

    def __init__(self, registry: ToolRegistry, max_parallel_read_tools: int = 6) -> None:
        self.registry = registry
        self.max_parallel_read_tools = max(1, int(max_parallel_read_tools or 1))

    def is_read_only(self, tool: ToolDefinition | None) -> bool:
        if tool is None:
            return False
        if tool.read_only is not None:
            return bool(tool.read_only)
        if tool.name in _READ_ONLY_TOOL_NAMES:
            return True
        return tool.category in {"search", "read"} or tool.name.endswith("_read")

    def is_concurrency_safe(self, tool: ToolDefinition | None) -> bool:
        if tool is None:
            return False
        if tool.concurrency_safe is not None:
            return bool(tool.concurrency_safe)
        if tool.name in _NON_CONCURRENT_TOOL_NAMES:
            return False
        return self.is_read_only(tool)

    def partition(self, tool_calls: list[dict[str, Any]]) -> list[ToolBatch]:
        batches: list[ToolBatch] = []
        for call in tool_calls:
            tool = self.registry.get(str(call.get("function", "") or ""))
            concurrency_safe = self.is_concurrency_safe(tool)
            if concurrency_safe and batches and batches[-1].concurrency_safe:
                batches[-1].calls.append(call)
            else:
                batches.append(ToolBatch(concurrency_safe=concurrency_safe, calls=[call]))
        return batches
