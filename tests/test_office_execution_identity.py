from __future__ import annotations

from types import SimpleNamespace

import pytest

from opc.core.execution_agents import EXECUTION_AGENTS, EXTERNAL_EXECUTION_AGENTS
from opc.layer3_agent.adapters.registry import ADAPTER_CLASSES
from opc.plugins.office_ui.execution_identity import (
    PREFERRED_AGENTS,
    canonicalize_execution_identity,
    execution_identity_from_task,
)


@pytest.mark.parametrize("mode", ["company", "org"])
@pytest.mark.parametrize("selected", ["", "native", "claude_code"])
def test_company_chat_agent_selection_survives_legacy_empty_or_conflicting_runtime_fields(mode, selected):
    from opc.plugins.office_ui.services.session import SessionService
    from opc.plugins.office_ui.snapshot_builder import _resolve_task_selected_execution_agent

    task = SimpleNamespace(
        id="ui-root", session_id="chat", parent_session_id=None, assigned_to=None,
        assigned_external_agent=None, org_id="team" if mode == "org" else None,
        metadata={"exec_mode": mode, "company_profile": "custom" if mode == "org" else "corporate",
                  "preferred_agent": "codex", "selected_execution_agent": selected},
    )
    assert _resolve_task_selected_execution_agent(task) == "codex"
    assert SessionService(SimpleNamespace()).resolve_task_selected_execution_agent(task) == "codex"


@pytest.mark.parametrize("agent", ["native", "codex", "claude_code"])
def test_company_role_agent_is_independent_of_primary_chat_preference(agent):
    from opc.plugins.office_ui.services.session import SessionService
    from opc.plugins.office_ui.snapshot_builder import _resolve_task_selected_execution_agent

    task = SimpleNamespace(
        id="role-task", session_id="role-chat", parent_session_id="root-chat",
        assigned_external_agent=None,
        metadata={"company_profile": "custom", "preferred_agent": "codex",
                  "work_item_runtime": True, "work_item_role_id": "reviewer",
                  "selected_execution_agent": agent},
    )
    assert _resolve_task_selected_execution_agent(task) == agent
    assert SessionService(SimpleNamespace()).resolve_task_selected_execution_agent(task) == agent


def test_company_identity_clears_stale_custom_org_fields() -> None:
    identity = canonicalize_execution_identity(
        exec_mode="company",
        company_profile="custom",
        org_id="quantum_harbor",
        preferred_agent="codex",
        explicit_exec_mode=True,
    )

    assert identity.exec_mode == "company"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""
    assert identity.preferred_agent == "codex"


def test_legacy_custom_profile_becomes_custom_org_identity() -> None:
    identity = canonicalize_execution_identity(
        company_profile="custom",
        org_id="quantum_harbor",
        explicit_exec_mode=False,
    )

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == "quantum_harbor"


def test_custom_exec_mode_is_canonical_org_identity() -> None:
    identity = canonicalize_execution_identity(
        exec_mode="custom",
        company_profile="corporate",
        org_id="quantum_harbor",
        explicit_exec_mode=True,
    )

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == "quantum_harbor"


def test_task_identity_clears_company_and_org_fields() -> None:
    identity = canonicalize_execution_identity(
        exec_mode="task",
        company_profile="custom",
        org_id="quantum_harbor",
        preferred_agent="claude-code",
        explicit_exec_mode=True,
    )

    assert identity.exec_mode == "task"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""
    assert identity.preferred_agent == "claude_code"


def test_jiuwen_agents_are_valid_task_execution_identities() -> None:
    for agent in ("jiuwen", "jiuwenswarm"):
        assert agent in PREFERRED_AGENTS
        identity = canonicalize_execution_identity(
            exec_mode="task",
            preferred_agent=agent,
            explicit_exec_mode=True,
        )
        assert identity.preferred_agent == agent


def test_office_identity_allowlist_matches_external_adapter_registry() -> None:
    assert PREFERRED_AGENTS == EXECUTION_AGENTS
    assert frozenset(ADAPTER_CLASSES) == EXTERNAL_EXECUTION_AGENTS


def test_task_metadata_identity_prefers_explicit_company_over_stale_custom_profile() -> None:
    task = SimpleNamespace(
        metadata={
            "exec_mode": "company",
            "company_profile": "custom",
            "org_id": "quantum_harbor",
            "preferred_agent": "codex",
        },
        org_id="quantum_harbor",
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "company"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""
    assert identity.preferred_agent == "codex"


def test_task_metadata_identity_keeps_custom_org_id() -> None:
    task = SimpleNamespace(
        metadata={
            "exec_mode": "org",
            "company_profile": "custom",
            "organization_id": "quantum_harbor",
        },
        org_id=None,
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == "quantum_harbor"


def test_task_org_id_field_is_org_identity_fallback() -> None:
    task = SimpleNamespace(
        metadata={},
        org_id="quantum_harbor",
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == "quantum_harbor"


def test_durable_task_org_id_wins_over_stale_metadata_org_id() -> None:
    task = SimpleNamespace(
        metadata={
            "exec_mode": "org",
            "company_profile": "custom",
            "organization_id": "active-org",
        },
        org_id="selected-org",
    )

    identity = execution_identity_from_task(task)

    assert identity.org_id == "selected-org"


def test_default_org_id_applies_only_when_task_has_no_persisted_identity() -> None:
    task = SimpleNamespace(metadata={}, org_id=None)

    identity = execution_identity_from_task(
        task,
        default_exec_mode="org",
        default_company_profile="custom",
        default_org_id="quantum_harbor",
    )

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == "quantum_harbor"


def test_explicit_company_identity_ignores_default_org_id() -> None:
    task = SimpleNamespace(
        metadata={
            "exec_mode": "company",
            "company_profile": "corporate",
        },
        org_id=None,
    )

    identity = execution_identity_from_task(
        task,
        default_exec_mode="org",
        default_company_profile="custom",
        default_org_id="quantum_harbor",
    )

    assert identity.exec_mode == "company"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""


def test_company_mode_marker_without_profile_is_company_identity() -> None:
    task = SimpleNamespace(
        metadata={"mode": "company"},
        org_id=None,
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "company"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""


def test_work_item_runtime_marker_without_profile_is_company_identity() -> None:
    task = SimpleNamespace(
        metadata={"work_item_runtime": True},
        org_id=None,
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "company"
    assert identity.company_profile == "corporate"
    assert identity.org_id == ""


def test_company_runtime_marker_with_custom_profile_is_org_identity() -> None:
    task = SimpleNamespace(
        metadata={"mode": "company", "work_item_runtime": True, "company_profile": "custom"},
        org_id=None,
    )

    identity = execution_identity_from_task(task)

    assert identity.exec_mode == "org"
    assert identity.company_profile == "custom"
    assert identity.org_id == ""
