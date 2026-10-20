from __future__ import annotations

import asyncio
from datetime import timedelta

from opc.core.models import ExecutionCheckpoint, Task, TaskStatus
from opc.database.store import OPCStore


def test_checkpoint_compare_and_set_has_one_winner_across_store_connections(
    tmp_path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "tasks.db"
        first_store = OPCStore(db_path)
        second_store = OPCStore(db_path)
        await first_store.initialize()
        await second_store.initialize()
        try:
            checkpoint = ExecutionCheckpoint(
                checkpoint_id="checkpoint-1",
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_type="company_runtime_interrupted",
                status="pending",
                task_id="runtime-task",
                payload={"reason": "service_restart"},
            )
            await first_store.save_execution_checkpoint(checkpoint)

            start = asyncio.Event()
            payloads = [
                {"reason": "service_restart", "claimed_by": "office"},
                {"reason": "service_restart", "claimed_by": "cli"},
            ]

            async def claim(store: OPCStore, payload: dict[str, str]) -> bool:
                await start.wait()
                return await store.compare_and_set_execution_checkpoint(
                    checkpoint.checkpoint_id,
                    expected_statuses={"pending"},
                    status="resuming",
                    payload=payload,
                )

            claims = [
                asyncio.create_task(claim(first_store, payloads[0])),
                asyncio.create_task(claim(second_store, payloads[1])),
            ]
            start.set()
            results = await asyncio.gather(*claims)

            assert results.count(True) == 1
            assert results.count(False) == 1
            winner = results.index(True)
            rows = await first_store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
            )
            assert len(rows) == 1
            assert rows[0].status == "resuming"
            assert rows[0].payload == payloads[winner]
        finally:
            await second_store.close()
            await first_store.close()

    asyncio.run(scenario())


def test_checkpoint_get_or_create_has_one_active_row_across_store_connections(
    tmp_path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "tasks.db"
        first_store = OPCStore(db_path)
        second_store = OPCStore(db_path)
        await first_store.initialize()
        await second_store.initialize()
        try:
            checkpoint_types = {
                "company_runtime_suspended",
                "company_runtime_interrupted",
            }
            candidates = [
                ExecutionCheckpoint(
                    checkpoint_id="checkpoint-office",
                    project_id="project-a",
                    session_id="runtime-session",
                    checkpoint_type="company_runtime_interrupted",
                    task_id="runtime-task",
                    payload={"creator": "office"},
                ),
                ExecutionCheckpoint(
                    checkpoint_id="checkpoint-cli",
                    project_id="project-a",
                    session_id="runtime-session",
                    checkpoint_type="company_runtime_suspended",
                    task_id="runtime-task",
                    payload={"creator": "cli"},
                ),
            ]
            start = asyncio.Event()

            async def create(
                store: OPCStore,
                candidate: ExecutionCheckpoint,
            ) -> tuple[ExecutionCheckpoint, bool]:
                await start.wait()
                return await store.get_or_create_active_execution_checkpoint(
                    candidate,
                    checkpoint_types=checkpoint_types,
                )

            attempts = [
                asyncio.create_task(create(first_store, candidates[0])),
                asyncio.create_task(create(second_store, candidates[1])),
            ]
            start.set()
            results = await asyncio.gather(*attempts)

            assert [created for _, created in results].count(True) == 1
            assert [created for _, created in results].count(False) == 1
            assert len({row.checkpoint_id for row, _ in results}) == 1

            active = await first_store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_types=list(checkpoint_types),
                statuses=["pending", "resuming"],
            )
            assert len(active) == 1
            assert active[0].checkpoint_id == results[0][0].checkpoint_id
        finally:
            await second_store.close()
            await first_store.close()

    asyncio.run(scenario())


def test_checkpoint_get_or_create_normalizes_historical_active_duplicates(
    tmp_path,
) -> None:
    async def scenario() -> None:
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        try:
            older = ExecutionCheckpoint(
                checkpoint_id="checkpoint-older",
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_type="company_runtime_interrupted",
                status="resuming",
                payload={"created": "older"},
            )
            newer = ExecutionCheckpoint(
                checkpoint_id="checkpoint-newer",
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_type="company_runtime_suspended",
                payload={"created": "newer"},
            )
            newer.updated_at = older.updated_at + timedelta(microseconds=1)
            await store.save_execution_checkpoint(older)
            await store.save_execution_checkpoint(newer)

            winner, created = await store.get_or_create_active_execution_checkpoint(
                ExecutionCheckpoint(
                    checkpoint_id="checkpoint-unused",
                    project_id="project-a",
                    session_id="runtime-session",
                    checkpoint_type="company_runtime_interrupted",
                ),
                checkpoint_types={
                    "company_runtime_suspended",
                    "company_runtime_interrupted",
                },
            )

            assert created is False
            assert winner.checkpoint_id == "checkpoint-newer"
            active = await store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
                statuses=["pending", "resuming"],
            )
            assert [row.checkpoint_id for row in active] == ["checkpoint-newer"]
            all_rows = await store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
            )
            by_id = {row.checkpoint_id: row for row in all_rows}
            assert by_id["checkpoint-older"].status == "superseded"
            assert (
                by_id["checkpoint-older"].payload["superseded_by_checkpoint_id"]
                == "checkpoint-newer"
            )
        finally:
            await store.close()

    asyncio.run(scenario())


def test_checkpoint_completion_and_cancelled_anchor_reopen_are_atomic(tmp_path) -> None:
    async def scenario() -> None:
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        try:
            anchor = Task(
                id="ui-anchor",
                project_id="project-a",
                session_id="runtime-session",
                title="Company chat",
                status=TaskStatus.CANCELLED,
                execution_lock=True,
            )
            checkpoint = ExecutionCheckpoint(
                checkpoint_id="checkpoint-1",
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_type="company_runtime_interrupted",
                status="resuming",
                payload={"ui_anchor_task_id": anchor.id},
            )
            await store.save_task(anchor)
            await store.save_execution_checkpoint(checkpoint)

            completed = await store.complete_execution_checkpoint_and_reopen_ui_anchor(
                checkpoint.checkpoint_id,
                project_id="project-a",
                session_id="runtime-session",
                expected_status="resuming",
                status="resolved",
                payload={"resume_state": "handoff_complete"},
                ui_anchor_task_id=anchor.id,
            )

            assert completed is True
            assert (await store.get_task(anchor.id)).status == TaskStatus.IDLE
            rows = await store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
            )
            assert rows[0].status == "resolved"
            assert rows[0].payload == {"resume_state": "handoff_complete"}
        finally:
            await store.close()

    asyncio.run(scenario())


def test_checkpoint_completion_does_not_reopen_anchor_after_stop_wins(tmp_path) -> None:
    async def scenario() -> None:
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        try:
            anchor = Task(
                id="ui-anchor",
                project_id="project-a",
                session_id="runtime-session",
                title="Company chat",
                status=TaskStatus.CANCELLED,
            )
            checkpoint = ExecutionCheckpoint(
                checkpoint_id="checkpoint-1",
                project_id="project-a",
                session_id="runtime-session",
                checkpoint_type="company_runtime_interrupted",
                status="pending",
                payload={"resume_state": "interrupted"},
            )
            await store.save_task(anchor)
            await store.save_execution_checkpoint(checkpoint)

            completed = await store.complete_execution_checkpoint_and_reopen_ui_anchor(
                checkpoint.checkpoint_id,
                project_id="project-a",
                session_id="runtime-session",
                expected_status="resuming",
                status="resolved",
                payload={"resume_state": "handoff_complete"},
                ui_anchor_task_id=anchor.id,
            )

            assert completed is False
            assert (await store.get_task(anchor.id)).status == TaskStatus.CANCELLED
            rows = await store.get_execution_checkpoints(
                project_id="project-a",
                session_id="runtime-session",
            )
            assert rows[0].status == "pending"
            assert rows[0].payload == {"resume_state": "interrupted"}
        finally:
            await store.close()

    asyncio.run(scenario())
