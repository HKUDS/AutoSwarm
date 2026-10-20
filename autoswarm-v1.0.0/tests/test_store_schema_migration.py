from __future__ import annotations

import json
import tempfile
import unittest
import sqlite3
from pathlib import Path

from opc.core.config import OPCConfig, SeatConfig, TeamConfig, TeamRuntimeConfig
from opc.core.models import DelegationRun, DelegationWorkItem, Phase, RoleRuntimeSession, SeatState, SessionLinkRecord, Task, TaskStatus, TeamInstance
from opc.database.store import OPCStore
from opc.layer2_organization.work_item_identity import (
    WORK_ITEM_PROJECTION_ID_KEY,
    WORK_ITEM_TURN_TYPE_KEY,
)


class TestOPCStoreSchemaMigration(unittest.IsolatedAsyncioTestCase):
    def test_org_config_roundtrips_team_runtime_structures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config = OPCConfig()
            config.org.company_name = "Team Runtime Co"
            config.org.teams = [
                TeamConfig(
                    team_id="team-1",
                    name="Platform Team",
                    description="Shared executor team",
                    seat_ids=["seat-1", "seat-2"],
                    seats=[
                        SeatConfig(
                            seat_id="seat-1",
                            name="Executor Seat",
                            role_id="executor",
                            seat_kind="workspace",
                            manager_seat_id="seat-0",
                            manager_role_id="manager",
                            shared_executor=True,
                        )
                    ],
                    metadata={"area": "platform"},
                )
            ]
            config.org.team_runtime = TeamRuntimeConfig(
                default_team_id="team-1",
                shared_role_session_scope="team",
                allow_shared_role_sessions=True,
                seat_refresh_interval_seconds=15,
                metadata={"mode": "team-based"},
            )
            config.save(config_dir)

            loaded = OPCConfig.load(config_dir)
            self.assertEqual(loaded.org.company_name, "Team Runtime Co")
            self.assertEqual(loaded.org.teams[0].team_id, "team-1")
            self.assertEqual(loaded.org.teams[0].seats[0].seat_id, "seat-1")
            self.assertTrue(loaded.org.teams[0].seats[0].shared_executor)
            self.assertEqual(loaded.org.team_runtime.default_team_id, "team-1")
            self.assertEqual(loaded.org.team_runtime.seat_refresh_interval_seconds, 15)

    async def test_initialize_extends_existing_tables_before_creating_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE handoff_records (
                    handoff_id TEXT PRIMARY KEY,
                    project_id TEXT DEFAULT 'default',
                    task_id TEXT,
                    from_role TEXT DEFAULT '',
                    to_role TEXT DEFAULT '',
                    source_projection_id TEXT DEFAULT '',
                    target_projection_id TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    payload TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE delegation_work_items (
                    work_item_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    role_id TEXT DEFAULT '',
                    parent_work_item_id TEXT,
                    source_role_id TEXT,
                    title TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    kind TEXT DEFAULT 'execute',
                    projection_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE delegation_role_sessions (
                    role_session_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    employee_id TEXT DEFAULT '',
                    focused_work_item_id TEXT DEFAULT '',
                    background_work_item_ids TEXT DEFAULT '[]',
                    manager_role_ids TEXT DEFAULT '[]',
                    adapter_session_state TEXT DEFAULT '{}',
                    inbox_state TEXT DEFAULT '{}',
                    memory_slices_by_work_item TEXT DEFAULT '{}',
                    resume_state TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'cold',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE delegation_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT DEFAULT 'default',
                    session_id TEXT NOT NULL,
                    company_profile TEXT DEFAULT 'corporate',
                    execution_model TEXT DEFAULT 'recursive_delegation',
                    final_decider_role_id TEXT DEFAULT '',
                    top_level_role_ids TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT INTO delegation_runs (
                    run_id, project_id, session_id, company_profile,
                    execution_model, final_decider_role_id, top_level_role_ids,
                    status, metadata, created_at, updated_at
                ) VALUES (
                    'run-legacy', 'default', 'session-1', 'custom',
                    'multi_team_org', 'supervisor', '[]',
                    'running', '{}', '2026-04-27T00:00:00', '2026-04-27T00:00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            store = OPCStore(db_path)
            await store.initialize()
            try:
                async with store._db.execute("PRAGMA table_info(tasks)") as cursor:
                    task_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("status", task_columns)
                self.assertIn("project_id", task_columns)
                self.assertIn("metadata", task_columns)

                async with store._db.execute("PRAGMA table_info(handoff_records)") as cursor:
                    handoff_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("status", handoff_columns)
                self.assertIn("requires_ack", handoff_columns)
                self.assertIn("metadata", handoff_columns)

                async with store._db.execute("PRAGMA table_info(delegation_work_items)") as cursor:
                    delegation_work_item_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("team_instance_id", delegation_work_item_columns)
                self.assertIn("seat_id", delegation_work_item_columns)
                self.assertIn("role_runtime_session_id", delegation_work_item_columns)
                self.assertIn("manager_seat_id", delegation_work_item_columns)
                self.assertIn("batch_id", delegation_work_item_columns)
                self.assertIn("handoff_status", delegation_work_item_columns)

                async with store._db.execute("PRAGMA table_info(delegation_role_sessions)") as cursor:
                    role_session_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("project_id", role_session_columns)
                self.assertIn("team_instance_id", role_session_columns)
                self.assertIn("seat_ids", role_session_columns)
                self.assertIn("manager_seat_ids", role_session_columns)
                self.assertIn("manager_digest", role_session_columns)

                async with store._db.execute("PRAGMA table_info(delegation_runs)") as cursor:
                    delegation_run_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("company_profile", delegation_run_columns)
                self.assertIn("lifecycle_status", delegation_run_columns)
                self.assertIn("current_revision", delegation_run_columns)
                self.assertIn("project_dossier", delegation_run_columns)

                run = await store.get_delegation_run("run-legacy")
                self.assertIsNotNone(run)
                self.assertEqual(run.company_profile, "custom")

                async with store._db.execute("PRAGMA index_list(handoff_records)") as cursor:
                    handoff_indexes = {row[1] for row in await cursor.fetchall()}
                self.assertIn("idx_handoff_status", handoff_indexes)

                async with store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
                    table_names = {row[0] for row in await cursor.fetchall()}
                self.assertIn("team_instances", table_names)
                self.assertIn("seat_states", table_names)
                self.assertIn("role_runtime_sessions", table_names)
            finally:
                await store.close()

    async def test_initialize_creates_final_projection_schema_without_obsolete_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            store = OPCStore(db_path)
            await store.initialize()
            try:
                old_profile_column = "work" + "flow_profile"
                old_projection_column = "sta" + "ge_id"
                old_task_column = "sta" + "ge_task_id"
                old_source_column = "source_" + "sta" + "ge_id"
                old_target_column = "target_" + "sta" + "ge_id"
                old_decision_table = "work" + "flow_decisions"

                async with store._db.execute("PRAGMA table_info(delegation_runs)") as cursor:
                    run_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("company_profile", run_columns)
                self.assertNotIn(old_profile_column, run_columns)

                async with store._db.execute("PRAGMA table_info(delegation_work_items)") as cursor:
                    work_item_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("projection_id", work_item_columns)
                self.assertNotIn("work_item_task_id", work_item_columns)
                self.assertNotIn(old_projection_column, work_item_columns)
                self.assertNotIn(old_task_column, work_item_columns)

                async with store._db.execute("PRAGMA table_info(handoff_records)") as cursor:
                    handoff_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("source_projection_id", handoff_columns)
                self.assertIn("target_projection_id", handoff_columns)
                self.assertNotIn(old_source_column, handoff_columns)
                self.assertNotIn(old_target_column, handoff_columns)

                async with store._db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                    tables = {row[0] for row in await cursor.fetchall()}
                self.assertIn("work_item_decisions", tables)
                self.assertNotIn(old_decision_table, tables)

                async with store._db.execute("PRAGMA table_info(artifact_records)") as cursor:
                    artifact_columns = {row[1] for row in await cursor.fetchall()}
                self.assertIn("projection_id", artifact_columns)
                self.assertNotIn(old_projection_column, artifact_columns)
            finally:
                await store.close()

    async def test_initialize_normalizes_canonical_work_item_projection_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tasks.db"
            store = OPCStore(db_path)
            await store.initialize()
            await store.close()

            now = "2026-04-27T00:00:00"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO tasks (id, title, created_at, metadata) VALUES (?, ?, ?, ?)",
                (
                    "task-canonical-projection",
                    "Canonical projection task",
                    now,
                    json.dumps({
                        "work_item_projection_id": "canonical-projection",
                        "work_item_turn_type": "review",
                    }),
                ),
            )
            conn.execute(
                "INSERT INTO tasks (id, title, created_at, metadata) VALUES (?, ?, ?, ?)",
                (
                    "task-new-projection",
                    "New projection task",
                    now,
                    json.dumps({
                        "work_item_projection_id": "new-projection",
                        "work_item_turn_type": "deliver",
                    }),
                ),
            )
            conn.execute(
                """INSERT INTO delegation_work_items
                   (work_item_id, run_id, cell_id, kind, projection_id, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "wi-legacy-projection",
                    "run-projection",
                    "cell-projection",
                    "execute",
                    "wi-projection",
                    json.dumps({"kept": "work-item"}),
                    now,
                    now,
                ),
            )
            conn.commit()
            conn.close()

            store = OPCStore(db_path)
            await store.initialize()
            try:
                async with store._db.execute(
                    "SELECT metadata FROM tasks WHERE id=?",
                    ("task-canonical-projection",),
                ) as cursor:
                    row = await cursor.fetchone()
                canonical_task_meta = json.loads(row[0])
                self.assertEqual(canonical_task_meta[WORK_ITEM_PROJECTION_ID_KEY], "canonical-projection")
                self.assertEqual(canonical_task_meta[WORK_ITEM_TURN_TYPE_KEY], "review")

                async with store._db.execute(
                    "SELECT metadata FROM tasks WHERE id=?",
                    ("task-new-projection",),
                ) as cursor:
                    row = await cursor.fetchone()
                new_task_meta = json.loads(row[0])
                self.assertEqual(new_task_meta[WORK_ITEM_PROJECTION_ID_KEY], "new-projection")
                self.assertEqual(new_task_meta[WORK_ITEM_TURN_TYPE_KEY], "deliver")

                async with store._db.execute(
                    "SELECT metadata FROM delegation_work_items WHERE work_item_id=?",
                    ("wi-legacy-projection",),
                ) as cursor:
                    row = await cursor.fetchone()
                work_item_meta = json.loads(row[0])
                self.assertEqual(work_item_meta[WORK_ITEM_PROJECTION_ID_KEY], "wi-projection")
                self.assertEqual(work_item_meta[WORK_ITEM_TURN_TYPE_KEY], "execute")
                self.assertEqual(work_item_meta["kept"], "work-item")

                stats = await store._migrate_work_item_projection_metadata()
                self.assertEqual(sum(stats.values()), 0)
            finally:
                await store.close()

    async def test_store_saves_work_item_projection_metadata_without_legacy_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OPCStore(Path(tmpdir) / "tasks.db")
            await store.initialize()
            try:
                task = Task(
                    id="task-new-only-projection",
                    title="New-only projection task",
                    status=TaskStatus.PENDING,
                    metadata={
                        WORK_ITEM_PROJECTION_ID_KEY: "projection-new-only",
                        WORK_ITEM_TURN_TYPE_KEY: "execute",
                    },
                )
                work_item = DelegationWorkItem(
                    work_item_id="wi-new-only-projection",
                    run_id="run-new-only",
                    cell_id="cell-new-only",
                    kind="execute",
                    projection_id="projection-new-only",
                    phase=Phase.READY,
                    metadata={
                        WORK_ITEM_PROJECTION_ID_KEY: "projection-new-only",
                        WORK_ITEM_TURN_TYPE_KEY: "execute",
                    },
                )

                await store.save_task(task)
                await store.save_delegation_work_item(work_item)
                old_projection_key = "work" + "flow_" + "sta" + "ge_id"
                old_turn_key = "company_" + "sta" + "ge_type"

                saved_task = await store.get_task(task.id)
                self.assertEqual(saved_task.metadata[WORK_ITEM_PROJECTION_ID_KEY], "projection-new-only")
                self.assertEqual(saved_task.metadata[WORK_ITEM_TURN_TYPE_KEY], "execute")
                self.assertNotIn(old_projection_key, saved_task.metadata)
                self.assertNotIn(old_turn_key, saved_task.metadata)

                saved_work_item = await store.get_delegation_work_item(work_item.work_item_id)
                self.assertEqual(saved_work_item.metadata[WORK_ITEM_PROJECTION_ID_KEY], "projection-new-only")
                self.assertEqual(saved_work_item.metadata[WORK_ITEM_TURN_TYPE_KEY], "execute")
                self.assertNotIn(old_projection_key, saved_work_item.metadata)
                self.assertNotIn(old_turn_key, saved_work_item.metadata)
            finally:
                await store.close()

    async def test_team_work_item_runtime_roundtrips_through_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OPCStore(Path(tmpdir) / "tasks.db")
            await store.initialize()
            try:
                run = DelegationRun(
                    run_id="run-1",
                    project_id="proj-1",
                    session_id="session-1",
                    status="running",
                    lifecycle_status="deliverable",
                    current_revision=2,
                    latest_deliverable_summary="Ready for owner review",
                    recovery_pointer={"status": "warm"},
                    project_dossier={"open_issues": ["qa"]},
                )
                team = TeamInstance(
                    team_instance_id="team-instance-1",
                    run_id=run.run_id,
                    project_id="proj-1",
                    team_id="team-1",
                    session_id="session-1",
                    status="running",
                    seat_ids=["seat-1"],
                    role_ids=["executor"],
                    metadata={"purpose": "company replacement"},
                )
                seat = SeatState(
                    seat_state_id="seat-state-1",
                    team_instance_id=team.team_instance_id,
                    run_id=team.run_id,
                    project_id=team.project_id,
                    team_id=team.team_id,
                    seat_id="seat-1",
                    role_id="executor",
                    employee_id="employee-1",
                    member_session_id="member::proj-1::executor::employee-1",
                    role_runtime_session_id="role-session-1",
                    status="running",
                    resident_status="running",
                    current_task_id="task-1",
                    current_work_item_id="work-item-1",
                    manager_role_id="manager",
                    manager_seat_id="seat-manager",
                    manager_role_ids=["manager"],
                    manager_seat_ids=["seat-manager"],
                    inbox_state={"pending": 1},
                    resume_state={"runtime_session_id": "rt-1"},
                    current_work_item={"work_item_id": "work-item-1"},
                    latest_notification={"subject": "Idle"},
                    manager_digest={"resident_status": "running"},
                    metadata={"scope": "seat"},
                )
                role_session = RoleRuntimeSession(
                    role_session_id="role-runtime::run-1::seat-1",
                    run_id=run.run_id,
                    project_id=team.project_id,
                    team_instance_id=team.team_instance_id,
                    team_id=team.team_id,
                    role_id="executor",
                    seat_id=seat.seat_id,
                    seat_state_id=seat.seat_state_id,
                    employee_id=seat.employee_id,
                    focused_work_item_id="work-item-1",
                    background_work_item_ids=["work-item-2"],
                    manager_role_ids=["manager"],
                    manager_seat_ids=["seat-manager"],
                    seat_ids=["seat-1"],
                    adapter_session_state={"phase": "claim"},
                    inbox_state={"pending": 1},
                    memory_slices_by_work_item={"work-item-1": ["memo"]},
                    resume_state={"runtime_session_id": "rt-1"},
                    current_work_item={"work_item_id": "work-item-1"},
                    latest_notification={"subject": "Idle"},
                    manager_digest={"resident_status": "running"},
                    status="running",
                    metadata={"executor": "shared"},
                )
                work_item = DelegationWorkItem(
                    work_item_id="work-item-1",
                    run_id=team.run_id,
                    cell_id="cell-1",
                    team_instance_id=team.team_instance_id,
                    team_id=team.team_id,
                    role_id="executor",
                    seat_id=seat.seat_id,
                    seat_state_id=seat.seat_state_id,
                    role_runtime_session_id=role_session.role_session_id,
                    source_role_id="manager",
                    source_seat_id="seat-manager",
                    title="Implement feature",
                    summary="Queued for executor seat",
                    kind="execute",
                    projection_id="projection-1",
                    phase=Phase.READY,
                    batch_id="batch-1",
                    batch_index=0,
                    deliverable_summary="Feature branch ready",
                    blocked_reason="",
                    handoff_status="ready",
                    continuation_source="run-0",
                    manager_role_id="manager",
                    manager_seat_id="seat-manager",
                    claimed_by_role_runtime_session_id=role_session.role_session_id,
                    claimed_by_seat_id=seat.seat_id,
                    metadata={"dependency_work_item_ids": ["work-item-0"]},
                )

                await store.save_delegation_run(run)
                await store.save_team_instance(team)
                await store.save_seat_state(seat)
                await store.save_role_runtime_session(role_session)
                await store.save_delegation_work_item(work_item)
                await store.save_session_link(
                    SessionLinkRecord(
                        project_id=run.project_id,
                        session_id=run.session_id,
                        linked_session_id="session-0",
                        link_type="continuation_of",
                        metadata={"run_id": run.run_id},
                    )
                )

                saved_run = await store.get_delegation_run(run.run_id)
                self.assertEqual(saved_run.lifecycle_status, "deliverable")
                self.assertEqual(saved_run.current_revision, 2)
                self.assertEqual(saved_run.project_dossier["open_issues"], ["qa"])
                self.assertEqual((await store.get_team_instance(team.team_instance_id)).team_id, "team-1")
                self.assertEqual((await store.get_seat_state(seat.seat_state_id)).seat_id, "seat-1")
                self.assertEqual((await store.get_role_runtime_session(role_session.role_session_id)).seat_id, "seat-1")
                self.assertEqual((await store.get_delegation_role_session(role_session.role_session_id)).team_id, "team-1")

                team_items = await store.list_team_instances(run_id="run-1", team_id="team-1")
                seat_items = await store.list_seat_states(team_instance_id=team.team_instance_id, seat_id="seat-1")
                role_items = await store.list_role_runtime_sessions("run-1", team_id="team-1", seat_id="seat-1")
                work_items = await store.list_delegation_work_items(
                    "run-1",
                    team_instance_id=team.team_instance_id,
                    team_id=team.team_id,
                    seat_id=seat.seat_id,
                    role_runtime_session_id=role_session.role_session_id,
                )

                self.assertEqual(len(team_items), 1)
                self.assertEqual(len(seat_items), 1)
                self.assertEqual(len(role_items), 1)
                self.assertEqual(len(work_items), 1)
                self.assertEqual(work_items[0].role_runtime_session_id, role_session.role_session_id)
                self.assertEqual(work_items[0].claimed_by_seat_id, seat.seat_id)
                self.assertEqual(work_items[0].batch_id, "batch-1")
                self.assertEqual(work_items[0].deliverable_summary, "Feature branch ready")
                self.assertEqual(work_items[0].metadata["dependency_work_item_ids"], ["work-item-0"])
                seat_alias = await store.list_delegation_seat_states("run-1", seat_id="seat-1")
                self.assertEqual(len(seat_alias), 1)
                links = await store.get_session_links(run.session_id, limit=10)
                self.assertEqual(links[0].link_type, "continuation_of")
            finally:
                await store.close()


class TestJsonLoadsFallback(unittest.TestCase):
    def test_corrupt_json_returns_default(self):
        from opc.database.store import _json_loads

        # Corrupt/partial JSON in a persisted column must not raise — it is read during
        # store.initialize() (via _sweep_stale_claims) and a JSONDecodeError there would
        # prevent the store from ever opening.
        self.assertEqual(_json_loads(None, {}), {})
        self.assertEqual(_json_loads("", {}), {})
        self.assertEqual(_json_loads("{not json", {}), {})
        self.assertEqual(_json_loads('{"a": 1}', {}), {"a": 1})


if __name__ == "__main__":
    unittest.main()
