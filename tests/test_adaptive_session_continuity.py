from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from opc.core.config import OPCConfig, write_company_org_payload
from opc.core.models import ExecutionMode, RouterDecision, SessionRecord, Task
from opc.database.store import OPCStore, _SQLiteConnectionAdapter
from opc.engine import OPCEngine
from opc.layer2_organization.adaptive_team import AdaptiveTeamPlan
from opc.layer2_organization.company_mode import CompanyRuntimeSpec
from opc.layer2_organization.custom_runtime import CustomRuntimeRunner
from opc.layer3_agent.adapters.codex_adapter import CodexAdapter
from opc.plugins.office_ui.services.context import OfficeServiceContext
from opc.plugins.office_ui.services.models import ServiceError
from opc.plugins.office_ui.services.session import SessionService
from opc.plugins.office_ui.chat_store import ChatStore
from opc.plugins.office_ui.event_adapter import EventAdapter
from opc.plugins.office_ui.ws_handler import WSHandler
from tests.test_adaptive_team import Planner, make_engine, spec, team_payload


@pytest.mark.parametrize("agent", ["native", "codex"])
def test_approved_adaptive_team_survives_second_third_turn_and_store_reopen(tmp_path, monkeypatch, agent):
    async def run():
        engine = make_engine(tmp_path, Planner(team_payload(2)))
        for config in engine.config.agents.agents.values():
            config.enabled = False
        if agent == "codex":
            engine.config.agents.agents["codex"].enabled = True
            monkeypatch.setattr(CodexAdapter, "is_available", AsyncMock(return_value=True))
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine.store = store
        anchor = Task(id="adaptive-chat", project_id=engine.project_id, session_id="adaptive-session",
                      metadata={"exec_mode": "company", "company_profile": "corporate", "preferred_agent": agent,
                                "company_team_strategy": "adaptive"})
        await store.save_task(anchor)
        await store.save_session(SessionRecord(session_id=anchor.session_id, project_id=engine.project_id, metadata=dict(anchor.metadata)))
        monkeypatch.setattr(OPCEngine, "_execute_company_mode", AsyncMock(return_value="First delivery"))
        monkeypatch.setattr(OPCEngine, "_resolve_workspace_contract", AsyncMock(return_value={
            "workspace_root": str(tmp_path / "work"), "output_root": str(tmp_path / "work" / "output"),
            "comms_workspace_root": str(tmp_path / "work"), "comms_root": str(tmp_path / "comms"),
        }))
        try:
            plan = await engine.company_recruiter.build_team_recruitment_plan(spec(), project_id=engine.project_id)
            plan.metadata["adaptive_organization_id"] = "adaptive_continuity"
            decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate", preferred_agent=agent,
                                      metadata={"company_team_strategy": "adaptive"})
            await CustomRuntimeRunner(engine).execute_adaptive_team(
                plan, decision, spec(), "Initial research", session_id=anchor.session_id, origin_task_id=anchor.id,
                role_agent_overrides={"role_0": agent, "role_1": agent},
            )
            persisted = await store.get_task(anchor.id)
            assert persisted.org_id == "adaptive_continuity"
            assert persisted.metadata["org_id"] == persisted.metadata["organization_id"] == persisted.org_id
            assert persisted.metadata["exec_mode"] == "org"
            session = await store.get_session(anchor.session_id)
            assert session.metadata["org_id"] == session.metadata["execution_defaults"]["org_id"] == persisted.org_id
            assert session.metadata["recruitment_confirmation_completed"] is True
            assert engine.config.org.company_profile == "corporate"
        finally:
            await store.close()

        # Reconstruct both Store and Engine; no in-memory org mapping survives.
        reopened = OPCStore(tmp_path / "tasks.db")
        await reopened.initialize()
        config, _ = CustomRuntimeRunner(engine)._build_org_config("adaptive_continuity")
        resumed = OPCEngine(config=config, opc_home=tmp_path, project_id=engine.project_id, store=reopened, owns_store=False)
        resumed._begin_company_recruitment_loop = AsyncMock(side_effect=AssertionError("Do not recruit the approved team again"))
        resumed._continue_company_mode_execution = AsyncMock(return_value="Follow-up delivered")
        service = SessionService(OfficeServiceContext(engine=resumed, agent_store=None, chat_store=None, event_adapter=None))
        try:
            for request in ("What about stocks in 2027?", "Explain the downside scenario."):
                persisted = await reopened.get_task(anchor.id)
                identity = service.resolve_task_identity(persisted)
                assert identity.org_id == "adaptive_continuity" and identity.preferred_agent == agent
                decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="custom", org_id=identity.org_id,
                                          preferred_agent=identity.preferred_agent, metadata={"company_team_strategy": "adaptive"})
                result = await resumed._begin_company_staffing_loop(
                    decision, request, CompanyRuntimeSpec(profile="custom"), session_id=anchor.session_id,
                    origin_task_id=anchor.id, origin_channel="cli", origin_chat_id="", origin_thread_id="",
                )
                assert result == "Follow-up delivered"
                assert set(resumed._continue_company_mode_execution.await_args.kwargs["role_agent_overrides"].values()) == {agent}
            assert resumed._continue_company_mode_execution.await_count == 2
            resumed._begin_company_recruitment_loop.assert_not_awaited()
        finally:
            await reopened.close()
    asyncio.run(run())


def save_adaptive_org(engine, org_id="adaptive_previous", project_id="project02"):
    payload = AdaptiveTeamPlan.model_validate(team_payload(2)).org_payload(engine.config, organization_id=org_id)
    payload["metadata"]["adaptive_source_project_id"] = project_id
    write_company_org_payload(engine.opc_home / "config", org_id, payload)


@pytest.mark.parametrize("damage", [None, "other_session", "other_project", "org_from_other_project", "conflicting_orgs"])
def test_legacy_adaptive_org_recovery_is_scoped_and_persistent(tmp_path, damage):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine = OPCEngine(config=OPCConfig(), opc_home=tmp_path, project_id="project02", store=store, owns_store=False)
        context = OfficeServiceContext(engine=engine, agent_store=None, chat_store=None, event_adapter=None)
        context.get_active_saved_org_name = AsyncMock(return_value="unrelated_company")
        service = SessionService(context)
        anchor = Task(id="legacy-adaptive-chat", project_id="project02", session_id="legacy-session",
                      metadata={"exec_mode": "org", "company_profile": "custom", "preferred_agent": "codex",
                                "company_team_strategy": "adaptive", "execution_mode": "company_mode"})
        child = Task(id="legacy-lead", project_id="project99" if damage == "other_project" else "project02",
                     session_id="legacy-lead-session", parent_session_id="another-session" if damage == "other_session" else anchor.session_id,
                     org_id="adaptive_previous", metadata={"execution_mode": "company_mode", "company_profile": "custom",
                                                          "org_id": "adaptive_previous", "origin_task_id": anchor.id})
        save_adaptive_org(engine, project_id="project99" if damage == "org_from_other_project" else "project02")
        await store.save_session(SessionRecord(session_id=anchor.session_id, project_id="project02", metadata={}))
        await store.save_task(anchor)
        await store.save_task(child)
        if damage == "conflicting_orgs":
            await store.save_task(Task(id="conflicting-lead", project_id="project02", session_id="conflicting-session",
                                       parent_session_id=anchor.session_id, org_id="another_org",
                                       metadata={"execution_mode": "company_mode", "company_profile": "custom"}))
        try:
            if damage in {"org_from_other_project", "conflicting_orgs"}:
                with pytest.raises(ServiceError) as error:
                    await service._resolve_company_runtime_target(engine, anchor)
                assert error.value.code == "adaptive_org_identity_conflict"
            else:
                if damage is None:
                    # Exercise the exact WebSocket second-turn ingress that
                    # previously rejected the legacy anchor before dispatch.
                    chat = ChatStore(_SQLiteConnectionAdapter(":memory:"))
                    await chat.initialize()
                    await chat.create_session_channel(anchor.id, "Adaptive chat", project_id="project02")
                    handler = WSHandler(engine, MagicMock(), chat, EventAdapter())
                    handler.broadcast = AsyncMock()
                    engine.process_message = AsyncMock(return_value="Follow-up accepted")
                    try:
                        await handler._process_session_message(anchor.id, "What about stocks in 2027?", session_id=anchor.session_id)
                        engine.process_message.assert_awaited_once()
                        dispatched = engine.process_message.await_args.kwargs
                        assert dispatched["org_id"] == "adaptive_previous"
                        assert dispatched["preferred_agent"] == "codex"
                        assert dispatched["session_id"] == anchor.session_id
                    finally:
                        await chat._db.close()
                target = await service._resolve_company_runtime_target(engine, anchor)
                recovered = service.resolve_task_identity(target["config_task"])
                assert recovered.org_id == ("adaptive_previous" if damage is None else "")
            persisted = await store.get_task(anchor.id)
            if damage is None:
                assert persisted.org_id == "adaptive_previous"
                assert persisted.metadata["preferred_agent"] == "codex"
                assert persisted.metadata["company_team_strategy"] == "adaptive"
                session = await store.get_session(anchor.session_id)
                assert session.metadata["org_id"] == "adaptive_previous"
                assert session.metadata["recruitment_confirmation_completed"] is True
            else:
                assert persisted.org_id is None
                assert "org_id" not in persisted.metadata
            assert engine.config.org.company_profile == "corporate"
            context.get_active_saved_org_name.assert_not_awaited()
        finally:
            await store.close()
    asyncio.run(run())
