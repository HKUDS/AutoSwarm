"""Shared helpers for company-mode session scoping and continuity guards.

Phase A (role-instance model): the session / queue key is keyed by
``(session_scope, role_id)`` — *not* by seat. A role that appears as a
member in multiple teams (e.g. CMO is both CEO's subordinate and the
leader of her own team) has **one** session and **one** queue. The seat
id stays as organizational metadata but no longer affects identity.

If a later refactor needs to support multiple parallel instances of the
same role (e.g. two CMOs in parallel branches of a run), pass
``team_instance_id`` to disambiguate — it is appended to the key when
present.
"""

from __future__ import annotations

from opc.core.models import Task
from opc.layer2_organization.work_item_runtime import is_work_item_runtime_metadata


def task_session_scope_id(task: Task) -> str:
    """Return the top-level session scope for a company-mode task."""
    metadata = dict(getattr(task, "metadata", {}) or {})
    return str(
        getattr(task, "parent_session_id", "")
        or metadata.get("parent_session_id", "")
        or getattr(task, "session_id", "")
        or metadata.get("session_id", "")
        or ""
    ).strip()


def scoped_member_session_id(
    *,
    project_id: str,
    session_scope_id: str,
    role_id: str,
    employee_id: str,
    team_instance_id: str = "",
    explicit_id: str = "",
) -> str:
    """Build a role-instance member session id.

    One per ``(project, session_scope, [team_instance], role, employee)``.
    Previously this was keyed by seat; that is gone in the role-instance
    model. Same role → same session, across upward/downward work.

    ``team_instance_id`` is optional — included when multiple concurrent
    instances of the same role exist in a single run. For standard
    single-branch company mode it's left blank.
    """
    explicit = str(explicit_id or "").strip()
    if explicit:
        return explicit
    project = str(project_id or "default").strip() or "default"
    scope = str(session_scope_id or "").strip()
    role = str(role_id or "unknown").strip() or "unknown"
    employee = str(employee_id or "default").strip() or "default"
    team_instance = str(team_instance_id or "").strip()
    scoped_prefix = f"{project}::{scope}" if scope else project
    parts: list[str] = [scoped_prefix]
    if team_instance:
        parts.append(team_instance)
    parts.append(role)
    parts.append(employee)
    return "role-session::" + "::".join(parts)


def scoped_queue_key(
    *,
    session_scope_id: str,
    role_id: str = "",
    team_instance_id: str = "",
    seat_id: str = "",  # deprecated, ignored — kept for arg compat while callers migrate
) -> str:
    """Build the per-role dispatch queue key.

    Role-scoped (not seat-scoped). ``team_instance_id`` is only appended
    when present (future-proof for multi-branch). ``seat_id`` is ignored.
    """
    role = str(role_id or "").strip()
    scope = str(session_scope_id or "").strip()
    team_instance = str(team_instance_id or "").strip()
    if not role:
        return ""
    parts: list[str] = []
    if scope:
        parts.append(scope)
    if team_instance:
        parts.append(team_instance)
    parts.append(role)
    return "::".join(parts)


def role_home_team_instance_id(
    role_id: str,
    seats: list[dict] | None,
) -> str:
    """Return the ``team_instance_id`` where ``role_id`` is the leader.

    Convention: the leader seat of a role has ``team_id == f"team::{role_id}"``.
    For leaf roles (no own team), returns the team_instance of any seat
    that lists this role.  Returns empty string if no seat matches.
    """
    role = str(role_id or "").strip()
    if not role:
        return ""
    seat_list = [dict(seat) for seat in (seats or []) if isinstance(seat, dict)]
    # Prefer the leader seat (role's own team).
    for seat in seat_list:
        if str(seat.get("role_id", "") or "").strip() != role:
            continue
        if str(seat.get("team_id", "") or "").strip() == f"team::{role}":
            ti = str(seat.get("team_instance_id", "") or "").strip()
            if ti:
                return ti
    # Fallback: first seat listing this role.
    for seat in seat_list:
        if str(seat.get("role_id", "") or "").strip() != role:
            continue
        ti = str(seat.get("team_instance_id", "") or "").strip()
        if ti:
            return ti
    return ""


def is_top_level_company_session(task: Task) -> bool:
    """Return whether the task belongs to a top-level actor-runtime session."""
    metadata = dict(getattr(task, "metadata", {}) or {})
    if not is_work_item_runtime_metadata(metadata):
        return False
    session_id = str(
        getattr(task, "session_id", "")
        or metadata.get("session_id", "")
        or ""
    ).strip()
    parent_session_id = str(
        getattr(task, "parent_session_id", "")
        or metadata.get("parent_session_id", "")
        or ""
    ).strip()
    return bool(session_id and parent_session_id and session_id == parent_session_id)


def external_resume_allowed_for_scope(task: Task, *, resume_scope_id: str = "") -> bool:
    """Only allow external session continuation when the scope matches."""
    metadata = dict(getattr(task, "metadata", {}) or {})
    if bool(metadata.get("allow_external_resume_on_top_level_session", False)):
        return True
    current_scope = task_session_scope_id(task)
    if not current_scope:
        return True
    resume_scope = str(resume_scope_id or "").strip()
    if resume_scope:
        return resume_scope == current_scope
    return not is_top_level_company_session(task)
