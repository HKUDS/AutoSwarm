from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from opc.core.config import ExternalTeamBindingConfig, OPCConfig, RoleConfig
from opc.core.models import ExecutionCheckpoint, ExecutionMode, RouterDecision
from opc.engine import OPCEngine
from opc.layer2_organization.company_mode import CompanyRuntimeSpec
from opc.layer2_organization.external_team_compiler import apply_external_team_bindings_to_topology
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.recruiter import CompanyRecruiter
from opc.layer2_organization.talent_market import TalentMarket
from opc.plugins.office_ui.snapshot_builder import _build_snapshot_checkpoint_meta
from opc.plugins.office_ui.ws_handler import WSHandler


def make_engine(root, *, custom=False, project_id="staffing-choice"):
    config = OPCConfig()
    if custom:
        config.org.company_profile = "custom"
        config.org.organization_id = "engineering-lab"
        config.org.roles = [
            RoleConfig(id="ceo", name="Lead", responsibility="Lead delivery", reports_to="owner"),
            RoleConfig(id="cto", name="Tech", responsibility="Lead engineering", reports_to="ceo"),
            RoleConfig(id="engineer", name="Engineer", responsibility="Implement", reports_to="cto"),
        ]
    config.org.external_team_bindings = [
        ExternalTeamBindingConfig(boundary_role_id=role, max_inflight=2, metadata={"deliverables": ["code"]})
        for role in (["cto"] if custom else ["cto", "cmo", "coo"])
    ]
    engine = OPCEngine(config=config, opc_home=root, project_id=project_id)
    engine.org_engine = OrgEngine(config, root)
    engine.talent_market = TalentMarket(root, config)
    engine.adapter_registry = SimpleNamespace(
        list_available=lambda: ["jiuwenswarm", "jiuwen", "codex"],
        get=lambda name: SimpleNamespace(
            capabilities=lambda: {"company_mode": True},
            supports_company_execution=lambda: True,
            execution_unit_kind=lambda: "external_agent",
        ),
    )
    engine._load_project_company_staffing_defaults = lambda *args, **kwargs: {}
    engine.company_recruiter = CompanyRecruiter(None, engine.org_engine, engine.talent_market)
    engine._resolve_recruitment_llm = lambda agent: (None, "native")
    return engine


def make_payload(engine, preferred_agent=None, *, session_id="staffing-session"):
    profile = engine.config.org.company_profile
    return engine._build_manual_staffing_checkpoint_payload(
        RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile=profile,
                       org_id="engineering-lab" if profile == "custom" else None,
                       preferred_agent=preferred_agent, domains=[]),
        "Build and test the product", CompanyRuntimeSpec(profile=profile),
        session_id=session_id, origin_channel="ui", origin_chat_id="", origin_thread_id="",
    )


def execution_plan(engine, payload, agents):
    teams, overrides = engine._apply_staffing_external_team_bindings(payload, {"recruitment_role_agents": agents})
    _, overrides = engine._filter_staffing_for_external_teams(teams, overrides)
    topology = engine._enrich_runtime_delegation_topology(
        runtime_topology=engine.org_engine.build_runtime_delegation_topology(),
        decision=engine._deserialize_router_decision(payload["decision"]),
        project_id=engine.project_id, role_agent_overrides=overrides, compiled_external_teams=teams,
    )
    topology = apply_external_team_bindings_to_topology(engine.org_engine, topology, compiled_bindings=teams)
    plan = engine.org_engine.build_company_work_item_runtime_plan(
        engine.config.org.company_profile, runtime_topology=topology,
        original_request="Build and test", compiled_external_teams=teams,
    )
    return teams, topology, plan


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("agent", ["native", "codex", "jiuwen"])
def test_switching_off_configured_teams_restores_real_execution_seats(tmp_path, custom, agent):
    engine = make_engine(tmp_path, custom=custom)
    payload = make_payload(engine)
    before = engine.config.org.model_dump()
    agents = {role["role_id"]: agent for role in payload["staffing_roles"]}
    teams, topology, plan = execution_plan(engine, payload, agents)
    assert not teams
    assert not any(role.get("staffing_locked") for role in payload["staffing_roles"])
    assert {role.role_id for role in engine.org_engine.list_agents()} == set(agents)
    assert all(seat["selected_execution_agent"] == agent for seat in topology["seats"])
    assert not any(p.metadata.get("execution_unit_kind") == "opaque_external_team" for p in plan.projections)
    assert engine.config.org.model_dump() == before


def test_team_selection_still_collapses_only_selected_subtree(tmp_path):
    engine = make_engine(tmp_path)
    payload = make_payload(engine, "native")
    assert all(r["selected_agent"] == "native" for r in payload["staffing_roles"])
    agents = dict(payload["recruitment_role_agents"], cto="jiuwenswarm")
    before = engine.config.org.model_dump()
    teams, topology, plan = execution_plan(engine, payload, agents)
    assert [t.boundary_role_id for t in teams] == ["cto"]
    assert teams[0].max_inflight == 2
    covered = set(teams[0].covered_role_ids)
    assert not (covered - {"cto"}) & {p.role_id for p in plan.projections}
    assert next(s for s in topology["seats"] if s["role_id"] == "cto" and s.get("dispatchable"))["selected_execution_agent"] == "jiuwenswarm"
    assert engine.config.org.model_dump() == before
    # A different run can use Native without changing the first run or defaults.
    assert not execution_plan(engine, payload, payload["recruitment_role_agents"])[0]
    assert teams[0].boundary_role_id == "cto"


def test_disabled_default_can_be_selected_and_nested_teams_do_not_overlap(tmp_path):
    engine = make_engine(tmp_path)
    engine.config.org.external_team_bindings[0].enabled = False
    payload = make_payload(engine, "native")
    agents = dict(payload["recruitment_role_agents"], cto="jiuwenswarm", senior_engineer="jiuwenswarm")
    teams, _, _ = execution_plan(engine, payload, agents)
    assert [t.boundary_role_id for t in teams] == ["cto"]
    assert engine.config.org.external_team_bindings[0].enabled is False
    agents["cto"] = "native"
    assert [t.boundary_role_id for t in execution_plan(engine, payload, agents)[0]] == ["senior_engineer"]


def legacy_payload(engine):
    payload = make_payload(engine)
    teams, _ = engine._apply_staffing_external_team_bindings(payload, {})
    covered = {role for team in teams for role in team.covered_role_ids}
    boundaries = {team.boundary_role_id: team for team in teams}
    payload["staffing_roles"] = [r for r in payload["staffing_roles"] if r["role_id"] not in covered or r["role_id"] in boundaries]
    for role in payload["staffing_roles"]:
        if role["role_id"] in boundaries:
            role.update(staffing_locked=True, staffing_mode="opaque_external_team",
                        covered_role_ids=list(boundaries[role["role_id"]].covered_role_ids),
                        default_selection={"kind": "fallback"}, same_role_employee_ids=[])
    payload["recruitment_role_agents"] = {r["role_id"]: r["selected_agent"] for r in payload["staffing_roles"]}
    return payload


def test_legacy_pending_card_expands_without_database_mutation(tmp_path):
    async def run():
        engine = make_engine(tmp_path)
        payload = legacy_payload(engine)
        original = copy.deepcopy(payload)
        checkpoint = ExecutionCheckpoint(checkpoint_type="company_staffing_selection", session_id="staffing-session", payload=payload)
        engine.get_latest_pending_checkpoint_for_session = AsyncMock(return_value=checkpoint)
        snapshot = await _build_snapshot_checkpoint_meta(engine, SimpleNamespace(session_id="staffing-session"))
        handler = object.__new__(WSHandler)
        handler.engine = engine
        live = handler._build_staffing_selection_meta(checkpoint)
        assert snapshot["staffing_roles"] == live["staffing_roles"]
        assert len(snapshot["staffing_roles"]) == 11
        assert not any(r.get("staffing_locked") for r in snapshot["staffing_roles"])
        assert payload == original
        expanded = engine._editable_manual_staffing_payload(payload)
        assert engine._editable_manual_staffing_payload(expanded) == expanded
        assert not execution_plan(engine, expanded, {r["role_id"]: "native" for r in expanded["staffing_roles"]})[0]
        checkpoint.status = "resolved"
        historical = handler._build_staffing_selection_meta(checkpoint)
        assert len(historical["staffing_roles"]) == 4
        assert historical["staffing_roles"][1]["staffing_locked"] is True
    asyncio.run(run())


def test_legacy_manual_approval_restores_all_roles_without_changing_org_bindings(tmp_path):
    async def run():
        engine = make_engine(tmp_path)
        payload = legacy_payload(engine)
        original = copy.deepcopy(payload)
        bindings = [b.model_dump() for b in engine.config.org.external_team_bindings]
        engine.store = SimpleNamespace()
        engine._mark_session_recruitment_confirmation_completed = AsyncMock()
        engine._save_project_company_staffing_defaults = MagicMock()
        engine._continue_company_mode_execution = AsyncMock(return_value="ready")
        native = {role.role_id: "native" for role in engine.org_engine.list_agents()}
        checkpoint = ExecutionCheckpoint(checkpoint_type="company_staffing_selection", payload=payload)
        result = await engine._resume_staffing_selection_checkpoint(checkpoint, "approve", reply_metadata={
            "staffing_action": "manual_approve", "recruitment_role_agents": native,
            "staffing_selections": {role: {"kind": "fallback"} for role in native},
        })
        assert result == "ready"
        continued = engine._continue_company_mode_execution.call_args.kwargs
        assert continued["role_agent_overrides"] == native
        assert set(continued["fallback_role_ids"]) == set(native)
        assert checkpoint.payload == original
        assert [b.model_dump() for b in engine.config.org.external_team_bindings] == bindings
        assert not execution_plan(engine, make_payload(engine), native)[0]
    asyncio.run(run())


def test_explicit_session_agent_overrides_bindings_before_execution(tmp_path):
    engine = make_engine(tmp_path)
    decision = RouterDecision(mode=ExecutionMode.COMPANY_MODE, company_profile="corporate", preferred_agent="native", domains=[])
    agents = engine._company_execution_agent_defaults(decision, {"cmo": "jiuwenswarm"})
    teams, _, _ = execution_plan(engine, make_payload(engine), agents)
    assert [team.boundary_role_id for team in teams] == ["cmo"]
    assert agents["cto"] == "native"


def project_engine(engine, project_id):
    """Exercise production defaults I/O with a shared organization config."""
    other = OPCEngine(config=engine.config, opc_home=engine.opc_home, project_id=project_id)
    other.org_engine = OrgEngine(other.config, other.opc_home)
    other.talent_market = TalentMarket(other.opc_home, other.config)
    other.adapter_registry = engine.adapter_registry
    return other


def save_agent_defaults(engine, payload, agents):
    engine._save_project_company_staffing_defaults(
        engine._deserialize_router_decision(payload["decision"]),
        company_profile=engine.config.org.company_profile, role_ids=set(agents),
        staffing_overrides={}, staffing_experience_modes={},
        fallback_role_ids=set(agents), role_agent_overrides=agents,
    )


@pytest.mark.parametrize("custom", [False, True])
def test_new_projects_do_not_inherit_another_projects_executor_choices(tmp_path, custom):
    seed = make_engine(tmp_path, custom=custom)
    first = project_engine(seed, "experiment-alpha")
    second = project_engine(seed, "experiment-beta")
    config_before = seed.config.org.model_dump()
    initial = make_payload(first, session_id="alpha-session-1")
    native = {role["role_id"]: "native" for role in initial["staffing_roles"]}
    save_agent_defaults(first, initial, native)

    first_card = make_payload(first, session_id="alpha-session-2")
    second_card = make_payload(second, session_id="beta-session-1")
    assert first_card["recruitment_role_agents"] == native
    assert second_card["recruitment_role_agents"]["cto"] == "jiuwenswarm"
    for card in [first_card, second_card]:
        assert len(card["staffing_roles"]) == len(native)
        assert not any(role.get("staffing_locked") for role in card["staffing_roles"])
    first_teams, first_topology, _ = execution_plan(first, first_card, native)
    first_snapshot = copy.deepcopy(first_topology)
    second_teams, _, _ = execution_plan(second, second_card, second_card["recruitment_role_agents"])
    assert first_teams == []
    assert "cto" in {team.boundary_role_id for team in second_teams}
    assert not execution_plan(second, second_card, native)[0]
    assert first_topology == first_snapshot
    assert seed.config.org.model_dump() == config_before
    assert first._project_company_staffing_defaults_path().is_file()
    assert not second._project_company_staffing_defaults_path().exists()


@pytest.mark.parametrize("custom", [False, True])
def test_new_sessions_and_engine_reload_keep_saved_teams_editable(tmp_path, custom):
    seed = make_engine(tmp_path, custom=custom)
    engine = project_engine(seed, "experiment-sessions")
    initial = make_payload(engine, session_id="session-1")
    native = {role["role_id"]: "native" for role in initial["staffing_roles"]}
    mixed = dict(native, cto="jiuwenswarm")
    save_agent_defaults(engine, initial, mixed)
    next_session = make_payload(engine, session_id="session-2")
    assert next_session["recruitment_role_agents"] == mixed
    assert not any(role.get("staffing_locked") for role in next_session["staffing_roles"])
    assert not execution_plan(engine, next_session, native)[0]
    save_agent_defaults(engine, next_session, native)

    reloaded = project_engine(seed, "experiment-sessions")
    restored = make_payload(reloaded, session_id="session-3")
    assert restored["recruitment_role_agents"] == native
    assert not any(role.get("staffing_locked") for role in restored["staffing_roles"])
    assert not execution_plan(reloaded, restored, native)[0]
    # A subsequent per-role Team choice still works after persisting Native.
    assert [team.boundary_role_id for team in execution_plan(reloaded, restored, mixed)[0]] == ["cto"]
    save_agent_defaults(reloaded, restored, mixed)
    explicit_native = make_payload(reloaded, "native", session_id="session-4")
    assert explicit_native["recruitment_role_agents"] == native


@pytest.mark.parametrize("keep_team", [False, True])
def test_auto_recruit_and_confirmation_preserve_run_choices(tmp_path, keep_team):
    async def run():
        engine = make_engine(tmp_path)
        async def recruit(prompt, **kwargs):
            data = json.loads(prompt)
            if "category_catalog" in data:
                return json.dumps({"roles": [
                    {"role_id": r["role_id"], "action": "direct_role_execution", "categories": []}
                    for r in data["roles"]
                ]})
            return json.dumps({"proposals": [
                {"role_id": r["role_id"], "status": "direct_role_execution"}
                for r in data["roles"]
            ]})
        planner = SimpleNamespace(simple_chat=recruit)
        engine.company_recruiter.llm = planner
        engine._resolve_recruitment_llm = lambda agent: (planner, agent)
        payload = make_payload(engine, "native")
        agents = dict(payload["recruitment_role_agents"])
        if keep_team:
            agents["cto"] = "jiuwenswarm"
        engine._session_has_completed_recruitment_confirmation = AsyncMock(return_value=False)
        engine._save_execution_checkpoint = AsyncMock()
        triage = engine.company_recruiter._triage_staffing_for_needs
        engine.company_recruiter._triage_staffing_for_needs = AsyncMock(wraps=triage)
        await engine._begin_company_recruitment_loop(
            engine._deserialize_router_decision(payload["decision"]), payload["original_message"],
            CompanyRuntimeSpec(profile="corporate"), session_id="staffing-session",
            origin_channel="ui", origin_chat_id="", origin_thread_id="",
            force_confirmation=True, role_agent_overrides=agents,
        )
        saved = engine._save_execution_checkpoint.call_args.args[0]["payload"]
        assert saved["recruitment_role_agents"] == agents
        needs = engine.company_recruiter._triage_staffing_for_needs.call_args.args[0]
        assert ("senior_engineer" in {need.role_id for need in needs}) is not keep_team
        assert len(saved["recruitment_plan"]["proposals"]) == 11
        # Switching Team off at recruitment confirmation must honor the new
        # choices too, including roles intentionally skipped by the recruiter.
        engine.store = SimpleNamespace()
        engine._mark_session_recruitment_confirmation_completed = AsyncMock()
        engine._save_project_company_staffing_defaults = MagicMock()
        engine._continue_company_mode_execution = AsyncMock(return_value="ready")
        checkpoint = ExecutionCheckpoint(checkpoint_type="company_recruitment_confirmation", payload=saved)
        native = {r["role_id"]: "native" for r in payload["staffing_roles"]}
        await engine._resume_recruitment_checkpoint(checkpoint, "approve", reply_metadata={
            "recruitment_role_agents": native,
            "staffing_selections": {role: {"kind": "fallback"} for role in native},
        })
        continued = engine._continue_company_mode_execution.call_args.kwargs
        assert continued["role_agent_overrides"] == native
        assert set(continued["fallback_role_ids"]) == set(native)
        assert not execution_plan(engine, payload, continued["role_agent_overrides"])[0]
    asyncio.run(run())
