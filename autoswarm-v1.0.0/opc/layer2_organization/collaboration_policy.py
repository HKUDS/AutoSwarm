"""Helpers for soft-topology collaboration and ownership-contract enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opc.core.models import Task


_COLLABORATION_TOOLS = {
    "send_dm",
    "broadcast_issue",
    "start_meeting",
    "ask_peer_and_wait",
}

_WRITE_TOOLS = {
    "file_write",
    "file_edit",
    "apply_patch",
    "shell_exec",
}


def _normalized_role_list(values: list[Any] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values or []:
        role_id = str(item or "").strip()
        if not role_id or role_id in seen:
            continue
        seen.add(role_id)
        result.append(role_id)
    return result


def collect_dynamic_contact_roles(task: Task | None) -> list[str]:
    if task is None:
        return []
    metadata = dict(getattr(task, "metadata", {}) or {})
    ownership_contract = dict(metadata.get("ownership_contract", {}) or {})
    work_item_gate = dict(metadata.get("work_item_gate", {}) or {})
    dynamic = []
    dynamic.extend(_normalized_role_list(ownership_contract.get("allowed_collaboration_targets")))
    dynamic.extend(_normalized_role_list(ownership_contract.get("downstream_consumer")))
    dynamic.extend(_normalized_role_list(metadata.get("dynamic_allowed_contact_roles")))
    reviewer_role = str(work_item_gate.get("reviewer_role", "") or metadata.get("manager_role_id", "") or "").strip()
    if reviewer_role:
        dynamic.append(reviewer_role)
    for handoff in list(metadata.get("handoff_log", []) or []):
        if isinstance(handoff, dict):
            dynamic.append(str(handoff.get("to", "")).strip())
            dynamic.append(str(handoff.get("from", "")).strip())
    return _normalized_role_list(dynamic)


def effective_contact_roles(
    role_id: str,
    *,
    task: Task | None = None,
    org_engine: Any | None = None,
) -> list[str]:
    static_roles: list[str] = []
    if org_engine is not None and hasattr(org_engine, "get_allowed_contact_roles"):
        getter = getattr(org_engine, "get_allowed_contact_roles")
        try:
            static_roles = list(getter(role_id, task=task) or [])
        except TypeError:
            static_roles = list(getter(role_id) or [])
    return _normalized_role_list([*static_roles, *collect_dynamic_contact_roles(task)])


def _candidate_paths(tool_name: str, arguments: dict[str, Any]) -> list[Path]:
    if tool_name in {"file_write", "file_edit", "file_read", "grep", "glob", "file_search", "list_dir"}:
        path = str(arguments.get("path", "") or arguments.get("directory", "") or "").strip()
        return [Path(path)] if path else []
    if tool_name == "apply_patch":
        patch = str(arguments.get("patch", "") or "").splitlines()
        paths: list[Path] = []
        prefixes = ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
        for line in patch:
            for prefix in prefixes:
                if line.startswith(prefix):
                    raw = line[len(prefix):].strip()
                    if raw:
                        paths.append(Path(raw))
        return paths
    if tool_name == "shell_exec":
        cwd = str(arguments.get("working_directory", "") or "").strip()
        return [Path(cwd)] if cwd else []
    return []


def _resolved_write_roots(task: Task | None) -> list[Path]:
    if task is None:
        return []
    metadata = dict(getattr(task, "metadata", {}) or {})
    ownership_contract = dict(metadata.get("ownership_contract", {}) or {})
    scope = str(ownership_contract.get("write_scope", "") or "").strip()
    roots: list[Path] = []
    if scope and scope not in {"assigned_workspace", "read_only"}:
        roots.append(Path(scope))
    output_dir = str(metadata.get("target_output_dir", "") or "").strip()
    if output_dir:
        roots.append(Path(output_dir))
    return [path.resolve() for path in roots if str(path).strip()]


def _path_within(candidate: Path, root: Path) -> bool:
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except AttributeError:
        resolved = str(candidate.resolve())
        root_value = str(root.resolve())
        return resolved == root_value or resolved.startswith(root_value.rstrip("/") + "/")
    except FileNotFoundError:
        resolved = candidate.expanduser().resolve(strict=False)
        root_resolved = root.expanduser().resolve(strict=False)
        try:
            return resolved.is_relative_to(root_resolved)
        except AttributeError:
            resolved_value = str(resolved)
            root_value = str(root_resolved)
            return resolved_value == root_value or resolved_value.startswith(root_value.rstrip("/") + "/")


def ownership_guard_violation(
    *,
    task: Task | None,
    tool_name: str,
    arguments: dict[str, Any],
    org_engine: Any | None = None,
) -> str | None:
    if task is None:
        return None
    metadata = dict(getattr(task, "metadata", {}) or {})
    if str(metadata.get("execution_mode", "") or "").strip() != "company_mode":
        return None
    ownership_contract = dict(metadata.get("ownership_contract", {}) or {})
    if not ownership_contract:
        return None
    if tool_name in _WRITE_TOOLS:
        write_scope = str(ownership_contract.get("write_scope", "") or "").strip()
        if write_scope == "read_only":
            return (
                "Ownership contract blocks write-side effects for this work item. "
                "This work item is read_only and must not modify files or run mutating shell commands."
            )
        roots = _resolved_write_roots(task)
        candidate_paths = _candidate_paths(tool_name, arguments)
        if roots and candidate_paths:
            out_of_scope = [str(path) for path in candidate_paths if not any(_path_within(path, root) for root in roots)]
            if out_of_scope:
                return (
                    "Ownership contract blocks writes outside the assigned workspace. "
                    f"Out-of-scope target(s): {', '.join(out_of_scope[:4])}."
                )
    if tool_name in _COLLABORATION_TOOLS:
        # TODO(role-identity): route through a central work-item role accessor.
        from_role = str(getattr(task, "assigned_to", "") or metadata.get("work_item_role_id", "") or "").strip()
        allowed = set(effective_contact_roles(from_role, task=task, org_engine=org_engine))
        recipients: list[str] = []
        if tool_name == "broadcast_issue":
            recipients = _normalized_role_list(arguments.get("to_agents"))
        elif tool_name == "start_meeting":
            recipients = _normalized_role_list(arguments.get("participants"))
        else:
            recipients = _normalized_role_list([arguments.get("to_agent")])
        invalid = [recipient for recipient in recipients if recipient and recipient not in allowed]
        if invalid:
            return (
                "Ownership contract / collaboration topology blocks this contact target. "
                f"Recipient(s) not allowed for the current work item: {', '.join(invalid)}."
            )
    return None


def render_ownership_contract(task: Task | None) -> str:
    """Render the boundary-enforcing part of the ownership contract.

    Only three fields are rendered here: write scope, allowed
    collaboration targets, and downstream consumers. The
    ``summary`` and ``expected_artifacts`` fields are intentionally
    NOT rendered — ``summary`` duplicates the work-item identity's
    ``your_responsibility``, and ``expected_artifacts`` duplicates
    the work-item identity's ``deliverables``. Both already appear in
    the work-item identity block that every role receives, so repeating
    them here would be redundant.
    Returns "" if the task carries no contract or if none of the
    boundary-enforcing fields are populated.
    """
    if task is None:
        return ""
    ownership_contract = dict(getattr(task, "metadata", {}).get("ownership_contract", {}) or {})
    if not ownership_contract:
        return ""
    lines: list[str] = []
    write_scope = str(ownership_contract.get("write_scope", "") or "").strip()
    if write_scope:
        lines.append(f"Write scope: {write_scope}")
    allowed_collaboration_targets = _normalized_role_list(ownership_contract.get("allowed_collaboration_targets"))
    if allowed_collaboration_targets:
        lines.append("Allowed collaboration targets:")
        lines.extend(f"- {item}" for item in allowed_collaboration_targets[:12])
    downstream = _normalized_role_list(ownership_contract.get("downstream_consumer"))
    if downstream:
        lines.append("Downstream consumers:")
        lines.extend(f"- {item}" for item in downstream[:8])
    if not lines:
        return ""
    return "## Ownership Contract\n" + "\n".join(lines)
