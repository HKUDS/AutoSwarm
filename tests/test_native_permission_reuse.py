from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from opc.core.company_controller import CompanyRunControllerLeaseLost
from opc.core.config import AutonomyConfig
from opc.core.models import ApprovalAction, ApprovalDecision, DelegationRun, DelegationWorkItem, ExecutionCheckpoint, PermissionResolution, RiskLevel, Task
from opc.database.store import OPCStore
from opc.layer0_interaction.coordinator import InteractionCoordinator
from opc.layer2_organization.approval import ApprovalEngine
from opc.layer4_tools.web_search import create_web_tools
from opc.layer4_tools.opaque_execution import exact_tool_call_fingerprint
from opc.layer5_memory.preference import PreferenceManager
from opc.plugins.office_ui.services.session import SessionService
from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2
from opc.layer3_agent.runtime_v2.permissions import RuntimePermissionAdapter
from opc.layer3_agent.runtime_v2.tool_hooks import RuntimeToolHookContext
from opc.layer4_tools.registry import ToolRegistry


def approval_engine(tmp_path, store, project="p"):
    tools = {tool.name: tool for tool in create_web_tools()}
    coordinator = InteractionCoordinator(store=store, project_id=project)
    scheduled = []
    engine = ApprovalEngine(
        llm=object(), store=store, preferences=PreferenceManager(tmp_path),
        memory=SimpleNamespace(append_autonomy_event=lambda *args, **kwargs: None),
        config=AutonomyConfig(), interaction_coordinator=coordinator,
        native_tool_lookup=tools.get, on_permission_ready=lambda *args: scheduled.append(args),
    )
    return engine, tools, scheduled


@pytest.mark.parametrize("reply,expected_prompts", [("always_project", 1), ("approve_session", 1), ("approve_once", 6), ("deny", 6)])
def test_parallel_native_calls_recheck_the_first_reusable_grant(tmp_path, reply, expected_prompts):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine, tools, _ = approval_engine(tmp_path, store)
        task = Task(id="worker", session_id="role-session", project_id="p", metadata={"native_approval_level": "auto", "native_permission_scope_id": "root"})
        entered = asyncio.Event()
        release = asyncio.Event()
        prompts = []

        async def answer(task, action_kind, action_name, decision, metadata):
            prompts.append(metadata)
            entered.set()
            await release.wait()
            result = engine.apply_approval_scope_decision(reply, {
                "action_kind": action_kind, "action_name": action_name, "project_id": "p",
                "session_scope_id": "root", "allowlist_enabled": True, "allowlist_patterns": ["*"],
            })
            return result["approved"], ApprovalDecision(
                action=ApprovalAction.AUTO_APPROVE if result["approved"] else ApprovalAction.REJECT,
                risk_level=RiskLevel.MEDIUM,
                policy_source="human_escalation", rationale="Fixture owner answer",
            )

        engine._ask_user_once = answer
        try:
            pending = [asyncio.create_task(engine.authorize_tool_call(task, "web_search", {"query": f"research {i}"}, tool=tools["web_search"])) for i in range(6)]
            await asyncio.wait_for(entered.wait(), timeout=2)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*pending), timeout=5)
            assert len(prompts) == expected_prompts
            assert all(allowed == (reply != "deny") for allowed, _ in results)
            if expected_prompts == 1:
                assert all(decision.policy_source in {"approval_allowlist", "session_approval"} for _, decision in results[1:])
        finally:
            await engine.interaction_coordinator.shutdown()
            await store.close()
    asyncio.run(run())


def test_project_grant_reconsiders_existing_cards_without_granting_other_tools_or_projects(tmp_path):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine, _, scheduled = approval_engine(tmp_path, store)
        cases = [("first", "p", "web_search", "pending"), ("other-role", "p", "web_search", "pending"),
                 ("fetch", "p", "web_fetch", "pending"), ("other-project", "q", "web_search", "pending"),
                 ("denied", "p", "web_search", "answered"), ("tampered", "p", "web_search", "pending")]
        try:
            before = {}
            for name, project, tool, status in cases:
                task = Task(id=name, session_id=f"session-{name}", project_id=project,
                            metadata={"native_approval_level": "auto", "native_permission_scope_id": "root"})
                await store.save_task(task)
                arguments = {"query": name}
                fingerprint = exact_tool_call_fingerprint(tool_call_id=f"call-{name}", tool_name=tool,
                                                        arguments=arguments, runtime_session_id=f"rt-{name}")
                if name == "tampered": fingerprint = "stale-fingerprint"
                checkpoint = ExecutionCheckpoint(
                    checkpoint_id=name, task_id=name, session_id=task.session_id, project_id=project,
                    checkpoint_type="tool_permission", status="pending",
                    payload={"interaction": {"domain_key": name, "options": [{"id": "approve_once"}, {"id": "deny"}], "ownership": {"waiting_task_id": name}},
                             "tool_call": {"id": f"call-{name}", "name": tool, "arguments": arguments,
                                           "runtime_session_id": f"rt-{name}", "fingerprint": fingerprint}},
                )
                await store.create_owner_interaction_checkpoint(checkpoint, interaction_key=name)
                if status == "answered":
                    await engine.interaction_coordinator.submit(
                        checkpoint_id=name, checkpoint_type="tool_permission", checkpoint=checkpoint,
                        client_request_id="prior-denial", decision={"option_id": "deny", "text": "Deny"},
                    )
                before[name] = checkpoint.payload["tool_call"]
            engine.allowlist.add_patterns("tool", "web_search", ["*"], project_id="p")
            assert set(await engine.reconcile_pending_tool_permissions(project_id="p")) == {"first", "other-role"}
            assert set(scheduled) == {("first", "tool_permission"), ("other-role", "tool_permission")}
            for name, project, _, previous in cases:
                checkpoint = await store.get_execution_checkpoint(name, project_id=project)
                assert checkpoint.payload["tool_call"] == before[name]
                assert checkpoint.status == ("answered" if name in {"first", "other-role"} else previous)
            assert await engine.reconcile_pending_tool_permissions(project_id="p") == []
            engine.native_policy.set_session_level("root", "full-access")
            assert await engine.reconcile_pending_tool_permissions(project_id="p", scope_id="root") == ["fetch"]
            assert (await store.get_execution_checkpoint("denied", project_id="p")).status == "answered"
            assert (await store.get_execution_checkpoint("other-project", project_id="q")).status == "pending"
        finally:
            await engine.interaction_coordinator.shutdown()
            await store.close()
    asyncio.run(run())


def test_live_full_access_updates_fenced_roles_and_preserves_runtime_fields(tmp_path):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        approval, tools, _ = approval_engine(tmp_path, store)
        root = Task(id="root", project_id="p", session_id="root-session", metadata={"native_approval_level": "auto", "native_permission_scope_id": "root-session"})
        child = Task(id="child", project_id="p", session_id="child-session", parent_session_id="root-session", linked_work_item_id="work",
                     metadata={"execution_mode": "company_mode", "delegation_run_id": "run", "native_approval_level": "auto", "native_permission_scope_id": "root-session", "sentinel": "keep"})
        other = Task(id="other", project_id="p", session_id="other-session", metadata={"native_approval_level": "auto"})
        runtime = SimpleNamespace(project_id="p", store=store, memory=None, approval_engine=approval)
        try:
            await store.save_delegation_run(DelegationRun(run_id="run", project_id="p", session_id=root.session_id))
            await store.save_delegation_work_item(DelegationWorkItem(work_item_id="work", run_id="run", role_id="researcher"))
            for task in (root, child, other): await store.save_task(task)
            await store.link_work_item_runtime_task("work", child.id)
            await store.acquire_delegation_run_controller_lease("run", project_id="p", root_session_id=root.session_id, owner_token="live-controller", lease_seconds=60)
            with pytest.raises(CompanyRunControllerLeaseLost): await store.save_task(child)
            assert approval.predict(tools["web_search"], {"query": "q"}, task=child).resolution == PermissionResolution.ASK
            before = await store.get_task(child.id)
            service = SessionService(SimpleNamespace())
            await service.persist_native_permission_config(root, level="full-access", engine=runtime)
            # The executing Task instance is intentionally the old in-memory copy.
            assert approval.predict(tools["web_search"], {"query": "q"}, task=child).resolution == PermissionResolution.ALLOW
            persisted = await store.get_task(child.id)
            assert persisted.context_snapshot == before.context_snapshot
            assert persisted.status == before.status and persisted.metadata["sentinel"] == "keep"
            assert persisted.metadata["native_approval_level"] == "full-access"
            assert (await store.get_task(other.id)).metadata["native_approval_level"] == "auto"
            # A stale broad save of the UI Task cannot undo the operator update.
            stale_root = copy.deepcopy(root)
            stale_root.metadata["native_approval_level"] = "auto"
            await store.save_task(stale_root)
            assert (await store.get_task(root.id)).metadata["native_approval_level"] == "full-access"
            await service.persist_native_permission_config(root, level="auto", engine=runtime)
            assert approval.predict(tools["web_search"], {"query": "q"}, task=child).resolution == PermissionResolution.ASK
        finally:
            await approval.interaction_coordinator.shutdown()
            await store.close()
    asyncio.run(run())


def test_runtime_refreshes_its_native_policy_proof_after_a_queued_call_is_allowed(tmp_path):
    async def run():
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        approval, tools, _ = approval_engine(tmp_path, store)
        task = Task(id="worker", project_id="p", session_id="root", metadata={"native_approval_level": "auto", "native_permission_scope_id": "root"})
        tool = tools["web_search"]
        arguments = {"query": "research"}
        predicted = approval.predict(tool, arguments, task=task)
        assert predicted.resolution == PermissionResolution.ASK

        async def callback(*args, **kwargs):
            approval.native_policy.set_session_level("root", "full-access")
            current = approval.predict(tool, arguments, task=task)
            return True, ApprovalDecision(action=ApprovalAction.AUTO_APPROVE, risk_level=current.risk_level,
                                          rationale=current.rationale, policy_source=current.source, metadata=current.metadata)

        runtime = NativeRuntimeV2(llm=object(), tool_registry=ToolRegistry(), approval_callback=callback, permission_policy=approval)
        context = RuntimeToolHookContext(phase="pre", tool_name=tool.name, tool=tool, task=task,
                                         call={"id": "queued"}, arguments=arguments, predicted_permission=predicted)
        try:
            result = await runtime._approval_pre_hook(context, permission_resolver=RuntimePermissionAdapter(approval), runtime_session_id="runtime")
            assert not result.get("stop_execution", False)
            assert context.call["_native_permission_auto_sign"] == {
                "level": "full-access", "policy_source": "native_permission_policy", "scope_id": "root",
            }
        finally:
            await approval.interaction_coordinator.shutdown()
            await store.close()
    asyncio.run(run())
