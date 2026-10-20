"""Invariant tests for company WorkItem runtime projections."""

from __future__ import annotations

from pathlib import Path

from opc.core.models import CompanyMemberSession, DelegationWorkItem, Phase, Task
from opc.layer2_organization.company_runtime import CompanyRuntime
from opc.layer2_organization.work_item_links import set_linked_work_item_id
from opc.layer2_organization.work_item_runtime import mark_work_item_runtime
from opc.layer2_organization.work_item_identity import mark_work_item_projection
from opc.layer2_organization.work_item_runtime_invariants import (
    diagnose_work_item_runtime_projections,
    is_company_runtime_projection_task,
    validate_work_item_runtime_projection,
)


def _runtime_metadata(
    *,
    projection_id: str = "wi-1",
    turn_type: str = "execute",
    seat_id: str = "seat-1",
) -> dict:
    return mark_work_item_projection(
        mark_work_item_runtime(
            {
                "runtime_model": "multi_team_org",
                "delegation_run_id": "run-1",
                "work_item_role_id": "worker",
                "delegation_seat_id": seat_id,
                "work_kind": turn_type,
            }
        ),
        projection_id=projection_id,
        turn_type=turn_type,
    )


def _work_item(
    work_item_id: str = "wi-1",
    *,
    role_id: str = "worker",
    seat_id: str = "seat-1",
    kind: str = "execute",
    phase: Phase = Phase.READY,
    metadata: dict | None = None,
) -> DelegationWorkItem:
    base_metadata = mark_work_item_projection(
        mark_work_item_runtime(
            {
                "runtime_model": "multi_team_org",
                "work_kind": kind,
                "seat_id": seat_id,
            }
        ),
        projection_id=work_item_id,
        turn_type=kind,
    )
    base_metadata.update(dict(metadata or {}))
    return DelegationWorkItem(
        work_item_id=work_item_id,
        run_id="run-1",
        cell_id="cell-1",
        team_id="team-1",
        role_id=role_id,
        seat_id=seat_id,
        title=f"Work {work_item_id}",
        kind=kind,
        projection_id=work_item_id,
        phase=phase,
        metadata=base_metadata,
    )


def _task(task_id: str = "task-1", *, projection_id: str = "wi-1", turn_type: str = "execute", seat_id: str = "seat-1") -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        assigned_to="worker",
        project_id="project-1",
        session_id=f"root:{projection_id}",
        parent_session_id="root",
        metadata=_runtime_metadata(projection_id=projection_id, turn_type=turn_type, seat_id=seat_id),
    )


def test_predicate_requires_runtime_projection_identity() -> None:
    task = _task()
    assert is_company_runtime_projection_task(task)

    plain = Task(id="plain", metadata={"runtime_model": "multi_team_org"})
    assert not is_company_runtime_projection_task(plain)

    synthetic = _task("synthetic")
    synthetic.metadata["synthetic_inbox_turn"] = True
    assert not is_company_runtime_projection_task(synthetic)


def test_missing_link_is_diagnostic_only() -> None:
    task = _task()
    issues = diagnose_work_item_runtime_projections([task], [_work_item()])

    assert {issue.code for issue in issues} == {"missing_link", "missing_work_item", "work_item_missing_runtime_task"}
    assert task.linked_work_item_id == ""


def test_valid_linked_projection_has_no_issues() -> None:
    item = _work_item()
    task = _task()
    set_linked_work_item_id(task, item.work_item_id)

    assert validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item}) == []
    assert diagnose_work_item_runtime_projections([task], [item]) == []


def test_current_turn_mode_execution_copy_without_task_progress_has_no_ownership_warning() -> None:
    item = _work_item(
        "wi-progress-owner",
        metadata={
            "current_turn_mode": "worker_execute",
            "progress_log": ["step 1"],
        },
    )
    task = _task("task-progress-owner", projection_id=item.work_item_id)
    task.metadata["current_turn_mode"] = "worker_execute"
    task.metadata.pop("progress_log", None)
    set_linked_work_item_id(task, item.work_item_id)

    issues = validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item})

    assert "metadata_ownership_violation" not in {issue.code for issue in issues}
    assert "metadata_ownership_conflict" not in {issue.code for issue in issues}


def test_employee_assignment_execution_copy_is_owned_by_work_item() -> None:
    assignment = {"employee_id": "ceo-default-employee", "role_id": "ceo"}
    item = _work_item(
        "wi-employee-owner",
        role_id="ceo",
        metadata={"employee_assignment": assignment},
    )
    task = _task("task-employee-owner", projection_id=item.work_item_id)
    task.assigned_to = "ceo"
    task.metadata["employee_assignment"] = dict(assignment)
    set_linked_work_item_id(task, item.work_item_id)

    issues = validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item})

    assert "metadata_ownership_violation" not in {issue.code for issue in issues}
    assert "metadata_ownership_conflict" not in {issue.code for issue in issues}


def test_prepare_task_for_session_preserves_work_item_owner_seat() -> None:
    item = _work_item(
        "wi-cto",
        role_id="cto",
        seat_id="seat::team::ceo::cto",
        metadata={"employee_assignment": {"employee_id": "cto-employee", "role_id": "cto"}},
    )
    task = _task("task-cto", projection_id=item.work_item_id, seat_id=item.seat_id)
    task.assigned_to = "cto"
    task.metadata["employee_assignment"] = dict(item.metadata["employee_assignment"])
    set_linked_work_item_id(task, item.work_item_id)
    session = CompanyMemberSession(
        member_session_id="role-session::project::cto::cto-employee",
        role_id="cto",
        employee_id="cto-employee",
        team_id="team::cto",
        seat_id="seat::team::cto::cto",
        metadata={
            "team_id": "team::cto",
            "team_instance_id": "team-instance::run-1::team::cto",
            "seat_id": "seat::team::cto::cto",
            "manager_seat_id": "seat::team::ceo::cto",
        },
    )
    runtime = CompanyRuntime(org_engine=None, communication=None)

    runtime.prepare_task_for_session(session, task)

    assert task.metadata["delegation_seat_id"] == "seat::team::ceo::cto"
    assert task.metadata["runtime_session_seat_id"] == "seat::team::cto::cto"
    assert task.metadata["runtime_session_team_id"] == "team::cto"
    issues = validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item})
    assert "owner_seat_mismatch" not in {issue.code for issue in issues}


def test_delivery_alias_canonical_turn_does_not_trip_work_kind_mismatch() -> None:
    item = _work_item(
        "wi-delivery",
        role_id="ceo",
        kind="delivery",
        metadata={
            "work_kind": "delivery",
            "delegation_turn_kind": "delivery",
            "work_item_turn_type": "deliver",
        },
    )
    task = _task("task-delivery", projection_id=item.work_item_id, turn_type="deliver")
    task.assigned_to = "ceo"
    task.metadata["work_kind"] = "delivery"
    task.metadata["delegation_turn_kind"] = "delivery"
    set_linked_work_item_id(task, item.work_item_id)

    issues = validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item})

    assert "work_kind_mismatch" not in {issue.code for issue in issues}
    assert not [issue for issue in issues if issue.severity == "error"]


def test_stale_execute_turn_for_delivery_trips_work_kind_mismatch() -> None:
    item = _work_item(
        "wi-delivery",
        role_id="ceo",
        kind="delivery",
        metadata={
            "work_kind": "delivery",
            "delegation_turn_kind": "delivery",
            "work_item_turn_type": "deliver",
        },
    )
    task = _task("task-delivery", projection_id=item.work_item_id, turn_type="execute")
    task.assigned_to = "ceo"
    task.metadata["work_kind"] = "delivery"
    task.metadata["delegation_turn_kind"] = "delivery"
    set_linked_work_item_id(task, item.work_item_id)

    issues = validate_work_item_runtime_projection(task, item, work_item_by_id={item.work_item_id: item})

    assert "work_kind_mismatch" in {issue.code for issue in issues}


def test_review_owner_mismatch_is_reported_from_target_work_item() -> None:
    target = _work_item(
        "wi-target",
        role_id="engineer",
        seat_id="seat-engineer",
        metadata={
            "review_owner_role_id": "cto",
            "review_owner_seat_id": "seat-cto",
        },
    )
    review = _work_item(
        "review::wi-target::v1",
        role_id="cmo",
        seat_id="seat-cmo",
        kind="review",
        metadata={
            "hidden_from_company_kanban": True,
            "review_execution_work_item": True,
            "review_target_work_item_id": target.work_item_id,
            "review_owner_role_id": "cmo",
            "review_owner_seat_id": "seat-cmo",
            "current_turn_mode": "review_execute",
        },
    )
    task = _task("review-task", projection_id=review.work_item_id, turn_type="review", seat_id=review.seat_id)
    task.assigned_to = review.role_id
    task.metadata["current_turn_mode"] = "review_execute"
    set_linked_work_item_id(task, review.work_item_id)

    issues = validate_work_item_runtime_projection(
        task,
        review,
        work_item_by_id={target.work_item_id: target, review.work_item_id: review},
    )

    assert "review_owner_mismatch" in {issue.code for issue in issues}


def test_auxiliary_turn_mode_is_owned_by_work_item_metadata() -> None:
    report = _work_item(
        "report::wi-target::v1",
        kind="report",
        metadata={
            "hidden_from_company_kanban": True,
            "report_execution_work_item": True,
            "report_target_work_item_id": "wi-target",
            "current_turn_mode": "report_required",
        },
    )
    target = _work_item("wi-target")
    task = _task("report-task", projection_id=report.work_item_id, turn_type="report")
    task.metadata.pop("current_turn_mode", None)
    set_linked_work_item_id(task, report.work_item_id)

    issues = validate_work_item_runtime_projection(
        task,
        report,
        work_item_by_id={target.work_item_id: target, report.work_item_id: report},
    )

    turn_mode_issues = [issue for issue in issues if issue.code == "turn_mode_missing"]
    assert turn_mode_issues
    assert all(issue.severity == "warning" for issue in turn_mode_issues)
    assert not [issue for issue in issues if issue.severity == "error"]


def test_company_hot_paths_do_not_call_runtime_link_repair() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    hot_path_files = [
        repo_root / "opc" / "layer2_organization" / "company_mode.py",
        repo_root / "opc" / "engine.py",
        repo_root / "opc" / "plugins" / "office_ui" / "ws_handler.py",
    ]
    for path in hot_path_files:
        assert "repair_work_item_runtime_link" not in path.read_text()
