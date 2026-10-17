from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import yaml

from opc.core.config import OPCConfig
from opc.core.org_config import (
    apply_org_config_payload_to_config,
    allocate_org_config_id,
    load_org_config_payload,
    org_config_path,
    write_org_index,
    write_runtime_org_config_payload,
)
from opc.plugins.office_ui.services.context import ModeState, OfficeServiceContext
from opc.plugins.office_ui.services.org import OrgService
from tests.test_org_saved_crud import _write_saved_org


def _service(opc_home, *, active_name="", config=None):
    return OrgService(SimpleNamespace(
        opc_home=opc_home, engine=SimpleNamespace(config=config or OPCConfig()),
        get_active_saved_org_name=AsyncMock(return_value=active_name),
    ))


def _adaptive_payload(config_dir):
    seed = _write_saved_org(config_dir, "seed", organization_name="Task-adaptive team")
    payload = yaml.safe_load(seed.read_text())
    seed.unlink()
    payload["metadata"].update(
        adaptive_team_plan={"decision": "team"},
        adaptive_source_project_id="project05",
        adaptive_source_organization_id="corporate",
    )
    return payload


def test_old_adaptive_orgs_are_hidden_by_provenance_without_hiding_named_or_explicitly_saved_teams(tmp_path):
    config_dir = tmp_path / "config"
    _write_saved_org(config_dir, "manual", organization_name="Task-adaptive team")
    _write_saved_org(config_dir, "adaptive_manual", organization_name="Team")
    payload = _adaptive_payload(config_dir)
    for org_id, persistence in (("old_adaptive", None), ("explicitly_saved", "saved")):
        data = {**payload, "organization_id": org_id, "metadata": dict(payload["metadata"])}
        if persistence:
            data["metadata"]["organization_persistence"] = persistence
        org_config_path(config_dir, org_id).write_text(yaml.safe_dump(data))
    write_runtime_org_config_payload(config_dir, "adaptive_private", payload)
    before = org_config_path(config_dir, "old_adaptive").read_bytes()
    result = asyncio.run(_service(tmp_path, active_name="old_adaptive").saved_list()).payload
    assert {item["name"] for item in result["orgs"]} == {"manual", "adaptive_manual", "explicitly_saved"}
    assert result["active_name"] is None
    assert org_config_path(config_dir, "old_adaptive").read_bytes() == before
    # The historical session can still resolve its exact org after a restart.
    assert load_org_config_payload(config_dir, "old_adaptive")[0]["roles"] == payload["roles"]


def test_repeated_auto_teams_do_not_grow_saved_catalog_and_remain_reloadable(tmp_path):
    config_dir = tmp_path / "config"
    payload = _adaptive_payload(config_dir)
    payload["employees"] = [{"employee_id": "analyst", "name": "Analyst", "role_id": "role_0"}]
    for index in range(8):
        org_id = f"adaptive_session_{index}"
        path = write_runtime_org_config_payload(config_dir, org_id, payload)
        assert path.parent.name == "runtime_orgs"
        assert not org_config_path(config_dir, org_id).exists()
        restored, source = load_org_config_payload(config_dir, org_id)
        cfg = apply_org_config_payload_to_config(OPCConfig(), restored, source_path=source)
        assert cfg.org.organization_id == org_id
        assert cfg.org.organization_config_file == f"runtime_orgs/{path.name}"
        assert [role.id for role in cfg.org.roles] == ["role_0", "role_1"]
        assert [employee.employee_id for employee in cfg.org.employees] == ["analyst"]
    assert asyncio.run(_service(tmp_path).saved_list()).payload["orgs"] == []
    assert not (config_dir / "org_index.yaml").exists()
    assert allocate_org_config_id(config_dir, "adaptive_session_0") == "adaptive_session_0_2"


def test_runtime_config_save_keeps_snapshot_and_provenance_out_of_saved_org_index(tmp_path):
    config_dir = tmp_path / "config"
    payload = _adaptive_payload(config_dir)
    _write_saved_org(config_dir, "manual")
    write_org_index(config_dir, "manual")
    index_before = (config_dir / "org_index.yaml").read_bytes()
    path = write_runtime_org_config_payload(config_dir, "adaptive_runtime", payload)
    restored, source = load_org_config_payload(config_dir, "adaptive_runtime")
    cfg = apply_org_config_payload_to_config(OPCConfig(), restored, source_path=source)
    cfg.org.roles[0].responsibility = "Updated within this runtime."
    cfg.save(config_dir)
    current = yaml.safe_load(path.read_text())
    assert current["roles"][0]["responsibility"] == "Updated within this runtime."
    assert current["metadata"]["adaptive_source_project_id"] == "project05"
    assert current["metadata"]["adaptive_team_plan"] == payload["metadata"]["adaptive_team_plan"]
    assert current["metadata"]["organization_persistence"] == "session"
    assert not org_config_path(config_dir, "adaptive_runtime").exists()
    assert (config_dir / "org_index.yaml").read_bytes() == index_before


def test_explicit_save_as_promotes_a_snapshot_to_reusable_organization(tmp_path):
    async def run():
        config_dir = tmp_path / "config"
        path = write_runtime_org_config_payload(config_dir, "adaptive_runtime", _adaptive_payload(config_dir))
        restored, source = load_org_config_payload(config_dir, "adaptive_runtime")
        cfg = apply_org_config_payload_to_config(OPCConfig(), restored, source_path=source)
        engine = SimpleNamespace(opc_home=tmp_path, config=cfg, org_engine=MagicMock())
        context = OfficeServiceContext(
            engine=engine, agent_store=MagicMock(), chat_store=MagicMock(), event_adapter=MagicMock(),
            mode_state=ModeState(exec_mode="org", company_profile="custom"),
        )
        context.persist_runtime_config = lambda: engine.config.save(config_dir)
        service = OrgService(context)
        result = await service.saved_save_as("Reusable Research Team")
        saved = await service.saved_list()
        assert result.payload["ok"] is True
        assert [item["name"] for item in saved.payload["orgs"]] == ["reusable_research_team"]
        assert path.exists()
        assert yaml.safe_load(org_config_path(config_dir, "reusable_research_team").read_text())["metadata"]["organization_persistence"] == "saved"
    asyncio.run(run())
