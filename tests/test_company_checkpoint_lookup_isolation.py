from __future__ import annotations

import asyncio

import pytest

from opc.core.company_controller import CompanyRunControllerLeaseLost
from opc.core.models import DelegationRun, DelegationWorkItem, ExecutionCheckpoint, Task
from opc.database.store import OPCStore
from opc.engine import OPCEngine


@pytest.mark.parametrize("checkpoint_type", ["company_runtime_suspended", "company_runtime_interrupted"])
def test_company_checkpoint_lookup_preserves_company_scope_and_fenced_task(tmp_path, checkpoint_type):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        task = Task(id="fenced-lead", project_id="fresh_project", session_id="root-session",
                    parent_session_id="root-session", linked_work_item_id="root-work-item",
                    metadata={"exec_mode": "org", "company_profile": "custom", "org_id": "adaptive_team",
                              "shared_role_session": True, "shared_role_id": "lead", "delegation_run_id": "run"})
        try:
            await store.save_delegation_run(DelegationRun(run_id="run", project_id=task.project_id, session_id="root-session"))
            await store.save_delegation_work_item(DelegationWorkItem(work_item_id="root-work-item", run_id="run", role_id="lead"))
            await store.save_task(task)
            assert await store.link_work_item_runtime_task("root-work-item", task.id)
            checkpoint = ExecutionCheckpoint(
                checkpoint_id="company-checkpoint", project_id=task.project_id, session_id="root-session",
                checkpoint_type=checkpoint_type, task_id=task.id,
                payload={"parent_session_id": "root-session", "run_id": "run", "org_id": "adaptive_team"},
            )
            await store.save_execution_checkpoint(checkpoint)
            await store.acquire_delegation_run_controller_lease(
                "run", project_id=task.project_id, root_session_id="root-session", owner_token="other-controller", lease_seconds=60,
            )
            # Prove the old migration's broad save would cross a real fence.
            with pytest.raises(CompanyRunControllerLeaseLost):
                await store.save_task(task)
            before = await store.get_task(task.id)
            engine = OPCEngine(opc_home=tmp_path, project_id=task.project_id, store=store, owns_store=False)
            selected = await engine.get_latest_pending_checkpoint_for_session("root-session")
            assert selected.checkpoint_id == checkpoint.checkpoint_id
            assert selected.payload == checkpoint.payload
            assert "runtime_v2" not in selected.payload
            assert await store.get_task(task.id) == before
            persisted = await store.get_execution_checkpoint(checkpoint.checkpoint_id, project_id=task.project_id)
            assert persisted.payload == checkpoint.payload
        finally:
            await store.close()
    asyncio.run(run())
