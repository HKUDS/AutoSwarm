"""User-selected executors survive Company dispatch without provider fallback."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from opc.core.config import ExternalAgentConfig, OPCConfig, RoleConfig
from opc.core.models import ApprovalAction, ApprovalDecision, DelegationWorkItem, ExecutionMode, RiskLevel, RouterDecision, Task, TaskResult, TaskStatus
from opc.database.store import OPCStore
from opc.engine import OPCEngine
from opc.layer2_organization.company_mode import CompanyWorkItemExecutor
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer3_agent.adapters.codex_adapter import CodexAdapter
from opc.layer3_agent.external_broker import ExternalAgentBroker


def company_task(project="project02", agent="codex"):
    return Task(title="Research", project_id=project, assigned_to="research_lead", assigned_external_agent=agent if agent != "native" else None,
                metadata={"execution_mode": "company_mode", "selected_execution_agent": agent,
                          "execution_agent_locked": True, "external_company_execution_allowed": agent != "native",
                          "external_company_execution_fence": "validated_workspace" if agent != "native" else ""})


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_company_launch_keeps_own_model_and_project_workspace(tmp_path, monkeypatch, interactive, resume):
    monkeypatch.setenv("OPC_HOME", str(tmp_path / "shared-opc-home"))
    adapter = CodexAdapter(ExternalAgentConfig(model="codex-selected-model", approval_mode="full-auto",
                                             session_mode="resume" if resume else "new", session_id="project02-thread" if resume else ""))
    task = company_task()
    task.metadata["comms_workspace_root"] = str(tmp_path / "project02-comms")
    build = adapter.build_interactive_invocation if interactive else adapter.build_invocation
    cmd, metadata = build(task, str(tmp_path / "project02-work"))
    assert cmd[cmd.index("--model") + 1] == "codex-selected-model"
    assert 'sandbox_mode="workspace-write"' in cmd
    assert not any("danger" in arg for arg in cmd)
    roots = json.loads(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("sandbox_workspace_write.writable_roots=")))
    assert roots == [str(tmp_path / "project02-comms")]
    assert str(tmp_path / "shared-opc-home") not in cmd
    assert metadata["capabilities"]["company_mode"] is True
    if resume:
        assert cmd[:3] == ["codex", "exec", "resume"]
        assert cmd[-2] == "project02-thread"
    else:
        assert cmd[cmd.index("-C") + 1] == str(tmp_path / "project02-work")


def test_codex_company_cannot_bypass_its_workspace_fence(tmp_path):
    adapter = CodexAdapter(ExternalAgentConfig(extra_args=["--dangerously-bypass-approvals-and-sandbox"]))
    with pytest.raises(RuntimeError, match="workspace fence"):
        adapter.build_invocation(company_task(), str(tmp_path))


def execution_engine(root, *, project="project02", available=True, adapter=None):
    config = OPCConfig()
    config.org.company_profile = "custom"
    config.org.organization_id = "research-team"
    config.org.roles = [RoleConfig(id="research_lead", name="Research Lead", responsibility="Integrate research", reports_to="owner"),
                        RoleConfig(id="researcher", name="Researcher", responsibility="Research AI technology", reports_to="research_lead")]
    engine = OPCEngine(config=config, opc_home=root, project_id=project)
    engine.org_engine = OrgEngine(config, root)
    adapter = adapter or CodexAdapter(ExternalAgentConfig())
    engine.adapter_registry = SimpleNamespace(list_available=lambda: ["codex"] if available else [],
                                             get=lambda name: adapter if name == "codex" else None,
                                             get_ordered_available=lambda: [("codex", adapter)] if available else [])
    engine.store = SimpleNamespace(save_task=AsyncMock())
    return engine


def test_company_child_materialization_keeps_approved_codex_choice(tmp_path):
    async def run():
        engine = execution_engine(tmp_path)
        topology = engine._enrich_runtime_delegation_topology(
            runtime_topology=engine.org_engine.build_runtime_delegation_topology(),
            decision=RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="custom"),
            project_id="project02", role_agent_overrides={"research_lead": "native", "researcher": "codex"})
        seat = next(seat for seat in topology["seats"] if seat["role_id"] == "researcher")
        executor = CompanyWorkItemExecutor(org_engine=engine.org_engine, communication=SimpleNamespace(),
                                          approval_engine=SimpleNamespace(), memory=SimpleNamespace(ensure_session=AsyncMock()),
                                          execute_task=AsyncMock(), save_task=AsyncMock())
        executor.store = SimpleNamespace(get_runtime_task_for_work_item=AsyncMock(return_value=None),
                                        save_delegation_work_item=AsyncMock(), save_task=AsyncMock(),
                                        link_work_item_runtime_task=AsyncMock(return_value=True))
        root = company_task(agent="native")
        root.metadata.update(runtime_topology=topology, force_native_execution=True)
        child = DelegationWorkItem(work_item_id="child", run_id="run", role_id="researcher", seat_id=seat["seat_id"],
                                   title="AI technology research", kind="execute")
        tasks = await executor._materialize_work_item_tasks([root], [child])
        projected = next(task for task in tasks if task.id != root.id)
        assert projected.assigned_external_agent == "codex"
        assert projected.metadata["force_native_execution"] is False
        assert projected.metadata["external_company_execution_fence"] == "validated_workspace"
        assert await engine._assign_task_execution_agent(projected) == "codex"
        assert root.metadata["selected_execution_agent"] == "native"
    asyncio.run(run())


@pytest.mark.parametrize("available,supports", [(False, True), (True, False)])
def test_explicit_company_selection_fails_without_substituting_native(tmp_path, available, supports):
    engine = execution_engine(tmp_path, available=available,
                              adapter=SimpleNamespace(supports_company_execution=lambda: supports, execution_unit_kind=lambda: "external_agent"))
    original = engine.org_engine.build_runtime_delegation_topology()
    before = copy.deepcopy(original)
    with pytest.raises(RuntimeError, match="codex.*No alternate agent was started"):
        engine._enrich_runtime_delegation_topology(runtime_topology=original,
            decision=RouterDecision(mode=ExecutionMode.COMPANY_MODE, preferred_agent="codex"), project_id="project02")
    assert original == before


def test_projects_dispatch_independently_even_with_shared_adapter(tmp_path):
    async def run():
        adapter = CodexAdapter(ExternalAgentConfig())
        first = execution_engine(tmp_path, project="project01", adapter=adapter)
        second = execution_engine(tmp_path, project="project02", adapter=adapter)
        native = company_task("project01", "native")
        codex = company_task("project02", "codex")
        first._run_native_agent = AsyncMock(return_value=TaskResult(status=TaskStatus.DONE, content="project01 Native"))
        second._run_native_agent = AsyncMock(side_effect=AssertionError("project02 Codex must not invoke Native/GLM"))
        second._build_external_agent_task = AsyncMock(side_effect=lambda task: task)
        second._resolve_external_workspace = lambda task: str(tmp_path)
        second.external_broker = SimpleNamespace(run=AsyncMock(return_value=TaskResult(status=TaskStatus.DONE, content="project02 Codex")))
        before = adapter.config.model_dump()
        await asyncio.gather(first._assign_task_execution_agent(native), second._assign_task_execution_agent(codex))
        results = await asyncio.gather(first._run_task_once(native), second._run_task_once(codex))
        assert [result.content for result in results] == ["project01 Native", "project02 Codex"]
        second._run_native_agent.assert_not_awaited()
        call = second.external_broker.run.await_args.kwargs
        assert call["task"].project_id == "project02"
        assert call["adapter"] is not adapter
        assert adapter.config.model_dump() == before
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["unavailable", "failed", "denied"])
def test_explicit_codex_failure_never_calls_native_or_other_agents(tmp_path, failure):
    async def run():
        engine = execution_engine(tmp_path, available=failure != "unavailable")
        task = company_task()
        engine._run_native_agent = AsyncMock(side_effect=AssertionError("Silent Native fallback"))
        engine._build_external_agent_task = AsyncMock(side_effect=lambda task: task)
        engine._resolve_external_workspace = lambda task: str(tmp_path)
        artifacts = {"approval": {"action": ApprovalAction.REJECT.value, "policy_source": "human_escalation", "rationale": "User decision: deny"}} if failure == "denied" else {}
        engine.external_broker = SimpleNamespace(run=AsyncMock(return_value=TaskResult(status=TaskStatus.FAILED, content=failure, artifacts=artifacts)))
        if failure == "unavailable":
            engine.adapter_registry.get = lambda name: None
        result = await engine._run_task_once(task)
        assert result.status == TaskStatus.FAILED
        engine._run_native_agent.assert_not_awaited()
        assert task.metadata["selected_execution_agent"] == "codex"
        assert engine.external_broker.run.await_count == (0 if failure == "unavailable" else 1)
    asyncio.run(run())


@pytest.mark.timeout(20, method="thread")
def test_broker_runs_fenced_codex_and_keeps_project_context_and_resume_tokens_separate(tmp_path, monkeypatch):
    """Exercise real subprocess launch/attestation/persistence without a model call."""
    monkeypatch.setenv("OPC_HOME", str(tmp_path / "opc-home"))
    monkeypatch.setattr(CodexAdapter, "post_install_agent_home", lambda *args: None)

    class ScriptedCodex(CodexAdapter):
        async def start_process(self, cmd, workspace_path, extra_env=None, task=None, launch_metadata=None):
            assert 'sandbox_mode="workspace-write"' in cmd
            assert extra_env["OPC_COMMS_PROJECT"] == task.project_id
            assert extra_env["OPC_TASK_ID"] == task.id
            return await asyncio.create_subprocess_exec(
                sys.executable, "-c",
                "import json,os,pathlib; p=os.environ['OPC_COMMS_PROJECT']; "
                "pathlib.Path('result.txt').write_text(p); "
                "print(json.dumps({'type':'thread.started','thread_id':'thread-'+p})); "
                "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':p}}))",
                cwd=workspace_path, env={**os.environ, **extra_env},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        approval = SimpleNamespace(authorize_external_action=AsyncMock(return_value=(True, ApprovalDecision(
            action=ApprovalAction.AUTO_APPROVE, risk_level=RiskLevel.LOW, rationale="test"))))
        broker = ExternalAgentBroker(store, approval)
        try:
            for project in ("project01", "project02"):
                workspace = tmp_path / project
                workspace.mkdir()
                task = company_task(project)
                task.session_id = "same-chat-id"
                task.metadata["workspace_root"] = str(workspace)
                await store.save_task(task)
                result = await broker.run(ScriptedCodex(ExternalAgentConfig(run_mode="exec")), task, str(workspace))
                assert result.status == TaskStatus.DONE, result.content
                assert (workspace / "result.txt").read_text() == project
                assert result.artifacts["company_workspace_attestation"]["validated"] is True
                restored = CodexAdapter()
                await broker._restore_session_resume_from_store(restored, task)
                assert restored.config.session_id == f"thread-{project}"
        finally:
            await store.close()
    asyncio.run(run())
