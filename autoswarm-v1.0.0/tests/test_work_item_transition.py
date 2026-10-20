"""Regression tests for ``opc.layer2_organization.work_item_transition``.

Work-item transition adds ``transition_work_item_from_task`` — the task-facing wrapper
that replaces direct ``task.status = ...`` writes in company-mode code.
These tests cover:

* work_item resolution from hydrated task link (or lack thereof → False return)
* TaskStatus → Phase projection with BLOCKED disambiguation
* Silent preservation of persisted phase when the desired transition would
  be invalid (late async race — see docstring in the module).
* Phase passed directly bypasses TaskStatus projection.
* release_claim flag plumbs through to the underlying transition.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opc.core.models import DelegationWorkItem, Phase, Task, TaskStatus
from opc.database.store import OPCStore
from opc.layer2_organization import phase_hooks  # noqa: F401  (register hooks)
from opc.layer2_organization.work_item_links import set_linked_work_item_id
from opc.layer2_organization.work_item_runtime import mark_work_item_runtime
from opc.layer2_organization.work_item_transition import (
    apply_task_status_transition,
    transition_work_item_from_task,
)


def _make_work_item(
    *,
    work_item_id: str = "wi-1",
    run_id: str = "run-1",
    phase: Phase = Phase.READY,
    claimed_by: str = "",
) -> DelegationWorkItem:
    return DelegationWorkItem(
        work_item_id=work_item_id,
        run_id=run_id,
        cell_id="c",
        team_instance_id="ti",
        team_id="t",
        role_id="r",
        seat_id="s",
        title=f"item-{work_item_id}",
        phase=phase,
        claimed_by_role_runtime_session_id=claimed_by,
        claimed_by_seat_id="seat::x" if claimed_by else "",
        metadata={},
    )


def _make_task(
    *,
    task_id: str = "task-1",
    work_item_id: str | None = "wi-1",
    pending_children: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    metadata: dict = {}
    if pending_children is not None:
        metadata["delegation_pending_work_item_ids"] = list(pending_children)
    task = Task(
        id=task_id,
        title="t",
        description="",
        assigned_to="r",
        status=status,
        metadata=metadata,
    )
    if work_item_id:
        set_linked_work_item_id(task, work_item_id)
    return task


class TransitionWorkItemFromTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_returns_false_without_wid_but_syncs_local_status(self) -> None:
        """Task-mode leakage path: no linked work item → helper
        returns False BUT still syncs local task.status so callers that
        subsequently do save_task(task) persist the caller's intent.

        This obviates the need for each migrated call site to add its own
        ``if not ok: task.status = ...`` fallback."""
        task = _make_task(work_item_id=None, status=TaskStatus.RUNNING)
        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.DONE,
            reason="test-no-wid",
        )
        self.assertFalse(ok)
        self.assertEqual(task.status, TaskStatus.DONE)

    async def test_returns_false_without_wid_phase_arg_projects_to_status(self) -> None:
        """Same as above but caller passes a Phase; local task.status is
        the projection via task_status_for_phase."""
        task = _make_task(work_item_id=None, status=TaskStatus.RUNNING)
        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=Phase.APPROVED,
            reason="test-no-wid-phase",
        )
        self.assertFalse(ok)
        self.assertEqual(task.status, TaskStatus.DONE)  # APPROVED → DONE

    async def test_require_work_item_does_not_mutate_company_runtime_without_link(self) -> None:
        task = _make_task(work_item_id=None, status=TaskStatus.RUNNING)
        task.metadata = mark_work_item_runtime({"runtime_model": "multi_team_org"})

        ok = await transition_work_item_from_task(
            self.store,
            task,
            target_status_or_phase=TaskStatus.CANCELLED,
            reason="test-require-work-item",
            require_work_item=True,
        )

        self.assertFalse(ok)
        self.assertEqual(task.status, TaskStatus.RUNNING)

        with self.assertRaisesRegex(RuntimeError, "linked WorkItem"):
            await apply_task_status_transition(
                self.store,
                task,
                target_status_or_phase=TaskStatus.CANCELLED,
                reason="test-apply-require-work-item",
            )
        self.assertEqual(task.status, TaskStatus.RUNNING)

    async def test_apply_plain_task_fallback_persists_status(self) -> None:
        task = _make_task(work_item_id=None, status=TaskStatus.RUNNING)
        await self.store.save_task(task)

        ok = await apply_task_status_transition(
            self.store,
            task,
            target_status_or_phase=TaskStatus.DONE,
            reason="test-plain-apply",
        )

        self.assertFalse(ok)
        fresh = await self.store.get_task(task.id)
        self.assertEqual(fresh.status, TaskStatus.DONE)

    async def test_apply_linked_company_runtime_statuses_project_through_phase_hook(self) -> None:
        cases = [
            (TaskStatus.PENDING, Phase.RUNNING, Phase.READY),
            (TaskStatus.RUNNING, Phase.READY, Phase.RUNNING),
            (TaskStatus.DONE, Phase.AWAITING_MANAGER_REVIEW, Phase.APPROVED),
            (TaskStatus.FAILED, Phase.READY, Phase.FAILED),
            (TaskStatus.CANCELLED, Phase.READY, Phase.CANCELLED),
        ]
        for target_status, initial_phase, expected_phase in cases:
            with self.subTest(target_status=target_status.value):
                wid = f"wi-{target_status.value}"
                tid = f"task-{target_status.value}"
                wi = _make_work_item(work_item_id=wid, phase=initial_phase)
                await self.store.save_delegation_work_item(wi)
                task = _make_task(task_id=tid, work_item_id=wid, status=TaskStatus.RUNNING)
                task.metadata = mark_work_item_runtime({"runtime_model": "multi_team_org"})
                await self.store.save_task(task)
                await self.store.link_work_item_runtime_task(wid, tid)

                ok = await apply_task_status_transition(
                    self.store,
                    task,
                    target_status_or_phase=target_status,
                    reason="test-company-apply",
                    release_claim=target_status == TaskStatus.CANCELLED,
                )

                self.assertTrue(ok)
                after_item = await self.store.get_delegation_work_item(wid)
                after_task = await self.store.get_task(tid)
                self.assertEqual(after_item.phase, expected_phase)
                self.assertEqual(after_task.status, task.status)

    async def test_phase_passed_directly(self) -> None:
        """Caller can pass an explicit Phase; no TaskStatus projection runs."""
        wi = _make_work_item(phase=Phase.READY)
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=Phase.RUNNING,
            reason="test-phase-direct",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.RUNNING)
        # task_id back-reference stamped
        self.assertEqual(after.metadata.get("task_id"), task.id)

    async def test_task_status_projection_running(self) -> None:
        wi = _make_work_item(phase=Phase.READY)
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.RUNNING,
            reason="test-status-running",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.RUNNING)

    async def test_blocked_with_pending_children_routes_to_waiting_for_children(self) -> None:
        """BLOCKED with pending children → WAITING_FOR_CHILDREN.

        Preserves the old task-status projection rule.
        """
        wi = _make_work_item(phase=Phase.RUNNING)
        await self.store.save_delegation_work_item(wi)
        task = _make_task(pending_children=["child-1", "child-2"])

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.BLOCKED,
            reason="test-blocked-children",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.WAITING_FOR_CHILDREN)

    async def test_blocked_without_pending_children_routes_to_paused(self) -> None:
        """BLOCKED with empty/no pending children → PAUSED."""
        wi = _make_work_item(phase=Phase.RUNNING)
        await self.store.save_delegation_work_item(wi)
        task = _make_task(pending_children=[])

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.BLOCKED,
            reason="test-blocked-no-children",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.PAUSED)

    async def test_preserves_invalid_transition_silently(self) -> None:
        """Late async race: caller tries a transition that's not in
        ALLOWED_TRANSITIONS from the persisted phase. Instead of raising
        (which would crash the caller's work-item task), silently preserve
        the persisted phase and return True.
        """
        wi = _make_work_item(phase=Phase.APPROVED)
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        # APPROVED is terminal; APPROVED → RUNNING is not allowed.
        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.RUNNING,
            reason="test-invalid-race",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        # Phase preserved.
        self.assertEqual(after.phase, Phase.APPROVED)

    async def test_release_claim_flag_plumbs_through(self) -> None:
        """release_claim=True clears claimed_by_* atomically with the phase write."""
        wi = _make_work_item(
            phase=Phase.RUNNING,
            claimed_by="session-abc",
        )
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.FAILED,
            reason="test-release-claim",
            release_claim=True,
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.FAILED)
        self.assertEqual(after.claimed_by_role_runtime_session_id, "")
        self.assertEqual(after.claimed_by_seat_id, "")

    async def test_string_target_parsed_as_phase_first(self) -> None:
        """Strings that match a Phase name are parsed as Phase (not TaskStatus).

        ``ready_for_rework`` is a Phase but not a TaskStatus — verify it maps
        to Phase.READY_FOR_REWORK. Use AWAITING_MANAGER_REVIEW as source
        since that's the natural reviewer-returns-rework path and is in
        ALLOWED_TRANSITIONS.
        """
        wi = _make_work_item(phase=Phase.AWAITING_MANAGER_REVIEW)
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase="ready_for_rework",
            reason="test-string-phase",
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.READY_FOR_REWORK)

    async def test_local_task_status_synced_after_transition(self) -> None:
        """After a successful transition the local Task object's ``status``
        attribute reflects the new phase, so a subsequent ``save_task(task)``
        by the caller doesn't race with the hook's DB update."""
        wi = _make_work_item(phase=Phase.RUNNING)
        await self.store.save_delegation_work_item(wi)
        task = _make_task(status=TaskStatus.RUNNING)

        self.assertEqual(task.status, TaskStatus.RUNNING)
        await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=Phase.FAILED,
            reason="test-local-sync",
        )
        self.assertEqual(task.status, TaskStatus.FAILED)

    async def test_local_task_status_not_synced_on_silent_degrade(self) -> None:
        """When the persisted phase is preserved (invalid transition), the
        local task.status must NOT be changed — preserving caller's view of
        what they attempted vs. what actually happened."""
        wi = _make_work_item(phase=Phase.APPROVED)
        await self.store.save_delegation_work_item(wi)
        task = _make_task(status=TaskStatus.RUNNING)

        # APPROVED → RUNNING is invalid; helper degrades silently.
        await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.RUNNING,
            reason="test-degrade-no-sync",
        )
        # Local status unchanged (still RUNNING as we set it).
        self.assertEqual(task.status, TaskStatus.RUNNING)

    async def test_metadata_updates_merged_with_backref(self) -> None:
        """Caller's metadata_updates are merged after the task_id/task_status
        back-reference — caller keys win on conflict."""
        wi = _make_work_item(phase=Phase.RUNNING)
        await self.store.save_delegation_work_item(wi)
        task = _make_task()

        ok = await transition_work_item_from_task(
            self.store, task,
            target_status_or_phase=TaskStatus.AWAITING_MANAGER_REVIEW,
            reason="test-metadata-merge",
            metadata_updates={"review_owner_role_id": "cto", "completion_report": "done"},
        )
        self.assertTrue(ok)
        after = await self.store.get_delegation_work_item("wi-1")
        self.assertEqual(after.phase, Phase.AWAITING_MANAGER_REVIEW)
        self.assertEqual(after.metadata.get("task_id"), task.id)
        self.assertEqual(after.metadata.get("review_owner_role_id"), "cto")
        self.assertEqual(after.metadata.get("completion_report"), "done")


if __name__ == "__main__":
    unittest.main()
