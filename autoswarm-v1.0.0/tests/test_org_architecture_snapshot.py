from __future__ import annotations

import yaml

from opc.core.config import (
    CommunicationPolicyConfig,
    EmployeeConfig,
    OPCConfig,
    RoleConfig,
    RuntimePolicyConfig,
    SeatConfig,
    TalentTemplateConfig,
    TeamConfig,
)
from opc.plugins.office_ui.org_architecture_snapshot import (
    ORG_ARCHITECTURE_KIND,
    ORG_ARCHITECTURE_SCHEMA_VERSION,
    apply_org_architecture_snapshot,
    build_active_company_payload,
    build_active_org_runtime_payload,
    build_org_architecture_snapshot,
    dump_org_architecture_snapshot,
    parse_org_architecture_snapshot,
)


def _role(role_id: str = "supervisor") -> RoleConfig:
    return RoleConfig(
        id=role_id,
        name=role_id.title(),
        responsibility="Coordinate the work.",
        reports_to="owner",
        can_spawn=["student"],
        tools=["file_read"],
        prompt_refs=["Lead carefully."],
    )


def _rich_custom_config() -> OPCConfig:
    cfg = OPCConfig()
    cfg.org.company_name = "Research Lab"
    cfg.org.company_profile = "custom"
    cfg.org.final_decider_role_id = "supervisor"
    cfg.org.roles = [_role(), _role("student")]
    cfg.org.employees = [
        EmployeeConfig(
            employee_id="student-researcher",
            template_id="researcher",
            name="Researcher",
            role_id="student",
            prompt_refs=["Think scientifically."],
        ),
    ]
    cfg.org.talent_templates = [
        TalentTemplateConfig(
            id="researcher",
            name="Researcher",
            description="Does research.",
            category="research",
            prompt_ref="Think scientifically.",
        ),
    ]
    cfg.org.runtime_policies["custom"] = RuntimePolicyConfig(
        communication=CommunicationPolicyConfig(default_mode="broadcast"),
    )
    cfg.org.teams = [
        TeamConfig(
            team_id="research",
            name="Research",
            seats=[
                SeatConfig(
                    seat_id="lead",
                    role_id="supervisor",
                    seat_kind="lead",
                ),
            ],
        ),
    ]
    cfg.org.installed_packages = [
        {
            "package_id": "research-pack",
            "name": "Research Pack",
            "version": "1.0.0",
            "role_ids": ["supervisor", "student"],
        },
    ]
    cfg.org.role_serial_queue_enabled = False
    return cfg


def test_v2_snapshot_contains_complete_custom_architecture() -> None:
    cfg = _rich_custom_config()

    snapshot = build_org_architecture_snapshot(cfg, force_profile="custom")
    parsed = parse_org_architecture_snapshot(dump_org_architecture_snapshot(snapshot))

    assert parsed["schema_version"] == ORG_ARCHITECTURE_SCHEMA_VERSION
    assert parsed["kind"] == ORG_ARCHITECTURE_KIND
    assert parsed["company"]["company_profile"] == "custom"
    assert parsed["runtime_policies"]["custom"]["communication"]["default_mode"] == "broadcast"
    assert parsed["talent_templates"] == []
    assert parsed["teams"][0]["team_id"] == "research"
    assert parsed["installed_packages"][0]["package_id"] == "research-pack"
    assert parsed["role_serial_queue_enabled"] is False


def test_v2_snapshot_replaces_runtime_extras_instead_of_inheriting_stale_state() -> None:
    existing = _rich_custom_config()
    existing.org.talent_templates[0].id = "stale-template"
    existing.org.runtime_policies["custom"] = RuntimePolicyConfig(
        communication=CommunicationPolicyConfig(default_mode="broadcast"),
    )

    snapshot = {
        "schema_version": 2,
        "kind": ORG_ARCHITECTURE_KIND,
        "company": {
            "name": "Clean",
            "topology": "Flat",
            "company_profile": "custom",
            "final_decider_role_id": "lead",
            "company_profiles": ["corporate", "custom"],
        },
        "roles": [
            {
                "id": "lead",
                "name": "Lead",
                "responsibility": "Decide.",
                "reports_to": "owner",
            },
        ],
        "employees": [],
        "escalation_rules": [],
        "runtime_policies": {},
        "talent_templates": [],
        "teams": [],
        "team_runtime": {},
        "installed_packages": [],
        "role_serial_queue_enabled": True,
    }

    applied = apply_org_architecture_snapshot(existing, parse_org_architecture_snapshot(yaml.dump(snapshot)))

    assert applied.org.company_name == "Clean"
    assert [role.id for role in applied.org.roles] == ["lead"]
    assert applied.org.talent_templates == []
    assert applied.org.runtime_policies == {}
    assert applied.org.installed_packages == []


def test_v1_snapshot_preserves_unspecified_runtime_extras_except_talent_catalog() -> None:
    existing = _rich_custom_config()
    v1_snapshot = {
        "schema_version": 1,
        "company": {
            "name": "Legacy",
            "topology": "Tree",
            "company_profile": "custom",
            "final_decider_role_id": "legacy-lead",
            "company_profiles": ["custom"],
        },
        "roles": [
            {
                "id": "legacy-lead",
                "name": "Legacy Lead",
                "responsibility": "Lead.",
                "reports_to": "owner",
            },
        ],
        "employees": [],
        "escalation_rules": [],
    }

    applied = apply_org_architecture_snapshot(
        existing,
        parse_org_architecture_snapshot(yaml.dump(v1_snapshot)),
    )

    assert applied.org.company_name == "Legacy"
    assert [role.id for role in applied.org.roles] == ["legacy-lead"]
    assert applied.org.talent_templates == []
    assert "custom" in applied.org.runtime_policies
    assert applied.org.installed_packages[0]["package_id"] == "research-pack"


def test_active_config_payloads_keep_structure_and_runtime_extras_split() -> None:
    cfg = _rich_custom_config()

    company_payload = build_active_company_payload(cfg, force_profile="custom")
    runtime_payload = build_active_org_runtime_payload(cfg)

    assert company_payload["schema_version"] == 1
    assert company_payload["company"]["company_profile"] == "custom"
    assert "roles" in company_payload
    assert "runtime_policies" not in company_payload

    assert "roles" not in runtime_payload
    assert runtime_payload["runtime_policies"]["custom"]["communication"]["default_mode"] == "broadcast"
    assert runtime_payload["talent_templates"] == []
    assert runtime_payload["role_serial_queue_enabled"] is False


def test_custom_clone_can_match_corporate_runtime_policy_when_policy_is_saved() -> None:
    from opc.layer2_organization.company_runtime_profiles import (
        get_builtin_roles,
        get_builtin_runtime_policies,
    )
    from opc.layer2_organization.org_engine import OrgEngine

    corporate = OPCConfig()
    corporate.org.company_profile = "corporate"

    custom = OPCConfig()
    custom.org.company_profile = "custom"
    custom.org.final_decider_role_id = "ceo"
    custom.org.roles = [role.model_copy(deep=True) for role in get_builtin_roles("corporate")]
    custom.org.runtime_policies["custom"] = get_builtin_runtime_policies()["corporate"]

    assert OrgEngine(custom).get_runtime_policy("custom") == OrgEngine(corporate).get_runtime_policy("corporate")
