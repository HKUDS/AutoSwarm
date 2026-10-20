"""Fresh project/chat creation through approval, follow-ups and restart."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from opc.core.config import OPCConfig
from opc.core.models import Phase, TaskResult, TaskStatus
from opc.database.store import _SQLiteConnectionAdapter
from opc.engine import OPCEngine
from opc.layer3_agent.adapters.codex_adapter import CodexAdapter
from opc.llm.provider import LLMProvider
from opc.plugins.office_ui.chat_store import ChatStore
from opc.plugins.office_ui.event_adapter import EventAdapter
from opc.plugins.office_ui.ws_handler import WSHandler
from tests.test_adaptive_team import team_payload


@pytest.mark.parametrize("first_agent", ["native", "codex"])
def test_fresh_projects_and_chats_persist_their_own_approved_team(tmp_path, monkeypatch, first_agent):
    planning_calls = []
    execution_calls = []
    project_policies = {}

    async def native_plan(self, prompt, system=None, task_type=None):
        assert task_type == "team_formation", task_type
        planning_calls.append(("native", json.loads(prompt)["project_id"]))
        return json.dumps(team_payload(2))

    async def codex_plan(self, prompt, workspace_path):
        payload = json.loads(prompt.split("PAYLOAD:\n", 1)[1])
        planning_calls.append(("codex", payload["project_id"]))
        return TaskResult(status=TaskStatus.DONE, content=json.dumps(team_payload(2)))

    async def execute(runtime, tasks, runtime_plan):
        assert runtime.approval_engine.native_policy is project_policies[runtime.project_id]
        execution_calls.append((runtime.project_id, runtime.config.org.organization_id,
                                {task.metadata.get("selected_execution_agent") for task in tasks}))
        for task in tasks:
            task.status = TaskStatus.DONE
            task.result = TaskResult(status=TaskStatus.DONE, content="Fixture delivery completed.")
            task.metadata.update(self_evolution_review_completed=True, feedback_scope="final")
            await runtime.store.save_task(task)
        # The executor itself is replaced, so populate its terminal fixture
        # rows too. Leaving its WorkItems READY would simulate an interrupted
        # run instead of the completed delivery this test is exercising.
        for run_id in {task.metadata["delegation_run_id"] for task in tasks}:
            await runtime.store._db.execute(
                "UPDATE delegation_work_items SET phase=? WHERE run_id=?", (Phase.APPROVED.value, run_id),
            )
            await runtime.store._db.commit()
        return "Fixture delivery completed."

    async def workspace(runtime, message, session_id):
        root = tmp_path / "workplaces" / runtime.project_id
        return {"workspace_root": str(root), "output_root": str(root / session_id),
                "comms_workspace_root": str(root), "comms_root": str(root / ".opc-comms")}

    monkeypatch.setattr("opc.core.config.get_default_workplace_root", lambda: tmp_path / "workplaces")
    monkeypatch.setattr(LLMProvider, "simple_chat", native_plan)
    monkeypatch.setattr(LLMProvider, "chat", AsyncMock(side_effect=AssertionError("Unexpected model API call")))
    monkeypatch.setattr(CodexAdapter, "is_available", AsyncMock(return_value=True))
    monkeypatch.setattr(CodexAdapter, "plan_json", codex_plan)
    monkeypatch.setattr(OPCEngine, "_execute_company_mode", execute)
    monkeypatch.setattr(OPCEngine, "_resolve_workspace_contract", workspace)
    monkeypatch.setattr("opc.plugins.office_ui.services.session.build_collab_sync", AsyncMock(return_value={}))

    async def make_app(config):
        engine = OPCEngine(config=config.model_copy(deep=True), opc_home=tmp_path / "state", project_id="default")
        await engine.initialize()
        chat = ChatStore(_SQLiteConnectionAdapter(str(tmp_path / "ui.db")))
        await chat.initialize()
        handler = WSHandler(engine, MagicMock(), chat, EventAdapter())
        handler.broadcast = AsyncMock()
        handler._send_ack = AsyncMock()
        handler.services_context.project_workplace_hook = lambda project: tmp_path / "workplaces" / project
        return engine, chat, handler

    async def send(handler, engine, task_id, session_id, text):
        await handler._process_session_message(task_id, text, session_id=session_id,
                                               run_engine=engine, run_project_id=engine.project_id)
        errors = [call.args[0]["payload"].get("content", "") for call in handler.broadcast.await_args_list
                  if call.args[0].get("type") == "session_message"
                  and call.args[0]["payload"].get("sender") == "system"]
        assert not any(content.startswith("Error:") for content in errors), errors

    async def assert_detail_agent(handler, engine, task_id, agent):
        await handler._handle_session_detail(MagicMock(), {
            "project_id": engine.project_id, "task_id": task_id, "detail_level": "summary",
        })
        payload = handler._send_ack.await_args.kwargs
        assert payload["ok"] is True
        detail = payload["session_state"]
        assert detail["preferred_agent"] == detail["selected_execution_agent"] == agent
        assert detail["recruitment_agent"] == agent
        assert detail["company_team_strategy"] == "adaptive"

    async def run():
        config = OPCConfig()
        for agent in config.agents.agents.values():
            agent.enabled = False
        config.agents.agents["codex"].enabled = True
        config.save(tmp_path / "state" / "config")
        root, chat, handler = await make_app(config)
        scenarios = []
        second_agent = "codex" if first_agent == "native" else "native"
        try:
            for project in ("fresh_alpha", "fresh_beta"):
                await handler.services.project.create(project)
                for selected_agent in (first_agent, second_agent):
                    # A stale global selection must not become the new chat's
                    # org, approval state or execution-agent preference.
                    handler.services_context.mode_state.exec_mode = "org"
                    handler.services_context.mode_state.company_profile = "custom"
                    handler.services_context.mode_state.task_preferred_agent = second_agent
                    handler.services_context.get_active_saved_org_name = AsyncMock(return_value="stale_global_org")
                    created = await handler.services.session.create(
                        project_id=project, exec_mode="company", company_profile="corporate",
                        preferred_agent=selected_agent, company_team_strategy="adaptive", org_id="stale_global_org",
                    )
                    task_id, session_id = created.payload["task_id"], created.payload["session_id"]
                    engine = await handler.services_context.engine_for_project(project)
                    project_policies[project] = engine.approval_engine.native_policy
                    anchor = await engine.store.get_task(task_id)
                    assert anchor.org_id is None and "org_id" not in anchor.metadata
                    assert not await engine._session_has_completed_recruitment_confirmation(session_id)
                    assert created.payload["org_id"] == ""
                    await assert_detail_agent(handler, engine, task_id, selected_agent)
                    before_calls = len(planning_calls)
                    await send(handler, engine, task_id, session_id, "组建两人团队，调研未来一年的AI趋势与泡沫风险。")
                    checkpoint = await engine.store.get_latest_pending_checkpoint(project, session_id)
                    assert checkpoint.checkpoint_type == "company_recruitment_confirmation"
                    assert planning_calls[before_calls:] == [(selected_agent, project)]
                    proposed_org = checkpoint.payload["recruitment_plan"]["metadata"]["adaptive_organization_id"]
                    await send(handler, engine, task_id, session_id, "approve")
                    for message in ("请继续比较上行情景。", "请补充下行情景。"):
                        await send(handler, engine, task_id, session_id, message)
                        await assert_detail_agent(handler, engine, task_id, selected_agent)
                    anchor = await engine.store.get_task(task_id)
                    record = await engine.store.get_session(session_id)
                    assert anchor.org_id == anchor.metadata["org_id"] == proposed_org
                    assert record.metadata["org_id"] == record.metadata["execution_defaults"]["org_id"] == proposed_org
                    assert record.metadata["recruitment_confirmation_completed"] is True
                    assert anchor.metadata["preferred_agent"] == selected_agent
                    assert len(planning_calls) == before_calls + 1
                    assert execution_calls[-3:] == [(project, proposed_org, {selected_agent})] * 3
                    handler.services_context.get_active_saved_org_name.assert_not_awaited()
                    scenarios.append((project, task_id, session_id, proposed_org, selected_agent))
                    for old_project, old_task, _, old_org, old_agent in scenarios:
                        old_engine = await handler.services_context.engine_for_project(old_project)
                        old_anchor = await old_engine.store.get_task(old_task)
                        assert (old_anchor.org_id, old_anchor.metadata["preferred_agent"]) == (old_org, old_agent)
            assert len({item[3] for item in scenarios}) == 4
            assert root.config.org.company_profile == "corporate"
        finally:
            await root.shutdown()
            await chat._db.close()

        root, chat, handler = await make_app(config)
        try:
            for project, task_id, session_id, org_id, selected_agent in scenarios:
                engine = await handler.services_context.engine_for_project(project)
                project_policies[project] = engine.approval_engine.native_policy
                call_count = len(planning_calls)
                await send(handler, engine, task_id, session_id, "重启后继续补充研究结论。")
                await assert_detail_agent(handler, engine, task_id, selected_agent)
                anchor = await engine.store.get_task(task_id)
                assert (anchor.org_id, anchor.metadata["preferred_agent"]) == (org_id, selected_agent)
                assert len(planning_calls) == call_count
                assert execution_calls[-1] == (project, org_id, {selected_agent})
            assert len(planning_calls) == 4
        finally:
            await root.shutdown()
            await chat._db.close()

    asyncio.run(run())
