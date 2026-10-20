"""Diagnostics for company WorkItem runtime Task projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from opc.core.models import DelegationWorkItem, Phase, Task
from opc.layer2_organization.phase import (
    DONE_PHASES,
    is_report_execution_work_item_metadata,
    is_review_execution_work_item_metadata,
    should_hide_work_item_from_company_kanban,
)
from opc.layer2_organization.metadata_ownership import validate_metadata_ownership
from opc.layer2_organization.work_item_identity import (
    WORK_ITEM_TURN_TYPE_KEY,
    canonical_work_item_turn_type_for_kind,
    normalize_work_item_turn_type,
    projection_id_for_work_item,
    work_item_projection_id_from_metadata,
)
from opc.layer2_organization.work_item_links import linked_work_item_id_for_task
from opc.layer2_organization.work_item_runtime import is_work_item_runtime_metadata


WORK_ITEM_RUNTIME_INVARIANT_EVENT_TYPE = "work_item_runtime_invariant_violation"

_RUNTIME_MODELS = {"multi_team_org"}
_AUXILIARY_TURN_KINDS = {"review", "report", "followup", "follow_up", "delivery", "deliver"}
_HIDDEN_RUNNABLE_TURN_KINDS = _AUXILIARY_TURN_KINDS | {"aggregate", "synthesize"}


@dataclass(frozen=True)
class WorkItemRuntimeInvariantIssue:
    code: str
    severity: str
    run_id: str = ""
    work_item_id: str = ""
    runtime_task_id: str = ""
    projection_id: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> tuple[str, str, str, str]:
        return (
            str(self.code or "").strip(),
            str(self.work_item_id or "").strip(),
            str(self.runtime_task_id or "").strip(),
            str(self.projection_id or "").strip(),
        )

    def to_event_payload(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "run_id": self.run_id,
            "work_item_id": self.work_item_id,
            "runtime_task_id": self.runtime_task_id,
            "projection_id": self.projection_id,
            "message": self.message,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _phase_value(value: Any) -> str:
    if isinstance(value, Phase):
        return value.value
    return _lower(value)


def _is_done_phase(value: Any) -> bool:
    if isinstance(value, Phase):
        return value in DONE_PHASES
    normalized = _lower(value)
    return normalized in {phase.value for phase in DONE_PHASES}


def _runtime_model(metadata: Mapping[str, Any]) -> str:
    return _lower(metadata.get("runtime_model") or metadata.get("execution_model"))


def _turn_kind(metadata: Mapping[str, Any], *, fallback: str = "") -> str:
    turn = normalize_work_item_turn_type(metadata.get(WORK_ITEM_TURN_TYPE_KEY), fallback="")
    if turn:
        return turn
    for key in ("work_kind", "delegation_turn_kind"):
        owner_kind = canonical_work_item_turn_type_for_kind(metadata.get(key), fallback="")
        if owner_kind:
            return owner_kind
    return canonical_work_item_turn_type_for_kind(fallback, fallback="")


def _task_projection_id(task: Task | None) -> str:
    if task is None:
        return ""
    return work_item_projection_id_from_metadata(dict(task.metadata or {}), fallback=_clean(getattr(task, "id", "")))


def _issue(
    code: str,
    severity: str,
    *,
    task: Task | None = None,
    work_item: DelegationWorkItem | None = None,
    work_item_id: str = "",
    projection_id: str = "",
    message: str,
    details: Mapping[str, Any] | None = None,
) -> WorkItemRuntimeInvariantIssue:
    task_metadata = dict(getattr(task, "metadata", {}) or {})
    item_metadata = dict(getattr(work_item, "metadata", {}) or {})
    wid = (
        _clean(work_item_id)
        or _clean(getattr(work_item, "work_item_id", ""))
        or linked_work_item_id_for_task(task)
    )
    projection = _clean(projection_id) or _task_projection_id(task)
    if not projection and work_item is not None:
        projection = projection_id_for_work_item(work_item)
    return WorkItemRuntimeInvariantIssue(
        code=code,
        severity=severity,
        run_id=(
            _clean(getattr(work_item, "run_id", ""))
            or _clean(item_metadata.get("delegation_run_id"))
            or _clean(task_metadata.get("delegation_run_id"))
        ),
        work_item_id=wid,
        runtime_task_id=_clean(getattr(task, "id", "")),
        projection_id=projection,
        message=message,
        details=dict(details or {}),
    )


def is_company_runtime_projection_task(task: Task | None) -> bool:
    """Return true for company WorkItem runtime projection Tasks.

    The predicate intentionally does not read legacy owner metadata. A task is
    a projection only when it carries the canonical runtime marker and the
    canonical projection identity.
    """
    if task is None:
        return False
    metadata = dict(getattr(task, "metadata", {}) or {})
    if not is_work_item_runtime_metadata(metadata):
        return False
    if bool(metadata.get("synthetic_inbox_turn") or metadata.get("synthetic_company_inbox")):
        return False
    runtime_model = _runtime_model(metadata)
    if runtime_model and runtime_model not in _RUNTIME_MODELS:
        return False
    return bool(work_item_projection_id_from_metadata(metadata, fallback=""))


def _expected_work_kind(work_item: DelegationWorkItem | None) -> str:
    if work_item is None:
        return ""
    metadata = dict(work_item.metadata or {})
    return _turn_kind(metadata, fallback=_clean(getattr(work_item, "kind", "")) or "execute") or "execute"


def _expected_role_id(work_item: DelegationWorkItem | None) -> str:
    return _clean(getattr(work_item, "role_id", "")) if work_item is not None else ""


def _expected_seat_id(work_item: DelegationWorkItem | None) -> str:
    if work_item is None:
        return ""
    return _clean(getattr(work_item, "seat_id", "")) or _clean(dict(work_item.metadata or {}).get("seat_id"))


def _expected_turn_mode(work_item: DelegationWorkItem | None) -> str:
    if work_item is None:
        return ""
    return _clean(dict(work_item.metadata or {}).get("current_turn_mode"))


def validate_work_item_runtime_projection(
    task: Task,
    work_item: DelegationWorkItem | None,
    *,
    work_item_by_id: Mapping[str, DelegationWorkItem] | None = None,
) -> list[WorkItemRuntimeInvariantIssue]:
    """Validate that a projected runtime Task is backed by a WorkItem link.

    This function is read-only. It reports mismatches; it never repairs links
    and never uses legacy Task metadata as an owner source.
    """
    if not is_company_runtime_projection_task(task):
        return []
    task_metadata = dict(getattr(task, "metadata", {}) or {})
    item_metadata = dict(getattr(work_item, "metadata", {}) or {}) if work_item is not None else {}
    linked_work_item_id = linked_work_item_id_for_task(task)
    projection_id = _task_projection_id(task)
    issues: list[WorkItemRuntimeInvariantIssue] = []

    if not linked_work_item_id:
        issues.append(
            _issue(
                "missing_link",
                "error",
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message="Company runtime projection task is not hydrated from work_item_runtime_links.",
            )
        )
    if work_item is None:
        issues.append(
            _issue(
                "missing_work_item",
                "error",
                task=task,
                work_item_id=linked_work_item_id,
                projection_id=projection_id,
                message="Company runtime projection task link does not resolve to a WorkItem.",
            )
        )
        return issues

    expected_work_item_id = _clean(getattr(work_item, "work_item_id", ""))
    if linked_work_item_id and expected_work_item_id and linked_work_item_id != expected_work_item_id:
        issues.append(
            _issue(
                "link_work_item_mismatch",
                "error",
                task=task,
                work_item=work_item,
                work_item_id=linked_work_item_id,
                projection_id=projection_id,
                message="Runtime task link points at a different WorkItem than the supplied WorkItem.",
                details={"expected_work_item_id": expected_work_item_id, "linked_work_item_id": linked_work_item_id},
            )
        )

    expected_projection_id = projection_id_for_work_item(work_item)
    if projection_id and expected_projection_id and projection_id != expected_projection_id:
        issues.append(
            _issue(
                "projection_mismatch",
                "warning",
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message="Runtime task projection id differs from the WorkItem projection id.",
                details={"expected_projection_id": expected_projection_id, "task_projection_id": projection_id},
            )
        )

    expected_kind = _expected_work_kind(work_item)
    task_kind = _turn_kind(task_metadata, fallback=_lower(task_metadata.get("work_kind")) or expected_kind)
    if expected_kind and task_kind and task_kind != expected_kind:
        issues.append(
            _issue(
                "work_kind_mismatch",
                "error",
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message="Runtime task turn kind differs from the WorkItem turn kind.",
                details={"expected_work_kind": expected_kind, "task_work_kind": task_kind},
            )
        )

    expected_role_id = _expected_role_id(work_item)
    task_role_id = _clean(getattr(task, "assigned_to", "")) or _clean(task_metadata.get("work_item_role_id"))
    if expected_role_id and task_role_id and task_role_id != expected_role_id:
        issues.append(
            _issue(
                "owner_role_mismatch",
                "error",
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message="Runtime task owner role differs from the WorkItem role.",
                details={"expected_role_id": expected_role_id, "task_role_id": task_role_id},
            )
        )

    expected_seat_id = _expected_seat_id(work_item)
    task_seat_id = _clean(task_metadata.get("delegation_seat_id") or task_metadata.get("seat_id"))
    if expected_seat_id and task_seat_id and task_seat_id != expected_seat_id:
        issues.append(
            _issue(
                "owner_seat_mismatch",
                "error",
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message="Runtime task owner seat differs from the WorkItem seat.",
                details={"expected_seat_id": expected_seat_id, "task_seat_id": task_seat_id},
            )
        )

    expected_turn_mode = _expected_turn_mode(work_item)
    task_turn_mode = _clean(task_metadata.get("current_turn_mode"))
    if expected_turn_mode:
        if task_turn_mode and task_turn_mode != expected_turn_mode:
            issues.append(
                _issue(
                    "turn_mode_mismatch",
                    "error",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Runtime task current_turn_mode differs from the WorkItem mode.",
                    details={"expected_turn_mode": expected_turn_mode, "task_turn_mode": task_turn_mode},
                )
            )
        elif not task_turn_mode and expected_kind in _AUXILIARY_TURN_KINDS:
            issues.append(
                _issue(
                    "turn_mode_missing",
                    "warning",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Auxiliary runtime task is missing the WorkItem current_turn_mode execution copy.",
                    details={"expected_turn_mode": expected_turn_mode, "work_kind": expected_kind},
                )
            )

    has_work_item_context = work_item_by_id is not None
    work_item_by_id = dict(work_item_by_id or {})
    if is_review_execution_work_item_metadata(item_metadata):
        target_id = _clean(item_metadata.get("review_target_work_item_id"))
        target = work_item_by_id.get(target_id) if target_id else None
        if not target_id:
            issues.append(
                _issue(
                    "review_target_missing",
                    "error",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Review runtime WorkItem is missing review_target_work_item_id.",
                )
            )
        elif has_work_item_context and target is None:
            issues.append(
                _issue(
                    "review_target_unresolved",
                    "error",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Review runtime WorkItem target does not exist in the active WorkItem set.",
                    details={"review_target_work_item_id": target_id},
                )
            )
        elif target is not None:
            target_metadata = dict(target.metadata or {})
            expected_review_role = (
                _clean(target_metadata.get("review_owner_role_id"))
                or _clean(getattr(target, "manager_role_id", ""))
            )
            expected_review_seat = (
                _clean(target_metadata.get("review_owner_seat_id"))
                or _clean(getattr(target, "manager_seat_id", ""))
            )
            if expected_review_role and expected_review_role != expected_role_id:
                issues.append(
                    _issue(
                        "review_owner_mismatch",
                        "error",
                        task=task,
                        work_item=work_item,
                        projection_id=projection_id,
                        message="Review WorkItem owner role does not match the target WorkItem review owner.",
                        details={"expected_review_role_id": expected_review_role, "review_work_item_role_id": expected_role_id},
                    )
                )
            if expected_review_seat and expected_review_seat != expected_seat_id:
                issues.append(
                    _issue(
                        "review_owner_mismatch",
                        "error",
                        task=task,
                        work_item=work_item,
                        projection_id=projection_id,
                        message="Review WorkItem owner seat does not match the target WorkItem review owner.",
                        details={"expected_review_seat_id": expected_review_seat, "review_work_item_seat_id": expected_seat_id},
                    )
                )
            review_meta_role = _clean(item_metadata.get("review_owner_role_id"))
            review_meta_seat = _clean(item_metadata.get("review_owner_seat_id"))
            if expected_review_role and review_meta_role and review_meta_role != expected_review_role:
                issues.append(
                    _issue(
                        "review_owner_metadata_mismatch",
                        "warning",
                        task=task,
                        work_item=work_item,
                        projection_id=projection_id,
                        message="Review WorkItem metadata owner role differs from the target review owner.",
                        details={"expected_review_role_id": expected_review_role, "metadata_review_owner_role_id": review_meta_role},
                    )
                )
            if expected_review_seat and review_meta_seat and review_meta_seat != expected_review_seat:
                issues.append(
                    _issue(
                        "review_owner_metadata_mismatch",
                        "warning",
                        task=task,
                        work_item=work_item,
                        projection_id=projection_id,
                        message="Review WorkItem metadata owner seat differs from the target review owner.",
                        details={"expected_review_seat_id": expected_review_seat, "metadata_review_owner_seat_id": review_meta_seat},
                    )
                )

    if is_report_execution_work_item_metadata(item_metadata):
        target_id = _clean(item_metadata.get("report_target_work_item_id"))
        if not target_id:
            issues.append(
                _issue(
                    "report_target_missing",
                    "error",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Report runtime WorkItem is missing report_target_work_item_id.",
                )
            )
        elif has_work_item_context and target_id not in work_item_by_id:
            issues.append(
                _issue(
                    "report_target_unresolved",
                    "error",
                    task=task,
                    work_item=work_item,
                    projection_id=projection_id,
                    message="Report runtime WorkItem target does not exist in the active WorkItem set.",
                    details={"report_target_work_item_id": target_id},
                )
            )

    for ownership_issue in validate_metadata_ownership(work_item, task):
        issues.append(
            _issue(
                ownership_issue.code,
                ownership_issue.severity,
                task=task,
                work_item=work_item,
                projection_id=projection_id,
                message=ownership_issue.message,
                details={
                    "metadata_key": ownership_issue.key,
                    "owner": ownership_issue.owner,
                    **dict(ownership_issue.details or {}),
                },
            )
        )

    return issues


def _work_item_expects_runtime_projection(work_item: DelegationWorkItem) -> bool:
    metadata = dict(work_item.metadata or {})
    if not is_work_item_runtime_metadata(metadata):
        return False
    runtime_model = _runtime_model(metadata)
    if runtime_model and runtime_model not in _RUNTIME_MODELS:
        return False
    if _is_done_phase(getattr(work_item, "phase", "")):
        return False
    kind = _expected_work_kind(work_item)
    if should_hide_work_item_from_company_kanban(metadata) and kind not in _HIDDEN_RUNNABLE_TURN_KINDS:
        return False
    return bool(projection_id_for_work_item(work_item))


def diagnose_work_item_runtime_projections(
    tasks: Iterable[Task],
    work_items: Iterable[DelegationWorkItem],
) -> list[WorkItemRuntimeInvariantIssue]:
    """Return projection invariant issues without mutating Tasks or the DB."""
    task_list = list(tasks or [])
    work_item_list = list(work_items or [])
    work_item_by_id = {
        _clean(getattr(item, "work_item_id", "")): item
        for item in work_item_list
        if _clean(getattr(item, "work_item_id", ""))
    }
    issues: list[WorkItemRuntimeInvariantIssue] = []
    linked_work_item_ids: set[str] = set()
    for task in task_list:
        if not is_company_runtime_projection_task(task):
            continue
        linked_work_item_id = linked_work_item_id_for_task(task)
        if linked_work_item_id:
            linked_work_item_ids.add(linked_work_item_id)
        issues.extend(
            validate_work_item_runtime_projection(
                task,
                work_item_by_id.get(linked_work_item_id) if linked_work_item_id else None,
                work_item_by_id=work_item_by_id,
            )
        )

    for work_item in work_item_list:
        wid = _clean(getattr(work_item, "work_item_id", ""))
        if not wid or wid in linked_work_item_ids:
            continue
        if not _work_item_expects_runtime_projection(work_item):
            continue
        issues.append(
            _issue(
                "work_item_missing_runtime_task",
                "error",
                work_item=work_item,
                projection_id=projection_id_for_work_item(work_item),
                message="Non-terminal company WorkItem has no linked runtime Task projection after materialization.",
                details={"phase": _phase_value(getattr(work_item, "phase", "")), "work_kind": _expected_work_kind(work_item)},
            )
        )
    return issues
