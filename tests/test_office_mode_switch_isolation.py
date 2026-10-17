from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from opc.core.company_controller import CompanyRunControllerLeaseLost
from opc.core.config import OPCConfig, write_company_org_payload
from opc.core.models import DelegationRun, DelegationWorkItem, Task, TaskStatus
from opc.database.store import OPCStore
from opc.plugins.office_ui import ws_handler
from opc.plugins.office_ui.services.session import SessionService


def test_mode_switch_does_not_reassign_company_tasks_or_prune_their_channels(tmp_path, monkeypatch):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        try:
            task = Task(id="old-research", title="Research", project_id="project02", assigned_to="research_lead",
                        status=TaskStatus.RUNNING, metadata={"execution_mode": "company_mode"})
            await store.save_task(task)
            await store.save_delegation_run(DelegationRun(run_id="old-run", project_id="project02", session_id="old-session"))
            await store.save_delegation_work_item(DelegationWorkItem(work_item_id="old-item", run_id="old-run", role_id="research_lead"))
            assert await store.link_work_item_runtime_task("old-item", task.id)
            await store.acquire_delegation_run_controller_lease("old-run", project_id="project02", root_session_id="old-session",
                                                               owner_token="existing-controller", lease_seconds=60)
            before = copy.deepcopy(task)
            # Demonstrate that the previous cleanup would hit the real Store fence.
            with pytest.raises(CompanyRunControllerLeaseLost):
                await store.save_task(task)

            handler = object.__new__(ws_handler.WSHandler)
            handler.engine = SimpleNamespace(store=store, project_id="project02", org_engine=SimpleNamespace())
            handler.agent_store = SimpleNamespace(load_preset=AsyncMock(), get_all=AsyncMock(return_value=[{"agent_id": "cto", "opc_role_id": "cto"}]))
            handler.chat_store = SimpleNamespace(prune_stale_channels=AsyncMock(), ensure_activity_channel=AsyncMock())
            handler.event_adapter = SimpleNamespace()
            handler._exec_mode = "org"
            handler._company_profile = "custom"
            handler._task_preferred_agent = "codex"
            handler._config_lock = asyncio.Lock()
            handler._persist_mode = AsyncMock()
            handler._restore_company_config_into_engine = MagicMock()
            handler._resolve_preset_name = lambda: "corporate"
            handler._prune_stale_agent_store_entries = AsyncMock()
            handler._sync_role_map = AsyncMock()
            handler._broadcast_owner_interaction_baseline = AsyncMock()
            handler._broadcast_org_info = AsyncMock()
            handler.broadcast = AsyncMock()
            monkeypatch.setattr(ws_handler, "build_snapshot", AsyncMock(return_value={}))
            monkeypatch.setattr(ws_handler, "build_collab_sync", AsyncMock(return_value={}))

            assert await handler._apply_mode_switch("company", "corporate", "native")
            after = await store.get_task(task.id)
            assert after.assigned_to == before.assigned_to
            assert after.status == before.status
            assert after.metadata == before.metadata
            handler.chat_store.prune_stale_channels.assert_not_awaited()
        finally:
            await store.close()
    asyncio.run(run())


def test_selecting_a_session_org_validates_without_rebinding_the_shared_engine(tmp_path):
    from opc.layer2_organization.custom_runtime import CustomRuntimeRunner

    config = OPCConfig()
    engine = SimpleNamespace(config=config, opc_home=tmp_path)
    payload = {
        "organization_id": "research_team", "organization_name": "Research", "company_profile": "custom",
        "roles": [{"id": "lead", "name": "Research Lead", "responsibility": "Research and integrate", "reports_to": "owner"}],
        "final_decider_role_id": "lead",
    }
    write_company_org_payload(tmp_path / "config", "research_team", payload)
    context = SimpleNamespace(load_active_org_config=MagicMock(), set_active_saved_org_name=AsyncMock())
    before = config.model_dump()
    service = SessionService(context)
    service._validate_session_org("research_team", engine)
    assert engine.config is config
    assert config.model_dump() == before
    context.load_active_org_config.assert_not_called()
    context.set_active_saved_org_name.assert_not_awaited()
    scoped_config, org_id = CustomRuntimeRunner(engine)._build_org_config("research_team")
    assert org_id == "research_team"
    assert scoped_config.org.roles[0].id == "lead"
