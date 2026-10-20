"""End-to-end tests for the D2 phase-transition hook mechanism.

These tests guarantee that whenever a work-item phase changes, the
downstream layers (task.status, role_session.status, dispatcher wake
signal) are kept in sync — preventing the regression that caused the
new11/app04 deadlock where parent CTO/CMO/COO tasks stayed BLOCKED even
after all children were APPROVED.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opc.core.models import (
    DelegationRoleSession,
    DelegationWorkItem,
    Phase,
    Task,
    TaskStatus,
)
from opc.database.store import OPCStore
from opc.layer2_organization import phase_hooks  # noqa: F401  (registers hooks)
from opc.layer2_organization.phase import (
    _PHASE_TRANSITION_HOOKS,
    register_phase_transition_hook,
)


class PhaseHookFiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_hooks_fire_on_phase_change(self) -> None:
        """Every save_delegation_work_item call must fire on_phase_transition
        with the previous + target phase + item."""
        captured: list[tuple] = []

        async def spy(prev, target, item, *, store):
            captured.append((prev, target, item.work_item_id))

        register_phase_transition_hook(spy)
        try:
            item = DelegationWorkItem(
                run_id="r", cell_id="c", role_id="w", seat_id="ws",
                manager_role_id="m", manager_seat_id="ms", title="t",
            )
            await self.store.save_delegation_work_item(item)  # creation
            await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)
            await self.store.update_delegation_work_item(
                item.work_item_id, phase=Phase.AWAITING_MANAGER_REVIEW
            )

            self.assertEqual(len(captured), 3)
            self.assertIsNone(captured[0][0])
            self.assertEqual(captured[0][1], Phase.READY)
            self.assertEqual(captured[1], (Phase.READY, Phase.RUNNING, item.work_item_id))
            self.assertEqual(
                captured[2],
                (Phase.RUNNING, Phase.AWAITING_MANAGER_REVIEW, item.work_item_id),
            )
        finally:
            if spy in _PHASE_TRANSITION_HOOKS:
                _PHASE_TRANSITION_HOOKS.remove(spy)

    async def test_idempotent_write_does_not_propagate(self) -> None:
        """Writing the same phase twice must not re-fire downstream hooks."""
        fired = 0

        async def counter(prev, target, item, *, store):
            nonlocal fired
            if prev != target:
                fired += 1

        register_phase_transition_hook(counter)
        try:
            item = DelegationWorkItem(
                run_id="r", cell_id="c", role_id="w", seat_id="ws",
                manager_role_id="m", manager_seat_id="ms", title="t",
            )
            await self.store.save_delegation_work_item(item)
            await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)
            # Idempotent same-phase write
            await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)

            self.assertEqual(fired, 2)  # None→READY, READY→RUNNING
        finally:
            if counter in _PHASE_TRANSITION_HOOKS:
                _PHASE_TRANSITION_HOOKS.remove(counter)


class TaskStatusSyncHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_task_status_follows_phase(self) -> None:
        task = Task(
            id="task-1", title="t", description="d",
            assigned_to="worker", status=TaskStatus.PENDING,
            project_id="p", session_id="s",
        )
        await self.store.save_task(task)

        item = DelegationWorkItem(
            run_id="r", cell_id="c", role_id="worker", seat_id="ws",
            manager_role_id="m", manager_seat_id="ms",
            title="t",
        )
        await self.store.save_delegation_work_item(item)
        await self.store.link_work_item_runtime_task(item.work_item_id, task.id)
        await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)
        task_after = await self.store.get_task("task-1")
        self.assertEqual(task_after.status, TaskStatus.RUNNING)

        await self.store.update_delegation_work_item(
            item.work_item_id, phase=Phase.AWAITING_MANAGER_REVIEW
        )
        task_after = await self.store.get_task("task-1")
        self.assertEqual(task_after.status, TaskStatus.AWAITING_MANAGER_REVIEW)

        await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.APPROVED)
        task_after = await self.store.get_task("task-1")
        self.assertEqual(task_after.status, TaskStatus.DONE)


# ParentUnblockHookTests (old wake_parent_on_resume_hook regression)
# was removed in Phase B. The equivalent invariant — that the wake
# edge releases the parent claim so is_orphaned/is_dispatchable flips
# True — is now covered by RefreshDependentsClearClaimTests and
# RehydrateParkedMemberSessionsTests in tests/test_wake_and_delivery.py.


class DispatcherWakeHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_dispatcher_wake_fires_on_runnable_transition(self) -> None:
        wake_count = 0

        def wake_cb():
            nonlocal wake_count
            wake_count += 1

        phase_hooks.register_dispatcher_wake(wake_cb)
        try:
            item = DelegationWorkItem(
                run_id="r", cell_id="c", role_id="w", seat_id="ws",
                manager_role_id="m", manager_seat_id="ms", title="t",
            )
            await self.store.save_delegation_work_item(item)
            wake_after_create = wake_count

            await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)
            wake_after_run = wake_count

            # WAITING_FOR_PEER is in IN_PROGRESS_PHASES — not a wake target
            await self.store.update_delegation_work_item(
                item.work_item_id, phase=Phase.WAITING_FOR_PEER
            )
            wake_after_peer = wake_count

            await self.store.update_delegation_work_item(item.work_item_id, phase=Phase.RUNNING)
            wake_final = wake_count

            self.assertGreaterEqual(wake_after_create, 1)
            self.assertGreater(wake_after_run, wake_after_create)
            self.assertEqual(wake_after_peer, wake_after_run)
            self.assertGreater(wake_final, wake_after_peer)
        finally:
            phase_hooks.unregister_dispatcher_wake(wake_cb)


class HookRegistrationInvariantTests(unittest.TestCase):
    def test_default_hooks_registered_at_import(self) -> None:
        # Importing company_mode side-imports phase_hooks
        from opc.layer2_organization import company_mode  # noqa: F401
        names = {h.__name__ for h in _PHASE_TRANSITION_HOOKS}
        # Phase B reduced the required hooks to task.status projection +
        # dispatcher wake. The wake / reconcile / reenqueue hooks have
        # been replaced by the dispatcher's per-tick rehydrate pass.
        required = {
            "sync_task_status_hook",
            "signal_dispatcher_hook",
            # Fix 3: parent dep-frontier refresh fires on terminal child
            # transitions so stuck leaders wake (new16/app12 regression).
            "refresh_dependents_hook",
            # Fix 6: clear stale focus/status on role sessions when their
            # work item terminates (new16/app13 regression).
            "clear_session_focus_on_terminal_hook",
            # Fix 5 PR3: enqueue runnable work for busy sessions. Gated
            # internally on ``store.role_serial_queue_enabled``.
            "enqueue_session_work_on_runnable_hook",
        }
        self.assertTrue(
            required <= names,
            f"missing required hooks; registered: {names}",
        )

    def test_register_is_idempotent(self) -> None:
        from opc.layer2_organization.phase_hooks import sync_task_status_hook
        before = sum(1 for h in _PHASE_TRANSITION_HOOKS if h is sync_task_status_hook)
        register_phase_transition_hook(sync_task_status_hook)
        after = sum(1 for h in _PHASE_TRANSITION_HOOKS if h is sync_task_status_hook)
        self.assertEqual(before, after)
        self.assertEqual(after, 1)


if __name__ == "__main__":
    unittest.main()
