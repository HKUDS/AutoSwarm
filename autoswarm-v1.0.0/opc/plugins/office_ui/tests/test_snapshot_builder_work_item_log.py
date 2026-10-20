from __future__ import annotations

from types import SimpleNamespace

from opc.plugins.office_ui.snapshot_builder import _build_session_work_item_log


def _task(
    task_id: str,
    *,
    title: str,
    assigned_to: str = "",
    metadata: dict | None = None,
):
    return SimpleNamespace(
        id=task_id,
        title=title,
        assigned_to=assigned_to,
        metadata=metadata or {},
    )


def test_build_session_work_item_log_aggregates_child_work_item_progress_for_primary_session() -> None:
    parent = _task("parent-task", title="Primary Session")
    child_a = _task(
        "child-a",
        title="CEO Intake",
        assigned_to="ceo",
        metadata={"work_item_projection_id": "ceo__intake", "work_item_role_name": "CEO"},
    )
    child_b = _task(
        "child-b",
        title="CTO Delegation",
        assigned_to="cto",
        metadata={"work_item_projection_id": "cto__delegate", "work_item_role_name": "CTO"},
    )

    work_item_log = _build_session_work_item_log(
        parent,
        task_meta={},
        child_tasks=[child_b, child_a],
        task_meta_map={
            "parent-task": {},
            "child-a": dict(child_a.metadata),
            "child-b": dict(child_b.metadata),
        },
        progress_by_task={
            "child-a": [
                {"timestamp": 10.0, "type": "work_item_started", "detail": "starting CEO Intake"},
                {"timestamp": 12.0, "type": "gate_approved", "detail": "completed"},
            ],
            "child-b": [
                {"timestamp": 11.0, "type": "work_item_started", "detail": "starting CTO Delegation"},
            ],
        },
    )

    assert [entry["execution_turn_id"] for entry in work_item_log] == ["child-a", "child-b", "child-a"]
    assert [entry["work_item_projection_id"] for entry in work_item_log] == ["ceo__intake", "cto__delegate", "ceo__intake"]
    assert [entry["work_item_projection_title"] for entry in work_item_log] == ["CEO", "CTO", "CEO"]
    assert all("projection_id" not in entry for entry in work_item_log)
    assert all("legacy_title" not in entry for entry in work_item_log)
    assert work_item_log[0]["role_name"] == "CEO"
    assert work_item_log[1]["role_name"] == "CTO"


def test_build_session_work_item_log_keeps_child_session_projection_identity() -> None:
    child = _task(
        "child-task",
        title="CEO Intake",
        assigned_to="ceo",
        metadata={"work_item_projection_id": "ceo__intake", "work_item_role_name": "CEO"},
    )

    work_item_log = _build_session_work_item_log(
        child,
        task_meta=dict(child.metadata),
        child_tasks=[],
        task_meta_map={"child-task": dict(child.metadata)},
        progress_by_task={
            "child-task": [
                {"timestamp": 20.0, "type": "work_item_started", "detail": "starting CEO Intake"},
                {"timestamp": 21.0, "type": "awaiting_manager_review", "detail": "awaiting manager review"},
            ],
        },
    )

    assert len(work_item_log) == 2
    assert all(entry["execution_turn_id"] == "child-task" for entry in work_item_log)
    assert all(entry["work_item_projection_id"] == "ceo__intake" for entry in work_item_log)
    assert all("projection_id" not in entry for entry in work_item_log)
    assert all("legacy_title" not in entry for entry in work_item_log)
    assert all(entry["role_name"] == "CEO" for entry in work_item_log)


def test_build_session_work_item_log_uses_projection_metadata_for_entries_without_identity() -> None:
    child = _task("projection-child", title="Projection Work Item", assigned_to="cto")

    work_item_log = _build_session_work_item_log(
        child,
        task_meta={"work_item_projection_id": "projection_item"},
        child_tasks=[],
        task_meta_map={"projection-child": {"work_item_projection_id": "projection_item"}},
        progress_by_task={
            "projection-child": [
                {
                    "timestamp": 30.0,
                    "type": "work_item_started",
                    "detail": "projection progress",
                },
            ],
        },
    )

    assert len(work_item_log) == 1
    assert work_item_log[0]["work_item_projection_id"] == "projection_item"
    assert work_item_log[0]["work_item_projection_title"] == "CTO"
    assert work_item_log[0]["execution_turn_id"] == "projection-child"
    assert "projection_id" not in work_item_log[0]
    assert "legacy_title" not in work_item_log[0]
