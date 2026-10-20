"""Fix 5 PR2 rich-merge tests for ``_migrate_role_sessions_merge_by_role``.

These tests exercise the field-level merge policy that preserves session
state — inbox, memory, adapter_session_state, list unions — when multiple
legacy ``role_runtime_sessions`` rows collapse into the canonical
3-segment PK.

The old Fix-2 migration picked one "winner" row and dropped the rest,
losing the losers' inbox and memory. PR2 merges them instead: the user's
codex sessions, unread inbox messages, and per-work-item memory slices
that lived on the losing rows all survive the collapse.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from opc.database.store import OPCStore
from opc.layer2_organization.company_runtime import canonical_role_session_id


def _iso(offset_minutes: int = 0) -> str:
    # Deterministic anchor so test ordering is stable regardless of wall clock.
    base = datetime(2026, 4, 20, 13, 0, 0)
    return (base.replace(minute=(base.minute + offset_minutes) % 60)).isoformat()


class RichMergeMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = OPCStore(db_path=Path(self._tmpdir.name) / "store.db")
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _raw_insert(
        self,
        *,
        role_session_id: str,
        run_id: str,
        role_id: str,
        team_instance_id: str = "",
        status: str = "idle",
        focused: str = "",
        inbox_messages: list[dict] | None = None,
        memory_slices: dict[str, list] | None = None,
        adapter_state: dict | None = None,
        background_ids: list[str] | None = None,
        manager_role_ids: list[str] | None = None,
        seat_ids: list[str] | None = None,
        updated_at: str | None = None,
    ) -> None:
        ts = updated_at or _iso()
        inbox_payload = json.dumps({"messages": list(inbox_messages or [])})
        memory_payload = json.dumps(dict(memory_slices or {}))
        adapter_payload = json.dumps(dict(adapter_state or {}))
        bg_payload = json.dumps(list(background_ids or []))
        mgr_payload = json.dumps(list(manager_role_ids or []))
        seat_payload = json.dumps(list(seat_ids or []))
        for table in ("role_runtime_sessions", "delegation_role_sessions"):
            await self.store._db.execute(
                f"""INSERT INTO {table}
                    (role_session_id, run_id, project_id, team_instance_id, team_id,
                     role_id, seat_id, seat_state_id, employee_id,
                     focused_work_item_id, background_work_item_ids,
                     manager_role_ids, manager_seat_ids, seat_ids,
                     adapter_session_state, inbox_state, memory_slices_by_work_item,
                     resume_state, current_work_item, latest_notification,
                     manager_digest, status, metadata, created_at, updated_at)
                    VALUES (?, ?, 'p', ?, 'team::x',
                            ?, '', '', '',
                            ?, ?,
                            ?, '[]', ?,
                            ?, ?, ?,
                            '{{}}', '{{}}', '{{}}',
                            '{{}}', ?, '{{}}', ?, ?)""",
                (
                    role_session_id, run_id, team_instance_id, role_id,
                    focused, bg_payload,
                    mgr_payload, seat_payload,
                    adapter_payload, inbox_payload, memory_payload,
                    status, ts, ts,
                ),
            )
        await self.store._db.commit()

    async def _read_canonical(self, run_id: str, role_id: str) -> dict:
        canonical = canonical_role_session_id(run_id=run_id, role_id=role_id)
        async with self.store._db.execute(
            """SELECT role_session_id, status, focused_work_item_id,
                      background_work_item_ids, manager_role_ids, seat_ids,
                      inbox_state, memory_slices_by_work_item,
                      adapter_session_state, metadata
               FROM role_runtime_sessions
               WHERE role_session_id=?""",
            (canonical,),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None, f"canonical row missing for {canonical}"
        return {
            "role_session_id": row[0],
            "status": row[1],
            "focused_work_item_id": row[2],
            "background_work_item_ids": json.loads(row[3] or "[]"),
            "manager_role_ids": json.loads(row[4] or "[]"),
            "seat_ids": json.loads(row[5] or "[]"),
            "inbox_state": json.loads(row[6] or "{}"),
            "memory_slices_by_work_item": json.loads(row[7] or "{}"),
            "adapter_session_state": json.loads(row[8] or "{}"),
            "metadata": json.loads(row[9] or "{}"),
        }

    async def test_inbox_messages_merge_across_rows_dedup_and_sort(self) -> None:
        run_id, role_id = "run-inbox", "cto"
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-a::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-a",
            inbox_messages=[
                {"message_id": "m1", "timestamp": "2026-04-20T10:00:00", "body": "from A1"},
                {"message_id": "m2", "timestamp": "2026-04-20T12:00:00", "body": "from A2"},
            ],
            updated_at="2026-04-20T13:00:00",
        )
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-b::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-b",
            inbox_messages=[
                {"message_id": "m2", "timestamp": "2026-04-20T12:00:00", "body": "from A2"},
                {"message_id": "m3", "timestamp": "2026-04-20T11:00:00", "body": "from B1"},
            ],
            updated_at="2026-04-20T13:25:00",
        )

        stats = await self.store._migrate_role_sessions_merge_by_role()
        self.assertEqual(stats["canonical_written"], 1)
        canonical = await self._read_canonical(run_id, role_id)

        messages = canonical["inbox_state"]["messages"]
        ids = [m["message_id"] for m in messages]
        # m1/m3 preserved, m2 dedup'd, all sorted by timestamp.
        self.assertEqual(ids, ["m1", "m3", "m2"])

    async def test_memory_slices_union_by_work_item(self) -> None:
        run_id, role_id = "run-memory", "cto"
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-a::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-a",
            memory_slices={"wi-1": ["A-note1", "A-note2"]},
            updated_at="2026-04-20T13:00:00",
        )
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-b::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-b",
            memory_slices={
                "wi-1": ["A-note2", "B-note1"],  # A-note2 duplicates across rows
                "wi-2": ["only-B"],
            },
            updated_at="2026-04-20T13:25:00",
        )

        await self.store._migrate_role_sessions_merge_by_role()
        canonical = await self._read_canonical(run_id, role_id)

        wi1 = canonical["memory_slices_by_work_item"]["wi-1"]
        self.assertEqual(wi1, ["A-note1", "A-note2", "B-note1"])
        wi2 = canonical["memory_slices_by_work_item"]["wi-2"]
        self.assertEqual(wi2, ["only-B"])

    async def test_adapter_session_state_latest_wins_with_audit(self) -> None:
        run_id, role_id = "run-adapter", "cto"
        # Older row with its own codex session.
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-a::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-a",
            adapter_state={"codex_session_id": "codex-old", "turns": 3},
            updated_at="2026-04-20T10:00:00",
        )
        # Newer row — its codex session is the live one.
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-b::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-b",
            status="running",
            focused="wi-live",
            adapter_state={"codex_session_id": "codex-live", "turns": 7},
            updated_at="2026-04-20T13:25:00",
        )

        await self.store._migrate_role_sessions_merge_by_role()
        canonical = await self._read_canonical(run_id, role_id)

        # Live codex session preserved.
        self.assertEqual(canonical["adapter_session_state"]["codex_session_id"], "codex-live")
        # Old session preserved under audit so it's still recoverable.
        audit = canonical["metadata"].get("adapter_session_state_audit", [])
        self.assertEqual(len(audit), 1)
        self.assertEqual(
            audit[0]["adapter_session_state"]["codex_session_id"],
            "codex-old",
        )

    async def test_list_fields_unioned_across_rows(self) -> None:
        run_id, role_id = "run-lists", "cto"
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-a::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-a",
            background_ids=["wi-a1", "wi-a2"],
            manager_role_ids=["ceo"],
            seat_ids=["seat::team::ceo::cto"],
            updated_at="2026-04-20T13:00:00",
        )
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-b::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-b",
            status="running",
            background_ids=["wi-a2", "wi-b1"],  # wi-a2 overlaps
            manager_role_ids=["ceo", "coo"],    # ceo overlaps
            seat_ids=["seat::team::coo::cto"],
            updated_at="2026-04-20T13:25:00",
        )

        await self.store._migrate_role_sessions_merge_by_role()
        canonical = await self._read_canonical(run_id, role_id)

        self.assertEqual(sorted(canonical["background_work_item_ids"]),
                         ["wi-a1", "wi-a2", "wi-b1"])
        self.assertEqual(sorted(canonical["manager_role_ids"]), ["ceo", "coo"])
        self.assertEqual(
            sorted(canonical["seat_ids"]),
            ["seat::team::ceo::cto", "seat::team::coo::cto"],
        )

    async def test_active_row_status_and_focus_win_scalar_merge(self) -> None:
        run_id, role_id = "run-active", "cto"
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-a::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-a",
            status="idle",
            updated_at="2026-04-20T13:30:00",  # most recent but idle / no focus
        )
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-b::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-b",
            status="running",
            focused="wi-active",
            updated_at="2026-04-20T13:00:00",  # older but active
        )

        await self.store._migrate_role_sessions_merge_by_role()
        canonical = await self._read_canonical(run_id, role_id)

        # Active row (running + focus) wins even though another row is
        # more recent — state reflects what the role is actually doing.
        self.assertEqual(canonical["status"], "running")
        self.assertEqual(canonical["focused_work_item_id"], "wi-active")

    async def test_team_instance_history_recorded_in_metadata(self) -> None:
        run_id, role_id = "run-team-history", "cto"
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-ceo::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-ceo",
        )
        await self._raw_insert(
            role_session_id=f"role-runtime::{run_id}::ti-cto::{role_id}",
            run_id=run_id, role_id=role_id, team_instance_id="ti-cto",
            status="running",
            updated_at="2026-04-20T13:30:00",
        )

        await self.store._migrate_role_sessions_merge_by_role()
        canonical = await self._read_canonical(run_id, role_id)

        # Both team_instances retained under metadata for diagnostics.
        history = canonical["metadata"].get("team_instance_history", [])
        self.assertEqual(sorted(history), ["ti-ceo", "ti-cto"])


if __name__ == "__main__":
    unittest.main()
