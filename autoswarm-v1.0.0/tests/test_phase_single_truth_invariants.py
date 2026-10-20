"""Tests that lock in "phase is the single source of truth" architecturally.

Covers the stop-cascade regression and the phase → task.status
projection invariant. The old parent-resume regression tests
(which hooked into the deleted ``register_runtime_reconciler``
machinery) were removed in Phase B — the equivalent invariant is
now covered by ``RehydrateParkedMemberSessionsTests`` in
``tests/test_wake_and_delivery.py``, since the dispatcher's
per-tick rehydrate pass has replaced the hook-driven unpark.

The stop-cascade invariant is: when the user clicks Stop,
``_cancel_task_tree`` set ``task.status = CANCELLED`` but did
not cascade to ``work_item.phase``, so the UI kept rendering
the card in the In-Progress column forever (column is derived
from phase, not status).
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from opc.core.models import (
    DelegationCell,
    DelegationRun,
    DelegationWorkItem,
    Phase,
    Task,
    TaskStatus,
)
from opc.database.store import OPCStore
from opc.layer2_organization import phase_hooks  # noqa: F401  registers hooks
from opc.layer2_organization.phase import (
    _PHASE_TRANSITION_HOOKS,
    kanban_column,
    task_status_for_phase,
)
from opc.layer2_organization.work_item_transition import transition_work_item


async def _make_store(tmpdir: Path) -> OPCStore:
    store = OPCStore(db_path=tmpdir / "store.db")
    await store.initialize()
    return store


class StopCascadeBugRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Cancelling a task must cascade all the way to UI column, not
    just the task row. The snapshot builder derives the column from
    work_item.phase, so the cascade must reach phase."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = await _make_store(Path(self._tmpdir.name))

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def test_transition_cancelled_cascades_to_task_status_and_column(self) -> None:
        # Baseline: a running task with a linked work item in phase RUNNING
        await self.store.save_delegation_run(DelegationRun(run_id="r1", session_id="s1"))
        await self.store.save_delegation_cell(DelegationCell(cell_id="c1", run_id="r1"))
        task = Task(
            id="task-x",
            title="child",
            assigned_to="worker",
            status=TaskStatus.RUNNING,
            project_id="proj1",
        )
        await self.store.save_task(task)
        await self.store.save_delegation_work_item(DelegationWorkItem(
            work_item_id="wid-x", run_id="r1", cell_id="c1", role_id="worker",
            phase=Phase.RUNNING,
            claimed_by_role_runtime_session_id="rs-worker",
            claimed_by_seat_id="seat::worker",
        ))
        await self.store.link_work_item_runtime_task("wid-x", "task-x")

        await transition_work_item(
            self.store, "wid-x",
            target_phase=Phase.CANCELLED,
            reason="user_stop",
            release_claim=True,
        )

        # All four layers must agree
        fresh_task = await self.store.get_task("task-x")
        fresh_item = await self.store.get_delegation_work_item("wid-x")
        self.assertEqual(fresh_item.phase, Phase.CANCELLED)
        self.assertEqual(fresh_task.status, TaskStatus.CANCELLED)
        self.assertEqual(kanban_column(fresh_item.phase), "done")
        # Claim released so the seat can be reused (is_orphaned)
        self.assertEqual(fresh_item.claimed_by_role_runtime_session_id, "")
        self.assertEqual(fresh_item.claimed_by_seat_id, "")


class PhaseProjectionInvariantTests(unittest.IsolatedAsyncioTestCase):
    """For every reachable phase transition, after the hook chain fires
    the projected Task.status must exactly match
    ``task_status_for_phase(target_phase)`` — locking in that the two
    fields can never disagree after a transition."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = await _make_store(Path(self._tmpdir.name))
        await self.store.save_delegation_run(DelegationRun(run_id="r1", session_id="s1"))
        await self.store.save_delegation_cell(DelegationCell(cell_id="c1", run_id="r1"))

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _make_item(self, initial_phase: Phase = Phase.READY) -> tuple[DelegationWorkItem, Task]:
        task = Task(
            id="t",
            title="t",
            assigned_to="worker",
            status=task_status_for_phase(initial_phase),
            project_id="proj1",
        )
        await self.store.save_task(task)
        item = DelegationWorkItem(
            work_item_id="wid", run_id="r1", cell_id="c1", role_id="worker", phase=initial_phase,
        )
        await self.store.save_delegation_work_item(item)
        await self.store.link_work_item_runtime_task("wid", "t")
        return item, task

    async def test_every_transition_projects_task_status_cleanly(self) -> None:
        """Walk a representative phase path RUNNING → WAITING_* → APPROVED
        and confirm task.status tracks the projection after each step."""
        _, _ = await self._make_item(Phase.READY)
        path = [
            Phase.RUNNING,
            Phase.AWAITING_MANAGER_REVIEW,
            Phase.APPROVED,
        ]
        for target in path:
            await transition_work_item(
                self.store, "wid",
                target_phase=target,
                reason="fuzz",
            )
            fresh = await self.store.get_task("t")
            expected = task_status_for_phase(target)
            self.assertEqual(
                fresh.status, expected,
                f"after transition to {target.value}, task.status was {fresh.status} but projection expected {expected}",
            )


class DirectStatusWriteLintTest(unittest.TestCase):
    """Grep-based projector whitelist: outside of a small set of
    projector / runtime files, no code should do direct
    ``.status = TaskStatus.(CANCELLED|FAILED)`` (the archetypal bypass
    pattern). If this lint fails, either migrate the new write to
    ``transition_work_item`` or add the file to the whitelist with a
    justifying comment.
    """

    # Allowed to write status directly (each for a documented reason).
    # The entries below are known legacy call-sites: cancel / failure
    # paths that haven't been migrated to ``transition_work_item`` yet.
    # New code SHOULD NOT be added to this list — migrate it to the
    # phase channel instead. Over time these entries should shrink.
    WHITELIST = frozenset({
        # Projectors / intentional direct writes
        "opc/layer2_organization/phase_hooks.py",          # projector (phase → task)
        "opc/layer2_organization/company_runtime.py",      # claim lifecycle
        # Earlier migration landed: company_mode.py no longer writes
        # task.status = TaskStatus.CANCELLED/FAILED directly. All 9 FAILED
        # write sites routed through transition_work_item_from_task.
        # Re-adding requires a justifying comment. (Note: the 7 DONE
        # writes deferred to Step 7 do not match this lint regex.)
        "opc/layer2_organization/phase.py",                # enum defs
        "opc/layer2_organization/work_item_transition.py", # docstring example
        "opc/engine.py",                                   # legacy fallback (wraps transition_work_item)
        "opc/layer3_agent/external_broker.py",             # runtime_session rows (not work_item)
        # Legacy paths, outstanding migration candidates:
        "opc/layer2_organization/reorg_manager.py",        # reorg migration cancel
        "opc/layer2_organization/task_graph.py",           # plain-task fallback
    })

    # Files containing TaskStatus.CANCELLED / FAILED in executable code
    PATTERN = re.compile(r"\.status\s*=\s*TaskStatus\.(CANCELLED|FAILED)")
    MIGRATED_COMPANY_AWARE_FILES = (
        "opc/plugins/office_ui/dispatcher.py",
        "opc/plugins/office_ui/ws_handler.py",
        "opc/plugins/cli_board/services/actions.py",
    )
    MIGRATED_FIVE_STATUS_PATTERN = re.compile(
        r"\.status\s*=\s*TaskStatus\.(PENDING|RUNNING|DONE|FAILED|CANCELLED)"
    )

    def _iter_py_files(self, root: Path):
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p

    def _scan_status_assignments(self, repo_root: Path, files: list[Path], pattern: re.Pattern[str]) -> list[str]:
        offenders: list[str] = []
        for py_file in files:
            rel = str(py_file.relative_to(repo_root))
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                # Skip matches inside docstring / markdown backticks
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                if line_end < 0:
                    line_end = len(text)
                line = text[line_start:line_end]
                if "``" in line or line.lstrip().startswith("#"):
                    continue
                line_no = text[:m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line_no}  {m.group(0)}")
        return offenders

    def test_no_new_direct_status_writes_outside_whitelist(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        files = [
            py_file
            for py_file in self._iter_py_files(repo_root / "opc")
            if str(py_file.relative_to(repo_root)) not in self.WHITELIST
        ]
        offenders = self._scan_status_assignments(repo_root, files, self.PATTERN)
        self.assertFalse(
            offenders,
            "Direct `.status = TaskStatus.CANCELLED/FAILED` bypasses the phase "
            "channel and will cause cross-layer state desync. Route the "
            "change through `transition_work_item(..., phase=...)` instead, "
            "or add the file to WHITELIST (with an outstanding-migration "
            "comment).\n\nOffenders:\n  " + "\n  ".join(offenders),
        )

    def test_migrated_company_aware_paths_do_not_write_core_task_statuses(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        files = [repo_root / rel for rel in self.MIGRATED_COMPANY_AWARE_FILES]
        offenders = self._scan_status_assignments(
            repo_root,
            files,
            self.MIGRATED_FIVE_STATUS_PATTERN,
        )
        self.assertFalse(
            offenders,
            "Migrated UI/CLI/recovery company-aware paths must route "
            "PENDING/RUNNING/DONE/FAILED/CANCELLED through "
            "apply_task_status_transition or transition_work_item_from_task.\n\n"
            "Offenders:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
