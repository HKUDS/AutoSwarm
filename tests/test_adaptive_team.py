from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from opc.core.config import EmployeeConfig, OPCConfig
from opc.core.models import ExecutionCheckpoint, ExecutionMode, RouterDecision, TaskResult, TaskStatus
from opc.core.org_config import apply_org_config_payload_to_config, validate_runnable_org_config
from opc.engine import OPCEngine
from opc.layer2_organization.adaptive_team import AdaptiveTeamPlan
from opc.layer2_organization.company_mode import CompanyRuntimeSpec
from opc.layer2_organization.custom_runtime import CustomRuntimeRunner
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.recruiter import CompanyRecruiter, deserialize_recruitment_plan, serialize_recruitment_plan
from opc.layer2_organization.talent_market import TalentMarket


def team_payload(count=1, *, chain=False):
    return {
        "decision": "solo" if count == 1 else "team",
        "motivations": [] if count == 1 else ["better", "faster"],
        "comparison_with_solo": "Independent components can run concurrently." if count > 1 else "A single owner can deliver this small task.",
        "coordination_costs": "Context loading, messages, review and integration consume tokens and time.",
        "integration_strategy": "Root integrates accepted artifacts and validates the original request.",
        "team_size_review": {"smaller_alternative": "One owner handles the whole task.",
                             "hierarchy_tradeoff": "Each intermediate lead integrates a concrete subsystem."},
        "roles": [{"id": f"role_{index}", "name": f"Owner {index}",
                   "responsibility": f"Implement and verify component {index}.",
                   "parent_id": None if index == 0 else f"role_{index - 1 if chain else 0}",
                   "deliverables": [f"component_{index}"], "ownership": [f"component_{index}"],
                   "acceptance_criteria": ["The requested component works and has evidence."],
                   "dependencies": [], "status": "direct_role_execution",
                   "rationale": "No specialized persona adds useful capability."}
                  for index in range(count)],
    }


class Planner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def simple_chat(self, prompt, **kwargs):
        self.calls.append(json.loads(prompt))
        return json.dumps(self.responses.pop(0))


def make_engine(root, planner):
    config = OPCConfig()
    engine = OPCEngine(config=config, opc_home=root, project_id="adaptive-test")
    engine.llm = planner
    engine.org_engine = OrgEngine(config, root)
    engine.talent_market = TalentMarket(root, config)
    engine.company_recruiter = CompanyRecruiter(planner, engine.org_engine, engine.talent_market)
    return engine


def spec(**metadata):
    return CompanyRuntimeSpec(original_request="Build and verify the requested components.", metadata=metadata)


@pytest.mark.parametrize("count,chain", [(1, False), (6, False), (6, True)])
def test_solo_large_and_deep_teams_compile_without_size_or_depth_quotas(count, chain):
    team = AdaptiveTeamPlan.model_validate(team_payload(count, chain=chain))
    base = OPCConfig()
    before = base.model_dump()
    payload = team.org_payload(base, organization_id="adaptive_test")
    config = apply_org_config_payload_to_config(base, payload)
    validate_runnable_org_config(config, organization_id="adaptive_test")
    org = OrgEngine(config)
    topology = org.build_runtime_delegation_topology()
    runtime = org.build_company_work_item_runtime_plan("custom", runtime_topology=topology, original_request="Build components")
    assert len(org.list_agents()) == count
    assert topology["final_decider_role_id"] == "role_0"
    assert runtime is not None
    assert base.model_dump() == before
    if chain:
        assert len(org.get_chain_of_command("role_5")) >= 5


@pytest.mark.parametrize("damage", ["reporting_cycle", "dependency_cycle", "unknown_parent", "duplicate", "unknown_dependency"])
def test_structural_errors_are_rejected(damage):
    raw = team_payload(3)
    if damage == "reporting_cycle":
        raw["roles"][1]["parent_id"] = "role_2"
        raw["roles"][2]["parent_id"] = "role_1"
    elif damage == "dependency_cycle":
        raw["roles"][1]["dependencies"] = ["role_2"]
        raw["roles"][2]["dependencies"] = ["role_1"]
    elif damage == "unknown_parent":
        raw["roles"][1]["parent_id"] = "missing"
    elif damage == "duplicate":
        raw["roles"][1]["id"] = "role_0"
    else:
        raw["roles"][1]["dependencies"] = ["missing"]
    with pytest.raises(ValueError):
        AdaptiveTeamPlan.model_validate(raw)


def test_external_owner_reference_requires_agent_correction():
    raw = team_payload(2)
    raw["roles"][0]["parent_id"] = "owner"
    with pytest.raises(ValueError, match="exactly one accountable root"):
        AdaptiveTeamPlan.model_validate(raw)
    # An actual role named owner remains a valid reporting target.
    raw["roles"][0].update(id="owner", parent_id=None)
    raw["roles"][1]["parent_id"] = "owner"
    assert AdaptiveTeamPlan.model_validate(raw).roles[1].parent_id == "owner"


@pytest.mark.parametrize("template_id", ["fullstack-engineer", "another-specialist"])
def test_ambiguous_staff_ids_are_corrected_by_agent_instead_of_runtime(tmp_path, template_id):
    raw = team_payload()
    raw["roles"][0].update(status="existing_staff", employee_id="experienced", template_id=template_id)
    corrected = json.loads(json.dumps(raw))
    corrected["roles"][0]["template_id"] = ""
    planner = Planner(raw, corrected)
    engine = make_engine(tmp_path, planner)
    engine.config.org.employees = [EmployeeConfig(employee_id="experienced", name="Existing", role_id="old_role", template_id="fullstack-engineer")]
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="adaptive-test"))
    assert plan.proposals[0].existing_employee.employee_id == "experienced"
    assert len(planner.calls) == 2
    assert json.loads(planner.calls[1]["previous_response"]) == raw
    assert "Existing staff needs only an employee_id" in planner.calls[1]["validation_feedback"][0]


@pytest.mark.parametrize("inline_fence", [False, True])
def test_invalid_json_is_returned_to_agent_for_correction(tmp_path, inline_fence):
    raw = team_payload()
    raw["comparison_with_solo"] = 'The user requested "one owner" explicitly.'
    response = json.dumps(raw).replace(r'\"one owner\"', '"one owner"')
    if inline_fence:
        response = f"```{response}```"
    calls = []
    responses = [response, json.dumps(raw)]

    async def reply(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    engine = make_engine(tmp_path, SimpleNamespace(simple_chat=reply))
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test"))
    assert len(plan.proposals) == 1
    assert len(calls) == 2
    assert json.loads(calls[1]["prompt"])["previous_response"] == response


@pytest.mark.parametrize("truncation", ["outer_object", "after_complete_role"])
def test_truncated_plan_is_returned_to_agent_for_completion(tmp_path, truncation):
    raw = team_payload(2)
    complete = json.dumps(raw)
    truncated = complete[:-1] if truncation == "outer_object" else complete[:-2]
    responses = [truncated, complete]
    calls = []

    async def reply(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    engine = make_engine(tmp_path, SimpleNamespace(simple_chat=reply))
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test"))
    assert len(plan.proposals) == 2
    assert len(calls) == 2
    assert json.loads(calls[1]["prompt"])["previous_response"] == truncated


def test_one_call_designs_roles_and_hires_and_roundtrips(tmp_path):
    raw = team_payload(2)
    raw["roles"][1].update(status="proposed_hire", template_id="fullstack-engineer", rationale="Matches the concrete full stack implementation responsibility.")
    planner = Planner(raw)
    engine = make_engine(tmp_path, planner)
    before = engine.config.model_dump()
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="adaptive-test"))
    assert len(planner.calls) == 1
    assert len(plan.proposals) == 2
    assert plan.proposals[1].candidate.template_id == "fullstack-engineer"
    assert "category_catalog" not in planner.calls[0]  # no separate triage/role-filling pass
    assert "candidate_pool" in planner.calls[0]
    assert engine.config.model_dump() == before
    assert not (tmp_path / "config").exists()
    restored = deserialize_recruitment_plan(serialize_recruitment_plan(plan))
    assert restored.metadata["adaptive_team_plan"] == plan.metadata["adaptive_team_plan"]
    assert "Coordination costs" in restored.summary


def test_unknown_staff_id_is_repaired_without_inventing_a_hire(tmp_path):
    invalid = team_payload()
    invalid["roles"][0].update(status="proposed_hire", template_id="nonexistent")
    planner = Planner(invalid, team_payload())
    engine = make_engine(tmp_path, planner)
    result = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="adaptive-test"))
    assert result.proposals[0].status == "direct_role_execution"
    assert len(planner.calls) == 2
    assert "Invalid template_id" in planner.calls[1]["validation_feedback"][0]


@pytest.mark.parametrize("recruitment_agent", ["native", "codex"])
def test_agent_can_correct_plan_beyond_three_responses(tmp_path, recruitment_agent):
    invalid = team_payload()
    invalid["roles"][0].update(status="proposed_hire", template_id="nonexistent")
    planner = Planner(invalid, invalid, invalid, invalid, team_payload(2))
    engine = make_engine(tmp_path, planner)
    result = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(
        spec(), project_id="adaptive-test", recruitment_llm=planner, recruitment_agent=recruitment_agent,
    ))
    assert len(planner.calls) == 5
    assert len(result.proposals) == 2
    assert result.metadata["recruitment_agent"] == recruitment_agent
    assert result.metadata["planning_attempts"] == 5
    assert not (tmp_path / "config").exists()


def test_existing_employee_can_cover_compatible_responsibilities(tmp_path):
    raw = team_payload(2)
    for role in raw["roles"]:
        role.update(status="existing_staff", employee_id="experienced", rationale="Relevant existing implementation experience; execution is serialized.")
    planner = Planner(raw)
    engine = make_engine(tmp_path, planner)
    engine.config.org.employees = [EmployeeConfig(employee_id="experienced", name="Existing", role_id="legacy_role")]
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="adaptive-test"))
    assert {p.existing_employee.employee_id for p in plan.proposals} == {"experienced"}
    assert len(engine.config.org.employees) == 1


def test_planning_can_remain_active_beyond_old_deadline(tmp_path):
    async def run():
        loop = asyncio.get_running_loop()
        real_clock = loop.time
        elapsed = 0.0
        loop.time = lambda: real_clock() + elapsed
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def slow(**kwargs):
            calls.append(json.loads(kwargs["prompt"]))
            started.set()
            await release.wait()
            return json.dumps(team_payload(2))

        engine = make_engine(tmp_path, SimpleNamespace(simple_chat=slow))
        task = asyncio.create_task(engine.company_recruiter.build_team_recruitment_plan(
            spec(planning_timeout_seconds=0.01), project_id="test",
        ))
        try:
            await started.wait()
            elapsed = 121.0
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done()
            release.set()
            result = await task
            assert len(result.proposals) == 2
            assert "remaining_seconds" not in calls[0]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            loop.time = real_clock

    asyncio.run(run())


def test_user_can_stop_planning_and_cancel_the_provider(tmp_path):
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        engine = make_engine(tmp_path, SimpleNamespace(simple_chat=slow))
        task = asyncio.create_task(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert not (tmp_path / "config").exists()

    asyncio.run(run())


@pytest.mark.parametrize("error", [TimeoutError("Provider connection failed"), ValueError("Unknown model"), RuntimeError("Agent failed")])
def test_provider_failure_propagates_without_restarting_or_replacing_agent(tmp_path, error):
    planner = SimpleNamespace(simple_chat=AsyncMock(side_effect=error))
    engine = make_engine(tmp_path, planner)
    with pytest.raises(type(error), match=str(error)) as exc:
        asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test"))
    assert exc.value is error
    planner.simple_chat.assert_awaited_once()
    assert not (tmp_path / "config").exists()


def test_fixed_recruitment_agent_corrects_triage_and_staffing_without_rule_fallback(tmp_path):
    calls = {"triage": [], "staffing": []}

    async def reply(prompt, **kwargs):
        payload = json.loads(prompt)
        stage = "triage" if "category_catalog" in payload else "staffing"
        calls[stage].append(payload)
        if len(calls[stage]) <= 4:
            return json.dumps({})
        if stage == "triage":
            category = payload["category_catalog"][0]["category"]
            return json.dumps({"roles": [
                {"role_id": role["role_id"], "action": "category_screening", "categories": [category],
                 "rationale": "Compare relevant expertise for this responsibility."}
                for role in payload["roles"]
            ]})
        candidate = payload["candidate_pool"][0]["template_id"]
        return json.dumps({"proposals": [
            {"role_id": role["role_id"], "status": "proposed_hire", "template_id": candidate,
             "rationale": "The agent chose this expertise after comparing the responsibilities."}
            for role in payload["roles"]
        ]})

    engine = make_engine(tmp_path, SimpleNamespace(simple_chat=reply))
    result = asyncio.run(engine.company_recruiter.build_recruitment_plan(spec(), domains=[], project_id="test"))
    assert len(calls["triage"]) == len(calls["staffing"]) == 5
    assert calls["triage"][-1]["previous_response"] == "{}"
    assert calls["staffing"][-1]["previous_response"] == "{}"
    assert all(p.status == "proposed_hire" and p.metadata["selection_source"] == "llm_global" for p in result.proposals)
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("stage", ["triage", "staffing"])
def test_fixed_recruitment_provider_failure_is_visible_without_rule_fallback(tmp_path, stage):
    calls = []
    error = RuntimeError("Selected recruitment agent failed")

    async def reply(prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append(payload)
        if stage == "triage" or "category_catalog" not in payload:
            raise error
        return json.dumps({"roles": [
            {"role_id": role["role_id"], "action": "direct_role_execution", "categories": []}
            for role in payload["roles"]
        ]})

    engine = make_engine(tmp_path, SimpleNamespace(simple_chat=reply))
    with pytest.raises(RuntimeError, match=str(error)) as exc:
        asyncio.run(engine.company_recruiter.build_recruitment_plan(spec(), domains=[], project_id="test"))
    assert exc.value is error
    assert len(calls) == (1 if stage == "triage" else 2)
    assert not (tmp_path / "config").exists()


def test_automatic_recruitment_requires_agent_instead_of_heuristic_staffing(tmp_path):
    engine = make_engine(tmp_path, None)
    with pytest.raises(ValueError, match="requires an available planning agent"):
        asyncio.run(engine.company_recruiter.build_recruitment_plan(spec(), domains=[], project_id="test"))


def test_solo_plan_still_gets_one_unified_confirmation_even_in_confirmed_session(tmp_path):
    engine = make_engine(tmp_path, Planner(team_payload()))
    engine._session_has_completed_recruitment_confirmation = AsyncMock(return_value=True)
    engine._save_execution_checkpoint = AsyncMock()
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate")
    asyncio.run(engine._begin_company_recruitment_loop(
        decision, "Build components", spec(), adaptive_team=True,
        session_id="session", origin_channel="cli", origin_chat_id="", origin_thread_id="",
    ))
    checkpoint = engine._save_execution_checkpoint.await_args.args[0]
    assert checkpoint["checkpoint_type"] == "company_recruitment_confirmation"
    assert checkpoint["payload"]["recruitment_plan"]["metadata"]["adaptive_team_plan"]["decision"] == "solo"
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("strategy", ["adaptive", "fixed"])
def test_company_strategy_selects_the_correct_preflight(tmp_path, strategy):
    planner = Planner(team_payload())
    engine = make_engine(tmp_path, planner)
    engine._session_has_completed_recruitment_confirmation = AsyncMock(return_value=False)
    engine._save_execution_checkpoint = AsyncMock()
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate",
                              metadata={"company_team_strategy": strategy, "recruitment_agent": "native"})
    asyncio.run(engine._begin_company_staffing_loop(
        decision, "Build components", spec(), session_id="session",
        origin_channel="cli", origin_chat_id="", origin_thread_id="",
    ))
    checkpoint = engine._save_execution_checkpoint.await_args.args[0]
    if strategy == "adaptive":
        assert checkpoint["checkpoint_type"] == "company_recruitment_confirmation"
        assert len(planner.calls) == 1
    else:
        assert checkpoint["checkpoint_type"] == "company_staffing_selection"
        assert not planner.calls


def test_approved_roster_executes_in_isolated_org_and_preserves_company(tmp_path):
    async def run():
        raw = team_payload(2)
        raw["roles"][1].update(status="proposed_hire", template_id="fullstack-engineer")
        engine = make_engine(tmp_path, Planner(raw))
        plan = await engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test")
        plan.metadata["adaptive_organization_id"] = "adaptive_approved"
        before = engine.config.model_dump()
        runner = CustomRuntimeRunner(engine)
        captured = {}

        async def create_runtime(*, project_id, org_id, base_config=None):
            from opc.core.org_config import load_org_config_payload
            from opc.core.employee_registry import load_employee_registry

            payload, _ = load_org_config_payload(tmp_path / "config", org_id)
            config = apply_org_config_payload_to_config(OPCConfig(), payload)
            captured["roles"] = [r.id for r in config.org.roles]
            captured["profile"] = config.org.company_profile
            assert [e.employee_id for e in load_employee_registry(tmp_path, org_id)] == ["fullstack-engineer"]
            runtime = SimpleNamespace(_continue_company_mode_execution=AsyncMock(return_value="executed"),
                                      _save_project_company_staffing_defaults=MagicMock())
            captured["runtime"] = runtime
            return runtime, org_id

        runner._create_runtime = create_runtime
        runner._shutdown_runtime = AsyncMock()
        result = await runner.execute_adaptive_team(
            plan, RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate"), spec(),
            "Build components", session_id="session",
        )
        assert result == "executed"
        assert captured["roles"] == ["role_0", "role_1"]
        assert captured["profile"] == "custom"
        kwargs = captured["runtime"]._continue_company_mode_execution.await_args.kwargs
        assert kwargs["staffing_overrides"]["role_1"] == "fullstack-engineer"
        assert kwargs["staffing_experience_modes"]["role_1"] == "template_only"
        assert engine.config.model_dump() == before
        assert not (tmp_path / "config" / "company_index.yaml").exists()
        assert not (tmp_path / "config" / "company_orgs" / "org_corporate_config.yaml").exists()
        assert not (tmp_path / "config" / "company_orgs" / "org_adaptive_approved_config.yaml").exists()
        assert (tmp_path / "config" / "runtime_orgs" / "org_adaptive_approved_config.yaml").exists()
        runner._shutdown_runtime.assert_awaited_once()

    asyncio.run(run())


def test_codex_planner_cannot_inherit_worker_write_or_approval_flags(tmp_path, monkeypatch):
    from opc.core.config import ExternalAgentConfig
    from opc.layer3_agent.adapters.codex_adapter import CodexAdapter

    events = '\n'.join([json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok": true}'}}),
                        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}})])
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(events.encode(), b"")))
    launch = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    adapter = CodexAdapter(ExternalAgentConfig(model="test-model", approval_mode="full-auto",
                                               extra_args=["--dangerously-bypass-approvals-and-sandbox", "--add-dir", "/shared"]))
    result = asyncio.run(adapter.plan_json("Return JSON", str(tmp_path)))
    argv = launch.await_args.args
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--add-dir" not in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert 'approval_policy="never"' in argv
    assert 'features.shell_tool=false' in argv
    assert "mcp_servers={}" in argv
    assert result.content == '{"ok": true}'
    assert result.artifacts["planning_usage"] == [{"input_tokens": 12, "output_tokens": 3}]


def test_selected_planner_is_honored_and_worker_broker_is_not_used(tmp_path):
    engine = make_engine(tmp_path, None)
    planner = AsyncMock(return_value=TaskResult(status=TaskStatus.DONE, content=json.dumps(team_payload())))
    engine.adapter_registry = SimpleNamespace(get=lambda name: SimpleNamespace(plan_json=planner) if name == "codex" else None)
    engine.external_broker = SimpleNamespace(run=AsyncMock())
    llm, selected = engine._resolve_recruitment_llm("codex")
    result = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="test", recruitment_llm=llm, recruitment_agent=selected))
    assert result.metadata["recruitment_agent"] == "codex"
    planner.assert_awaited_once()
    engine.external_broker.run.assert_not_awaited()
    assert str(tmp_path / "planning") in planner.await_args.kwargs["workspace_path"]
    with pytest.raises(ValueError, match="Selected execution agent `cursor` is unavailable"):
        engine._resolve_recruitment_llm("cursor")


@pytest.mark.parametrize("agent", ["native", "codex", "claude_code", "cursor", "opencode", "jiuwen", "jiuwenswarm"])
def test_formation_uses_execution_choice_despite_stale_separate_recruiter(tmp_path, agent):
    engine = make_engine(tmp_path, Planner(team_payload(2)))
    external = AsyncMock(return_value=TaskResult(status=TaskStatus.DONE, content=json.dumps(team_payload(2))))
    if agent != "native":
        engine.llm = SimpleNamespace(simple_chat=AsyncMock(side_effect=AssertionError("Selected external agent must not call Native")))
        engine.company_recruiter.llm = engine.llm
    engine.adapter_registry = SimpleNamespace(get=lambda name: SimpleNamespace(plan_json=external) if name == agent else None)
    engine._save_execution_checkpoint = AsyncMock()
    engine._session_has_completed_recruitment_confirmation = AsyncMock(return_value=False)
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate", preferred_agent=agent,
                              metadata={"company_team_strategy": "adaptive", "recruitment_agent": "native" if agent != "native" else "codex"})
    asyncio.run(engine._begin_company_staffing_loop(
        decision, "Build components", spec(), session_id="session", origin_channel="cli", origin_chat_id="", origin_thread_id="",
    ))
    saved = engine._save_execution_checkpoint.await_args.args[0]["payload"]
    assert saved["recruitment_agent"] == agent
    assert {p["metadata"]["selected_execution_agent"] for p in saved["recruitment_plan"]["proposals"]} == {agent}
    if agent == "native":
        external.assert_not_awaited()
    else:
        external.assert_awaited_once()
        engine.llm.simple_chat.assert_not_awaited()


def test_unavailable_selected_execution_agent_cannot_form_team_using_native(tmp_path):
    planner = Planner(team_payload())
    engine = make_engine(tmp_path, planner)
    engine.adapter_registry = SimpleNamespace(get=lambda name: None)
    engine._save_execution_checkpoint = AsyncMock()
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate", preferred_agent="codex",
                              metadata={"company_team_strategy": "adaptive", "recruitment_agent": "native"})
    with pytest.raises(ValueError, match="cannot switch to another agent"):
        asyncio.run(engine._begin_company_staffing_loop(
            decision, "Build components", spec(), session_id="session", origin_channel="cli", origin_chat_id="", origin_thread_id="",
        ))
    assert not planner.calls
    engine._save_execution_checkpoint.assert_not_awaited()


def test_manual_lead_execution_choice_also_selects_recruitment_agent(tmp_path):
    engine = make_engine(tmp_path, None)
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate", preferred_agent="native")
    root_id = engine.org_engine.get_final_decider_role_id()
    assert engine._company_recruitment_agent(decision, {root_id: "codex"}) == "codex"
    # Adaptive revisions use their generated lead, not the original preset's CEO.
    assert engine._company_recruitment_agent(decision, {"research_lead": "codex"}, root_role_id="research_lead") == "codex"


def test_import_adds_new_categories_without_treating_playbooks_as_candidates(tmp_path):
    source = tmp_path / "agency"
    for directory in ["gis", "healthcare", "security", "strategy", "examples"]:
        (source / directory).mkdir(parents=True)
    for category in ["gis", "healthcare", "security"]:
        (source / category / "expert.md").write_text(f"---\nname: {category} expert\ndescription: Useful specialist\n---\n# Expert\n")
    (source / "strategy" / "QUICKSTART.md").write_text("# Start a workflow\n")
    (source / "examples" / "workflow.md").write_text("# Example only\n")
    market = TalentMarket(tmp_path / "state", OPCConfig())
    imported = market.import_from_repo(source)
    assert {t.category for t in imported} == {"gis", "healthcare", "security"}
    assert {t.category for t in market.scan_local_talent()} == {"gis", "healthcare", "security"}


@pytest.mark.parametrize("storage", ["legacy", "runtime"])
def test_adaptive_hires_are_recalled_only_for_their_source_org_and_project(tmp_path, storage):
    from opc.core.config import write_company_org_payload
    from opc.core.org_config import write_runtime_org_config_payload

    engine = make_engine(tmp_path, None)
    for org_id, source_org, project in [("adaptive_prior", "corporate", "adaptive-test"),
                                        ("adaptive_other_project", "corporate", "other"),
                                        ("adaptive_other_org", "different_company", "adaptive-test")]:
        payload = AdaptiveTeamPlan.model_validate(team_payload()).org_payload(engine.config, organization_id=org_id)
        payload["metadata"].update(adaptive_source_organization_id=source_org, adaptive_source_project_id=project)
        payload["employees"] = [EmployeeConfig(employee_id=org_id, name=org_id, role_id="role_0").model_dump()]
        writer = write_company_org_payload if storage == "legacy" else write_runtime_org_config_payload
        writer(tmp_path / "config", org_id, payload)
    employees = engine.talent_market.list_adaptive_staffing_employees(project_id="adaptive-test")
    assert [e.employee_id for e in employees] == ["adaptive_prior"]
    assert employees[0].metadata["staffing_source_organization_id"] == "adaptive_prior"
    raw = team_payload()
    raw["roles"][0].update(status="existing_staff", employee_id="adaptive_prior")
    planner = Planner(raw)
    engine.company_recruiter.llm = planner
    plan = asyncio.run(engine.company_recruiter.build_team_recruitment_plan(spec(), project_id="adaptive-test"))
    assert plan.proposals[0].existing_employee.employee_id == "adaptive_prior"


def test_auto_team_checkpoint_flow_supports_feedback_and_approval(tmp_path, monkeypatch):
    from opc.database.store import OPCStore
    from opc.layer0_interaction.coordinator import InteractionCoordinator

    async def run():
        planner = Planner(team_payload(3), team_payload())
        engine = make_engine(tmp_path, planner)
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine.store = store
        engine.interaction_coordinator = InteractionCoordinator(
            store=store, project_id=engine.project_id,
            checkpoint_changed_callback=engine._interaction_checkpoint_changed,
            orphaned_answer_callback=engine._schedule_interaction_consumption,
        )
        try:
            decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate")
            await engine._begin_company_staffing_loop(
                decision, "Build components", spec(), session_id="session",
                origin_channel="cli", origin_chat_id="", origin_thread_id="",
            )
            first = await store.get_latest_pending_checkpoint(engine.project_id, "session")
            assert first.checkpoint_type == "company_staffing_selection"
            assert not InteractionCoordinator.validate_decision(first, {"staffing_action": "auto_team"})
            await engine._maybe_resume_checkpoint("auto team", "session")
            combined = await store.get_latest_pending_checkpoint(engine.project_id, "session")
            assert combined.checkpoint_type == "company_recruitment_confirmation"
            assert len(combined.payload["recruitment_plan"]["proposals"]) == 3
            from opc.plugins.office_ui.snapshot_builder import _build_snapshot_checkpoint_meta

            card = await _build_snapshot_checkpoint_meta(engine, SimpleNamespace(session_id="session"))
            assert card["staffing_roles"][1]["reports_to"] == "role_0"
            assert card["staffing_roles"][1]["role_responsibility"]
            await engine._maybe_resume_checkpoint("Use a solo owner; avoid handoff cost", "session")
            revised = await store.get_latest_pending_checkpoint(engine.project_id, "session")
            assert revised.payload["recruitment_revision"] == 2
            assert len(revised.payload["recruitment_plan"]["proposals"]) == 1
            assert planner.calls[1]["recruiter_feedback"]
            execute = AsyncMock(return_value="adaptive runtime executed")
            monkeypatch.setattr(CustomRuntimeRunner, "execute_adaptive_team", execute)
            await engine._maybe_resume_checkpoint("approve", "session")
            execute.assert_awaited_once()
            assert len(planner.calls) == 2  # initial unified plan plus requested revision only
        finally:
            await engine.interaction_coordinator.shutdown()
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("execution_agent", ["native", "codex"])
def test_approved_team_bootstraps_real_company_runtime_without_recruiting_again(tmp_path, monkeypatch, execution_agent):
    from opc.database.store import OPCStore
    from opc.layer3_agent.adapters.codex_adapter import CodexAdapter

    async def run():
        planner = Planner(team_payload(2))
        engine = make_engine(tmp_path, planner)
        store = OPCStore(tmp_path / "tasks.db")
        await store.initialize()
        engine.store = store
        observed = {}

        async def execute(runtime, tasks, runtime_plan):
            root = tasks[0]
            observed["root"] = root
            assert await runtime._assign_task_execution_agent(root) == (None if execution_agent == "native" else "codex")
            runtime._run_native_agent = AsyncMock(
                return_value=TaskResult(status=TaskStatus.DONE, content="native runtime ready"),
                side_effect=AssertionError("Codex selection must never call the Native/GLM provider") if execution_agent == "codex" else None,
            )
            runtime.external_broker.run = AsyncMock(return_value=TaskResult(status=TaskStatus.DONE, content="codex runtime ready"))
            result = await runtime._run_task_once(root)
            if execution_agent == "codex":
                runtime._run_native_agent.assert_not_awaited()
                call = runtime.external_broker.run.await_args.kwargs
                assert isinstance(call["adapter"], CodexAdapter)
                assert call["task"].project_id == "adaptive-test"
                assert root.metadata["external_company_execution_fence"] == "validated_workspace"
            else:
                runtime.external_broker.run.assert_not_awaited()
            return result.content

        for agent in engine.config.agents.agents.values():
            agent.enabled = False
        if execution_agent == "codex":
            engine.config.agents.agents["codex"].enabled = True
            monkeypatch.setattr(CodexAdapter, "is_available", AsyncMock(return_value=True))
        monkeypatch.setattr(OPCEngine, "_execute_company_mode", execute)
        monkeypatch.setattr(OPCEngine, "_resolve_workspace_contract", AsyncMock(return_value={
            "workspace_root": str(tmp_path / "work"), "output_root": str(tmp_path / "work" / "output"),
            "comms_workspace_root": str(tmp_path / "work"), "comms_root": str(tmp_path / "comms"),
        }))
        try:
            plan = await engine.company_recruiter.build_team_recruitment_plan(spec(), project_id=engine.project_id)
            plan.metadata["adaptive_organization_id"] = "adaptive_bootstrap"
            result = await CustomRuntimeRunner(engine).execute_adaptive_team(
                plan, RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate"),
                spec(), "Build components", session_id="bootstrap-session",
                role_agent_overrides={"role_0": execution_agent, "role_1": execution_agent},
            )
            assert result == f"{execution_agent} runtime ready"
            root = observed["root"]
            assert root.assigned_to == "role_0"
            assert root.metadata["org_id"] == "adaptive_bootstrap"
            assert root.metadata["company_profile"] == "custom"
            assert root.metadata["runtime_topology"]["final_decider_role_id"] == "role_0"
            assert len(planner.calls) == 1
            assert engine.config.org.company_profile == "corporate"
            defaults_path = tmp_path / "projects" / "adaptive-test" / "company_staffing_defaults.json"
            defaults = json.loads(defaults_path.read_text())
            assert defaults["scopes"]["profile:custom|org:adaptive_bootstrap"]["recruitment_role_agents"] == {
                "role_0": execution_agent, "role_1": execution_agent,
            }
            assert not (tmp_path / "projects" / "other-project" / "company_staffing_defaults.json").exists()
        finally:
            await store.close()

    asyncio.run(run())
