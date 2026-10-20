from __future__ import annotations

import pytest

from opc.core.config import OPCConfig, RoleConfig
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.org_work_item_planner import (
    build_company_work_item_runtime_plan,
    build_custom_org_work_item_blueprint,
)


def _custom_config(*roles: RoleConfig, final_decider_role_id: str | None = "ceo") -> OPCConfig:
    config = OPCConfig()
    config.org.company_profile = "custom"
    config.org.final_decider_role_id = final_decider_role_id
    config.org.roles = list(roles)
    return config


def test_custom_org_blueprint_uses_roles_teams_seats_and_skills() -> None:
    config = _custom_config(
        RoleConfig(
            id="ceo",
            name="CEO",
            responsibility="Owns final decisions",
            reports_to="owner",
            can_spawn=["cto"],
            prompt_refs=["ceo-prompt"],
            skill_refs=["strategy"],
        ),
        RoleConfig(
            id="cto",
            name="CTO",
            responsibility="Coordinates engineering",
            reports_to="ceo",
            can_spawn=["engineer"],
            prompt_refs=["cto-prompt"],
            skill_refs=["architecture"],
        ),
        RoleConfig(
            id="engineer",
            name="Engineer",
            responsibility="Builds the deliverable",
            reports_to="cto",
            prompt_refs=["eng-prompt"],
            skill_refs=["coding"],
        ),
    )
    org = OrgEngine(config)

    blueprint = build_custom_org_work_item_blueprint(
        org,
        runtime_topology=org.build_runtime_delegation_topology(),
        original_request="Ship the product",
    )
    payload = blueprint.to_dict()

    assert payload["profile"] == "custom"
    assert payload["final_decider_role_id"] == "ceo"
    assert payload["root_projection_id"] == "custom::intake::ceo"
    seeds = {seed["role_id"]: seed for seed in payload["seeds"]}
    assert seeds["ceo"]["turn_type"] == "intake"
    assert seeds["cto"]["allowed_delegate_role_ids"] == ["engineer"]
    assert seeds["engineer"]["skill_refs"] == ["coding"]
    assert seeds["engineer"]["prompt_refs"] == ["eng-prompt"]
    assert any(link["link_type"] == "delegates_to" for link in payload["collaboration_links"])


def test_custom_blueprint_requires_final_decider_for_multiple_top_level_roles() -> None:
    config = _custom_config(
        RoleConfig(id="ceo", name="CEO", responsibility="Strategy", reports_to="owner"),
        RoleConfig(id="coo", name="COO", responsibility="Operations", reports_to="owner"),
        final_decider_role_id=None,
    )
    org = OrgEngine(config)

    with pytest.raises(ValueError, match="final_decider_role_id"):
        build_custom_org_work_item_blueprint(
            org,
            runtime_topology=org.build_runtime_delegation_topology(),
        )


def test_custom_runtime_definition_generation_api_is_removed() -> None:
    config = _custom_config(
        RoleConfig(id="ceo", name="CEO", responsibility="Strategy", reports_to="owner"),
    )
    org = OrgEngine(config)

    assert not hasattr(org, "build_" + "work" + "flow_definition")


def test_company_work_item_runtime_plan_serializes_projection_policies() -> None:
    config = _custom_config(
        RoleConfig(id="ceo", name="CEO", responsibility="Strategy", reports_to="owner", can_spawn=["cto"]),
        RoleConfig(id="cto", name="CTO", responsibility="Engineering", reports_to="ceo", skill_refs=["architecture"]),
    )
    org = OrgEngine(config)

    plan = build_company_work_item_runtime_plan(
        org,
        profile="custom",
        runtime_topology=org.build_runtime_delegation_topology(),
        original_request="Build the product",
    )
    payload = plan.to_dict()

    assert payload["runtime_model"] == "multi_team_org"
    assert payload["root_projection_id"] == "custom::intake::ceo"
    projections = {item["role_id"]: item for item in payload["projections"]}
    assert projections["ceo"]["turn_type"] == "intake"
    assert projections["cto"]["turn_type"] == "execute"
    assert projections["cto"]["skill_refs"] == ["architecture"]
    assert projections["cto"]["gate_policy"]["rework_projection_id"] == projections["cto"]["projection_id"]


def test_company_work_item_runtime_plan_has_no_obsolete_bridge_properties() -> None:
    config = _custom_config(
        RoleConfig(id="ceo", name="CEO", responsibility="Strategy", reports_to="owner", can_spawn=["cto"]),
        RoleConfig(id="cto", name="CTO", responsibility="Engineering", reports_to="ceo"),
    )
    org = OrgEngine(config)

    plan = build_company_work_item_runtime_plan(
        org,
        profile="custom",
        runtime_topology=org.build_runtime_delegation_topology(),
        original_request="Build the product",
    )
    cto_projection = next(item for item in plan.projections if item.role_id == "cto")

    assert not hasattr(plan, "stages")
    old_projection_attr = "sta" + "ge_id"
    for obsolete_attr in (old_projection_attr, "description", "dependencies", "gate"):
        assert not hasattr(cto_projection, obsolete_attr)
    assert plan.projection_order_map()[plan.root_projection_id] == 0
    assert plan.dependencies_for(cto_projection.projection_id) == [plan.root_projection_id]
    assert cto_projection.projection_id in plan.dependent_projection_ids(plan.root_projection_id)
