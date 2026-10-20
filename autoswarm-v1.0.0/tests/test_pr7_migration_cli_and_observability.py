"""Fix 5 PR7 — kanban queue-depth surface + stuck-session alerts.

Covers:
- Kanban payload surfacing: ``_serialize_session`` includes
  ``pending_queue_depth`` + ``pending_work_item_ids``.
- Stuck-queue runtime event fires at the depth threshold (once, not
  on every subsequent enqueue).
- ``check_stuck_focused_sessions`` emits one event per offender whose
  focus has been held past ``threshold_minutes``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from opc.core.models import (
    CompanyMemberSession,
    DelegationRoleSession,
    DelegationRun,
    DelegationWorkItem,
    Phase,
)
from opc.database.store import OPCStore
from opc.layer2_organization import phase_hooks  # noqa: F401 — registers hooks
from opc.layer2_organization.phase_hooks import (
    STUCK_QUEUE_DEPTH_THRESHOLD,
    check_stuck_focused_sessions,
)


# ── Kanban payload surfacing ─────────────────────────────────────────────


class SerializeSessionSurfacesQueueDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        from opc.layer2_organization.company_runtime import CompanyRuntime

        async def _noop(*_a, **_kw):
            return None

        self.runtime = CompanyRuntime(
            org_engine=None,
            communication=None,
            store=None,
            save_runtime_session=_noop,
            emit_runtime_event=_noop,
        )
        self.role_session_id = "role-runtime::run::cto"
        self.role_session = DelegationRoleSession(
            role_session_id=self.role_session_id,
            run_id="run", role_id="cto",
            pending_work_item_ids=["wi-a", "wi-b", "wi-c"],
        )
        self.runtime.role_sessions[self.role_session_id] = self.role_session
        self.member = CompanyMemberSession(
            member_session_id="m1",
            role_session_id=self.role_session_id,
            role_id="cto",
        )

    def test_serialize_includes_queue_depth_and_ids(self) -> None:
        payload = self.runtime._serialize_session(self.member)
        self.assertEqual(payload["pending_queue_depth"], 3)
        self.assertEqual(
            payload["pending_work_item_ids"], ["wi-a", "wi-b", "wi-c"]
        )

    def test_serialize_with_no_role_session_returns_empty_queue(self) -> None:
        orphan = CompanyMemberSession(
            member_session_id="m2",
            role_session_id="role-runtime::run::unknown",
            role_id="unknown",
        )
        payload = self.runtime._serialize_session(orphan)
        self.assertEqual(payload["pending_queue_depth"], 0)
        self.assertEqual(payload["pending_work_item_ids"], [])


# ── Stuck-queue runtime event ────────────────────────────────────────────


class StuckQueueRuntimeEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()
        self.store.role_serial_queue_enabled = True
        self.sid = "role-runtime::run-pr7::cto"
        await self.store.save_delegation_role_session(
            DelegationRoleSession(
                role_session_id=self.sid,
                run_id="run-pr7",
                role_id="cto",
                focused_work_item_id="wi-busy",
                status="running",
            )
        )

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _enqueue_wid(self, wid: str) -> None:
        item = DelegationWorkItem(
            work_item_id=wid,
            run_id="run-pr7", cell_id="c",
            role_id="cto", seat_id="s",
            role_runtime_session_id=self.sid,
            manager_role_id="ceo", manager_seat_id="seat::ceo",
            title="t", phase=Phase.READY,
        )
        await self.store.save_delegation_work_item(item)

    async def _count_events(self, event_type: str) -> int:
        async with self.store._db.execute(
            "SELECT COUNT(*) FROM runtime_events WHERE event_type=?",
            (event_type,),
        ) as cursor:
            (count,) = await cursor.fetchone()
        return count

    async def test_event_fires_exactly_at_threshold_crossing(self) -> None:
        # Enqueue threshold items — only the one that takes us TO the
        # threshold should fire the event.
        for i in range(STUCK_QUEUE_DEPTH_THRESHOLD):
            await self._enqueue_wid(f"wi-{i}")
        self.assertEqual(
            await self._count_events("stuck_session_queue_depth"), 1
        )

    async def test_event_does_not_fire_below_threshold(self) -> None:
        for i in range(STUCK_QUEUE_DEPTH_THRESHOLD - 1):
            await self._enqueue_wid(f"wi-{i}")
        self.assertEqual(
            await self._count_events("stuck_session_queue_depth"), 0
        )

    async def test_event_does_not_refire_past_threshold(self) -> None:
        # Enqueue enough to cross the threshold, then another. The hook
        # fires only at the crossing (``depth == threshold``), so
        # subsequent enqueues do not re-fire.
        for i in range(STUCK_QUEUE_DEPTH_THRESHOLD + 2):
            await self._enqueue_wid(f"wi-{i}")
        self.assertEqual(
            await self._count_events("stuck_session_queue_depth"), 1
        )


# ── Stuck-focus sweep ────────────────────────────────────────────────────


class CheckStuckFocusedSessionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()
        self.run_id = "run-pr7-stuck"
        # DelegationRun has to exist so list_delegation_runs returns it.
        await self.store.save_delegation_run(
            DelegationRun(
                run_id=self.run_id,
                project_id="proj1",
                session_id="sess",
            )
        )

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _seed_session(
        self,
        *,
        role_id: str,
        focused: str,
        minutes_ago: int,
    ) -> str:
        sid = f"role-runtime::{self.run_id}::{role_id}"
        session = DelegationRoleSession(
            role_session_id=sid,
            run_id=self.run_id,
            role_id=role_id,
            focused_work_item_id=focused,
            status="running",
        )
        # Backdate updated_at so the session looks stale.
        await self.store.save_delegation_role_session(session)
        backdated = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
        for table in ("role_runtime_sessions", "delegation_role_sessions"):
            await self.store._db.execute(
                f"UPDATE {table} SET updated_at = ? WHERE role_session_id = ?",
                (backdated, sid),
            )
        await self.store._db.commit()
        return sid

    async def test_stuck_session_emits_event(self) -> None:
        await self._seed_session(role_id="cto", focused="wi-x", minutes_ago=20)
        emitted = await check_stuck_focused_sessions(
            self.store, run_id=self.run_id, threshold_minutes=10,
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["focused_work_item_id"], "wi-x")
        self.assertGreaterEqual(emitted[0]["focused_for_minutes"], 10)
        # Event row persisted.
        async with self.store._db.execute(
            "SELECT COUNT(*) FROM runtime_events WHERE event_type=?",
            ("stuck_session_focused",),
        ) as cursor:
            (count,) = await cursor.fetchone()
        self.assertEqual(count, 1)

    async def test_fresh_session_does_not_emit(self) -> None:
        await self._seed_session(role_id="cmo", focused="wi-y", minutes_ago=1)
        emitted = await check_stuck_focused_sessions(
            self.store, run_id=self.run_id, threshold_minutes=10,
        )
        self.assertEqual(len(emitted), 0)

    async def test_unfocused_session_never_emits(self) -> None:
        await self._seed_session(role_id="coo", focused="", minutes_ago=60)
        emitted = await check_stuck_focused_sessions(
            self.store, run_id=self.run_id, threshold_minutes=10,
        )
        self.assertEqual(len(emitted), 0)


if __name__ == "__main__":
    unittest.main()
