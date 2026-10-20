from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import yaml

from opc.core.config import EmployeeConfig, OPCConfig, RoleConfig, TalentTemplateConfig
from opc.layer2_organization.org_engine import OrgEngine
from opc.layer2_organization.talent_market import TalentMarket
from opc.plugins.office_ui.ws_handler import WSHandler


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def _make_role(role_id: str, **kwargs) -> RoleConfig:
    return RoleConfig(
        id=role_id,
        name=kwargs.pop("name", role_id.replace("-", " ").title()),
        responsibility=kwargs.pop("responsibility", ""),
        **kwargs,
    )


def _make_template(template_id: str, *, prompt_ref: str = "") -> TalentTemplateConfig:
    return TalentTemplateConfig(
        id=template_id,
        name=template_id.replace("-", " ").title(),
        description=f"{template_id} description",
        category="general",
        prompt_ref=prompt_ref,
    )


def _make_default_emp(role_id: str) -> EmployeeConfig:
    return EmployeeConfig(
        employee_id=f"{role_id}-default-employee",
        template_id="general-default-employee",
        name=f"{role_id} Default Employee",
        role_id=role_id,
        prompt_refs=["prompts/talent/general-default-employee.md"],
        metadata={"is_default_employee": True, "auto_created_for_role": role_id},
    )


def _make_fallback_emp(role_id: str) -> EmployeeConfig:
    return EmployeeConfig(
        employee_id=f"{role_id}-fallback-empty-employee",
        template_id="fallback-empty-employee",
        name=f"{role_id} Fallback Empty Employee",
        role_id=role_id,
        metadata={
            "is_fallback_employee": True,
            "auto_created_for_role": role_id,
            "employee_origin": "recruitment_fallback",
        },
    )


def _make_handler(
    *,
    roles: list[RoleConfig] | None = None,
    employees: list[EmployeeConfig] | None = None,
    talent_templates: list[TalentTemplateConfig] | None = None,
    exec_mode: str = "custom",
    organization_id: str = "test_org",
    use_handler_persist: bool = False,
) -> tuple[WSHandler, OPCConfig]:
    cfg = OPCConfig()
    is_custom = exec_mode in {"custom", "org"}
    cfg.org.company_profile = "custom" if is_custom else "corporate"
    if is_custom:
        cfg.org.organization_id = organization_id
        cfg.org.organization_name = "Test Org"
        cfg.org.organization_config_file = f"company_orgs/org_{organization_id}_config.yaml"
    cfg.org.roles = roles or []
    cfg.org.employees = employees or []
    cfg.org.talent_templates = talent_templates or []
    object.__setattr__(cfg, "save", lambda config_dir=None: None)

    opc_home = Path(tempfile.mkdtemp(prefix="openopc-talent-hire-"))
    _write_talent_prompts(opc_home, talent_templates or [])
    org_engine = OrgEngine(cfg, opc_home, store=None)
    talent_market = TalentMarket(opc_home, cfg)

    handler = WSHandler.__new__(WSHandler)
    handler.engine = SimpleNamespace(
        config=cfg,
        org_engine=org_engine,
        talent_market=talent_market,
        opc_home=opc_home,
    )
    handler._exec_mode = exec_mode
    handler._company_profile = "custom" if is_custom else "corporate"
    handler._task_preferred_agent = "native"
    handler._local_talent_cache = None

    handler._config_lock = AsyncMock()
    handler._config_lock.__aenter__ = AsyncMock(return_value=None)
    handler._config_lock.__aexit__ = AsyncMock(return_value=None)

    handler._broadcast_org_info = AsyncMock()
    handler._sync_role_map = AsyncMock()
    handler.broadcast = AsyncMock()
    if not use_handler_persist:
        handler._persist_runtime_config = lambda: cfg.save(opc_home / "config")

    handler.agent_store = AsyncMock()
    handler.agent_store.create_agent_from_employee = AsyncMock(return_value={})
    handler.agent_store.ensure_custom_role_agents = AsyncMock(return_value=[])
    handler.agent_store.remove_agent = AsyncMock(return_value=None)
    handler.agent_store.sync_custom_shadow = AsyncMock()
    handler.agent_store.get_all = AsyncMock(return_value=[])

    return handler, cfg


def _write_talent_prompts(opc_home: Path, templates: list[TalentTemplateConfig]) -> None:
    talent_dir = opc_home / "prompts" / "talent"
    talent_dir.mkdir(parents=True, exist_ok=True)
    for template in templates:
        body = str(template.prompt_ref or f"# {template.name}\n")
        payload = {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "domains": list(template.domains),
            "tags": list(template.tags),
        }
        (talent_dir / f"{template.id}.md").write_text(
            "---\n"
            + yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + body.rstrip()
            + "\n",
            encoding="utf-8",
        )


def _last_direct_ack(ws: FakeWS) -> dict:
    assert ws.sent, "handler did not send any WS message"
    return ws.sent[-1]


class TalentHireHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_hire_into_unknown_role_returns_error(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            talent_templates=[_make_template("sarah")],
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "nonexistent_role"},
        )
        ack = _last_direct_ack(ws)
        self.assertEqual(ack["type"], "ack")
        self.assertFalse(ack["payload"]["ok"])
        self.assertIn("does not exist", ack["payload"]["error"])
        self.assertEqual([role.id for role in cfg.org.roles], ["supervisor"])
        self.assertEqual(cfg.org.employees, [])

    async def test_hire_replaces_default_employee(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[_make_default_emp("supervisor")],
            talent_templates=[_make_template("sarah", prompt_ref="I focus on growth")],
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "supervisor"},
        )
        ack = _last_direct_ack(ws)
        self.assertTrue(ack["payload"]["ok"])
        self.assertEqual(ack["payload"]["action"], "talent_hired")

        sup_emps = [employee for employee in cfg.org.employees if employee.role_id == "supervisor"]
        self.assertEqual(len(sup_emps), 1)
        self.assertFalse(sup_emps[0].metadata.get("is_default_employee"))
        self.assertEqual(sup_emps[0].template_id, "sarah")
        self.assertEqual(sup_emps[0].prompt_refs, ["prompts/talent/sarah.md"])
        handler.agent_store.remove_agent.assert_awaited_once_with(
            "emp-supervisor-default-employee",
        )

    async def test_hire_replaces_fallback_employee(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[_make_fallback_emp("supervisor")],
            talent_templates=[_make_template("sarah", prompt_ref="I focus on growth")],
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "supervisor"},
        )
        ack = _last_direct_ack(ws)
        self.assertTrue(ack["payload"]["ok"])
        self.assertEqual(ack["payload"]["action"], "talent_hired")

        sup_emps = [employee for employee in cfg.org.employees if employee.role_id == "supervisor"]
        self.assertEqual(len(sup_emps), 1)
        self.assertFalse(sup_emps[0].metadata.get("is_fallback_employee"))
        self.assertEqual(sup_emps[0].template_id, "sarah")
        handler.agent_store.remove_agent.assert_awaited_once_with(
            "emp-supervisor-fallback-empty-employee",
        )

    async def test_hire_preserves_existing_real_employee(self) -> None:
        existing_real = EmployeeConfig(
            employee_id="supervisor-bob",
            template_id="bob-template",
            name="Bob",
            role_id="supervisor",
            metadata={},
        )
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[existing_real],
            talent_templates=[_make_template("sarah")],
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "supervisor"},
        )
        ack = _last_direct_ack(ws)
        self.assertTrue(ack["payload"]["ok"])
        self.assertEqual(ack["payload"]["action"], "talent_hired")

        sup_emps = [employee for employee in cfg.org.employees if employee.role_id == "supervisor"]
        self.assertEqual({employee.employee_id for employee in sup_emps}, {"bob-template", "sarah"})
        handler.agent_store.remove_agent.assert_not_awaited()

    async def test_hire_into_empty_role_succeeds(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[],
            talent_templates=[_make_template("sarah")],
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "supervisor"},
        )
        ack = _last_direct_ack(ws)
        self.assertTrue(ack["payload"]["ok"])

        sup_emps = [employee for employee in cfg.org.employees if employee.role_id == "supervisor"]
        self.assertEqual(len(sup_emps), 1)
        handler.agent_store.remove_agent.assert_not_awaited()

    async def test_hire_persists_employee_registry_for_active_saved_org(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[],
            talent_templates=[_make_template("sarah")],
            organization_id="hkuds",
            use_handler_persist=True,
        )
        ws = FakeWS()
        await handler._handle_talent_hire(
            ws, {"template_id": "sarah", "role_id": "supervisor", "org_id": "hkuds"},
        )
        ack = _last_direct_ack(ws)
        self.assertTrue(ack["payload"]["ok"])

        opc_home = Path(handler.engine.opc_home)
        registry_path = opc_home / "company_state" / "hkuds" / "employees" / "sarah.yaml"
        self.assertTrue(registry_path.exists())
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["organization_id"], "hkuds")
        self.assertEqual(registry["employee"]["employee_id"], "sarah")
        self.assertEqual(registry["employee"]["role_id"], "supervisor")

        org_path = opc_home / "config" / "company_orgs" / "org_hkuds_config.yaml"
        org_payload = yaml.safe_load(org_path.read_text(encoding="utf-8"))
        self.assertEqual(org_payload.get("employees"), [])
        self.assertEqual([employee.employee_id for employee in cfg.org.employees], ["sarah"])
        handler.agent_store.ensure_custom_role_agents.assert_awaited()

    async def test_apply_saved_org_loads_external_employee_registry(self) -> None:
        handler, cfg = _make_handler(
            roles=[_make_role("supervisor")],
            employees=[],
            talent_templates=[_make_template("sarah")],
            organization_id="hkuds",
            use_handler_persist=True,
        )
        from opc.core.config import build_company_org_payload_from_config
        from opc.core.employee_registry import write_employee_registry

        opc_home = Path(handler.engine.opc_home)
        write_employee_registry(
            opc_home,
            "hkuds",
            [
                EmployeeConfig(
                    employee_id="sarah",
                    template_id="sarah",
                    name="Sarah",
                    role_id="supervisor",
                ),
            ],
        )
        raw_yaml = yaml.dump(
            build_company_org_payload_from_config(
                cfg,
                organization_id="hkuds",
                organization_name="HKUDS",
                force_profile="custom",
            ),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        ok, payload = await handler._apply_org_config(raw_yaml, dry_run=False, allow_mode_transition=True)

        self.assertTrue(ok, payload)
        self.assertEqual([employee.employee_id for employee in handler.engine.config.org.employees], ["sarah"])
        self.assertTrue((opc_home / "company_state" / "hkuds" / "employees" / "sarah.yaml").exists())


if __name__ == "__main__":
    unittest.main()
