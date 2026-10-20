"""Fix 2 + Fix 5 PR1/PR2 regression tests: canonical role_session_id
generator + DB merge migration.

Fix 2 pinned the ID format to ``role-runtime::{run}::{team}::{role}``
to collapse three divergent generators that produced 2–3 rows per role
in new16/app12. Fix 5 PR1 went further and dropped ``team_instance_id``
from the key — the canonical form is now ``role-runtime::{run}::{role}``
(same role = one session in a run, regardless of which team context
spawned the work). PR2's merge migration collapses every legacy row
shape (4-segment, seat-embedded, ``_no_team`` sentinel) into that
canonical 3-segment form and preserves inbox / memory state.

Canonical generator tests pin the new ID format. Migration tests seed
a pre-Fix-5 database with the exact shapes observed in app12/app13,
run the migration, and assert one canonical row per role with every
foreign reference re-pointed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from opc.core.models import (
    DelegationWorkItem,
    Phase,
    RoleRuntimeSession,
    Task,
    TaskStatus,
)
from opc.database.store import OPCStore
from opc.layer2_organization.company_runtime import (
    canonical_role_session_id,
    parse_role_session_id,
)


# ── Part 1: canonical generator pure unit tests ──────────────────────────


class CanonicalRoleSessionIdTests(unittest.TestCase):
    def test_canonical_form_ignores_team_instance(self) -> None:
        """Fix 5 PR1: ``team_instance_id`` belongs on work items, not on
        sessions. The 3-segment canonical form is role-scoped so the same
        role in different team contexts resolves to a single session."""
        sid = canonical_role_session_id(
            run_id="run-1", role_id="cto", team_instance_id="ti-alpha"
        )
        self.assertEqual(sid, "role-runtime::run-1::cto")

    def test_same_id_when_team_instance_blank(self) -> None:
        sid = canonical_role_session_id(
            run_id="run-1", role_id="cto", team_instance_id=""
        )
        self.assertEqual(sid, "role-runtime::run-1::cto")

    def test_whitespace_team_instance_ignored(self) -> None:
        sid = canonical_role_session_id(
            run_id="run-1", role_id="cto", team_instance_id="   "
        )
        self.assertEqual(sid, "role-runtime::run-1::cto")

    def test_team_instance_arg_accepted_for_api_compat(self) -> None:
        """Existing callers still pass ``team_instance_id`` — the signature
        must accept it and silently ignore it (return the role-scoped ID)."""
        with_team = canonical_role_session_id(
            run_id="r", role_id="cto", team_instance_id="ti-alpha"
        )
        without_team = canonical_role_session_id(run_id="r", role_id="cto")
        self.assertEqual(with_team, without_team)

    def test_requires_run_id(self) -> None:
        with self.assertRaises(ValueError):
            canonical_role_session_id(run_id="", role_id="cto")
        with self.assertRaises(ValueError):
            canonical_role_session_id(run_id="   ", role_id="cto")

    def test_requires_role_id(self) -> None:
        with self.assertRaises(ValueError):
            canonical_role_session_id(run_id="run-1", role_id="")

    def test_deterministic(self) -> None:
        # Calling twice must return the same ID — no timestamps, no uuids.
        a = canonical_role_session_id(run_id="r", role_id="cto")
        b = canonical_role_session_id(run_id="r", role_id="cto")
        self.assertEqual(a, b)

    def test_parse_roundtrip(self) -> None:
        sid = canonical_role_session_id(run_id="r", role_id="cto")
        parsed = parse_role_session_id(sid)
        self.assertIsNotNone(parsed)
        run_id, role_id = parsed
        self.assertEqual(run_id, "r")
        self.assertEqual(role_id, "cto")

    def test_parse_rejects_legacy_four_segment_form(self) -> None:
        """Fix-2 era 4-segment form (``role-runtime::run::team::role``)
        is now legacy — the parser must reject it so callers can detect
        legacy rows and route through the migration path instead of
        trusting parsed bits."""
        self.assertIsNone(
            parse_role_session_id("role-runtime::run-1::ti-alpha::cto")
        )
        self.assertIsNone(
            parse_role_session_id("role-runtime::run-1::_no_team::cto")
        )

    def test_parse_rejects_legacy_seat_form(self) -> None:
        # The old seat-embedded form: ``role-runtime::run::seat::team::ceo::cto``
        # has 5 segments and is not canonical.
        self.assertIsNone(
            parse_role_session_id("role-runtime::run::seat::team::ceo::cto")
        )

    def test_parse_rejects_non_role_runtime_prefix(self) -> None:
        self.assertIsNone(
            parse_role_session_id("role-session::ephemeral::abc")
        )
        self.assertIsNone(parse_role_session_id(""))
        self.assertIsNone(parse_role_session_id(None))  # type: ignore[arg-type]


# ── Part 2: DB migration integration tests ───────────────────────────────


def _now_iso() -> str:
    return datetime.now().isoformat()


class RoleSessionIdMigrationTests(unittest.IsolatedAsyncioTestCase):
    """Seed a store the way app12/app13 left it, then call the merge
    migration and assert: one canonical row per (run_id, role_id), every
    foreign reference re-pointed, active row's scalar state preserved."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _raw_insert_role_session(
        self,
        *,
        role_session_id: str,
        run_id: str,
        role_id: str,
        team_instance_id: str = "",
        status: str = "idle",
        focused: str = "",
        updated_at: str | None = None,
    ) -> None:
        """Insert directly via SQL so we bypass save_role_runtime_session's
        upsert-by-PK behaviour and keep the legacy row intact for the
        migration to collapse."""
        ts = updated_at or _now_iso()
        await self.store._db.execute(
            """INSERT INTO role_runtime_sessions
               (role_session_id, run_id, project_id, team_instance_id, team_id,
                role_id, seat_id, seat_state_id, employee_id,
                focused_work_item_id, background_work_item_ids,
                manager_role_ids, manager_seat_ids, seat_ids,
                adapter_session_state, inbox_state, memory_slices_by_work_item,
                resume_state, current_work_item, latest_notification,
                manager_digest, status, metadata, created_at, updated_at)
               VALUES (?, ?, 'p', ?, 'team::x',
                       ?, '', '', '',
                       ?, '[]',
                       '[]', '[]', '[]',
                       '{}', '{}', '{}',
                       '{}', '{}', '{}',
                       '{}', ?, '{}', ?, ?)""",
            (
                role_session_id,
                run_id,
                team_instance_id,
                role_id,
                focused,
                status,
                ts,
                ts,
            ),
        )
        # Mirror into delegation_role_sessions (same shape).
        await self.store._db.execute(
            """INSERT INTO delegation_role_sessions
               (role_session_id, run_id, project_id, team_instance_id, team_id,
                role_id, seat_id, seat_state_id, employee_id,
                focused_work_item_id, background_work_item_ids,
                manager_role_ids, manager_seat_ids, seat_ids,
                adapter_session_state, inbox_state, memory_slices_by_work_item,
                resume_state, current_work_item, latest_notification,
                manager_digest, status, metadata, created_at, updated_at)
               VALUES (?, ?, 'p', ?, 'team::x',
                       ?, '', '', '',
                       ?, '[]',
                       '[]', '[]', '[]',
                       '{}', '{}', '{}',
                       '{}', '{}', '{}',
                       '{}', ?, '{}', ?, ?)""",
            (
                role_session_id,
                run_id,
                team_instance_id,
                role_id,
                focused,
                status,
                ts,
                ts,
            ),
        )
        await self.store._db.commit()

    async def test_three_legacy_rows_collapse_to_canonical(self) -> None:
        """The app12 scenario: cto role has 3 rows — full-form idle,
        short-form running with focus, seat-form idle with a parent's
        claim. After migration: one canonical 3-segment row carrying the
        active ``running`` status and the focused_work_item_id from the
        running row."""
        run_id = "run-app12"
        role_id = "cto"
        team_instance_id = "team-instance-cto-home"
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::{team_instance_id}::team::cto::cto",
            run_id=run_id, role_id=role_id,
            team_instance_id=team_instance_id,
            status="idle",
            updated_at="2026-04-20T10:00:00",
        )
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::{team_instance_id}::cto",
            run_id=run_id, role_id=role_id,
            team_instance_id=team_instance_id,
            status="running",
            focused="review::d59c9f70::v5",
            updated_at="2026-04-20T13:25:00",
        )
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::seat::team::ceo::cto",
            run_id=run_id, role_id=role_id,
            team_instance_id="",
            status="idle",
            updated_at="2026-04-20T13:12:00",
        )

        stats = await self.store._migrate_role_sessions_merge_by_role()
        self.assertEqual(stats["groups"], 1)
        # 3 legacy rows deleted, 1 canonical row written (the merge target).
        self.assertEqual(stats["deleted"], 3)
        self.assertEqual(stats["canonical_written"], 1)

        async with self.store._db.execute(
            "SELECT role_session_id, status, focused_work_item_id, team_instance_id "
            "FROM role_runtime_sessions WHERE run_id=? AND role_id=?",
            (run_id, role_id),
        ) as cursor:
            remaining = await cursor.fetchall()
        self.assertEqual(len(remaining), 1)
        canonical = remaining[0]
        expected_id = canonical_role_session_id(run_id=run_id, role_id=role_id)
        self.assertEqual(canonical[0], expected_id)
        # Active row's scalar state won: running status + focus preserved.
        self.assertEqual(canonical[1], "running")
        self.assertEqual(canonical[2], "review::d59c9f70::v5")
        # Team instance populated from the active row for diagnostics
        # (no longer part of the PK post-Fix-5).
        self.assertEqual(canonical[3], team_instance_id)

    async def test_claim_reference_rewritten_to_canonical(self) -> None:
        """The stuck-parent bug from app12: a parent work item's claim
        pointed at the seat-form idle session; the "live" role session had
        a different ID. After migration, the claim must point to the
        surviving canonical ID."""
        run_id = "run-claim-test"
        role_id = "cto"
        team_instance_id = "ti-cto"
        # Legacy full-form — will be merged into the 3-segment canonical.
        full_id = f"role-runtime::{run_id}::{team_instance_id}::role::cto"
        seat_id = f"role-runtime::{run_id}::seat::team::ceo::cto"
        await self._raw_insert_role_session(
            role_session_id=full_id,
            run_id=run_id, role_id=role_id,
            team_instance_id=team_instance_id,
            status="running",
            focused="live-work",
            updated_at="2026-04-20T13:25:00",
        )
        await self._raw_insert_role_session(
            role_session_id=seat_id,
            run_id=run_id, role_id=role_id,
            team_instance_id="",
            status="idle",
            updated_at="2026-04-20T13:12:00",
        )

        # Parent work item: claim held by the seat-form idle session
        # (exactly what app12's cdb248d8 row looked like).
        parent = DelegationWorkItem(
            work_item_id="parent-stuck",
            run_id=run_id,
            cell_id="c",
            team_instance_id=team_instance_id,
            team_id="team::x",
            role_id=role_id,
            seat_id="seat::team::ceo::cto",
            role_runtime_session_id=seat_id,
            claimed_by_role_runtime_session_id=seat_id,
            claimed_by_seat_id="seat::team::ceo::cto",
            manager_role_id="ceo",
            manager_seat_id="seat::team::ceo::ceo",
            title="Engineer the translation app slice",
            phase=Phase.WAITING_FOR_CHILDREN,
            metadata={"assigned_role_runtime_id": seat_id},
        )
        await self.store.save_delegation_work_item(parent)

        await self.store._migrate_role_sessions_merge_by_role()

        expected_canonical = canonical_role_session_id(
            run_id=run_id, role_id=role_id
        )
        refreshed = await self.store.get_delegation_work_item("parent-stuck")
        self.assertEqual(
            refreshed.role_runtime_session_id, expected_canonical,
            "role_runtime_session_id column was not redirected",
        )
        self.assertEqual(
            refreshed.claimed_by_role_runtime_session_id, expected_canonical,
            "claimed_by_role_runtime_session_id was not redirected — parent would remain stuck",
        )
        self.assertEqual(
            refreshed.metadata.get("assigned_role_runtime_id"),
            expected_canonical,
            "metadata.assigned_role_runtime_id JSON reference was not redirected",
        )

    async def test_task_metadata_reference_rewritten(self) -> None:
        """``tasks.metadata.delegation_role_session_id`` is the task-side
        mirror of the same reference. Must be redirected too, or the
        runtime will look up a non-existent session post-migration."""
        run_id = "run-task-meta"
        role_id = "cmo"
        canonical = canonical_role_session_id(run_id=run_id, role_id=role_id)
        legacy = f"role-runtime::{run_id}::seat::team::ceo::cmo"
        # The canonical PK row is pre-seeded (simulating a row that was
        # already at the 3-segment shape).
        await self._raw_insert_role_session(
            role_session_id=canonical,
            run_id=run_id, role_id=role_id,
            team_instance_id="ti-cmo",
            status="running",
            focused="some-work",
            updated_at="2026-04-20T13:25:00",
        )
        await self._raw_insert_role_session(
            role_session_id=legacy,
            run_id=run_id, role_id=role_id,
            team_instance_id="",
            status="idle",
            updated_at="2026-04-20T13:10:00",
        )

        task = Task(
            id="task-1",
            title="t", description="d",
            assigned_to="cmo",
            status=TaskStatus.RUNNING,
            project_id="p", session_id="s",
            metadata={"delegation_role_session_id": legacy},
        )
        await self.store.save_task(task)

        await self.store._migrate_role_sessions_merge_by_role()

        refreshed = await self.store.get_task("task-1")
        self.assertEqual(
            refreshed.metadata.get("delegation_role_session_id"),
            canonical,
        )

    async def test_migration_is_idempotent(self) -> None:
        """Running the migration twice must produce no additional changes
        on the second pass — every group is already collapsed."""
        run_id = "run-idem"
        role_id = "coo"
        tid = "ti-coo"
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::{tid}::coo",
            run_id=run_id, role_id=role_id,
            team_instance_id=tid,
            status="running",
        )
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::seat::coo",
            run_id=run_id, role_id=role_id,
            status="idle",
        )

        first = await self.store._migrate_role_sessions_merge_by_role()
        second = await self.store._migrate_role_sessions_merge_by_role()
        self.assertEqual(first["groups"], 1)
        self.assertEqual(first["deleted"], 2)  # both legacy rows gone
        self.assertEqual(first["canonical_written"], 1)
        self.assertEqual(second["groups"], 0)
        self.assertEqual(second["deleted"], 0)

    async def test_single_row_already_canonical_is_noop(self) -> None:
        """The common post-migration case: a single row already at the
        canonical 3-segment PK. Migration must not touch it."""
        run_id = "run-x"
        role_id = "cto"
        canonical = canonical_role_session_id(run_id=run_id, role_id=role_id)
        await self._raw_insert_role_session(
            role_session_id=canonical,
            run_id=run_id, role_id=role_id,
            team_instance_id="ti",
        )
        stats = await self.store._migrate_role_sessions_merge_by_role()
        self.assertEqual(stats["groups"], 0)

    async def test_single_legacy_row_renamed_to_canonical(self) -> None:
        """A lone legacy row (4-segment / seat-embedded / sentinel) must
        be renamed to the 3-segment canonical form, not skipped. Without
        this the runtime would look up the canonical PK and miss the
        legacy row entirely post-Fix-5."""
        run_id = "run-legacy"
        role_id = "designer"
        legacy = f"role-runtime::{run_id}::ti-design::{role_id}"
        await self._raw_insert_role_session(
            role_session_id=legacy,
            run_id=run_id, role_id=role_id,
            team_instance_id="ti-design",
            status="running",
            focused="wi-legacy-focus",
        )

        stats = await self.store._migrate_role_sessions_merge_by_role()
        self.assertEqual(stats["groups"], 1)
        self.assertEqual(stats["deleted"], 1)

        expected = canonical_role_session_id(run_id=run_id, role_id=role_id)
        async with self.store._db.execute(
            "SELECT role_session_id, status, focused_work_item_id "
            "FROM role_runtime_sessions WHERE run_id=? AND role_id=?",
            (run_id, role_id),
        ) as cursor:
            rows = await cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], expected)
        self.assertEqual(rows[0][1], "running")
        self.assertEqual(rows[0][2], "wi-legacy-focus")

    async def test_initialize_runs_migration_automatically(self) -> None:
        """Fresh stores call the migration from ``initialize``. Seed a
        duplicate, close, reopen, and verify the second open collapsed
        the group without explicit call."""
        run_id = "run-auto"
        role_id = "ceo"
        tid = "ti-ceo"
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::{tid}::role::ceo",
            run_id=run_id, role_id=role_id,
            team_instance_id=tid,
            status="running",
        )
        await self._raw_insert_role_session(
            role_session_id=f"role-runtime::{run_id}::seat::ceo",
            run_id=run_id, role_id=role_id,
            status="idle",
        )
        # Close and reopen (new initialize call).
        await self.store.close()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

        async with self.store._db.execute(
            "SELECT COUNT(*) FROM role_runtime_sessions WHERE run_id=? AND role_id=?",
            (run_id, role_id),
        ) as cursor:
            (count,) = await cursor.fetchone()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
