"""Talent market service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opc.layer2_organization.talent_market import TalentMarket

from .context import OfficeServiceContext
from .models import ServiceError, ServiceEvent, ServiceResult


class TalentService:
    def __init__(self, context: OfficeServiceContext) -> None:
        self.context = context

    def _ensure_custom_org_editable(self) -> None:
        if not self.context.is_custom_org_editable():
            raise ServiceError(
                "org_read_only",
                "Corporate organization is read-only. Select or create a saved custom org before editing.",
            )

    @property
    def market(self) -> TalentMarket:
        return TalentMarket(self.context.opc_home, self.context.engine.config)

    async def list(self) -> ServiceResult:
        market = self.market
        templates = list(market.list_templates())
        known = {getattr(item, "id", "") for item in templates}
        try:
            for item in market.scan_local_talent():
                if getattr(item, "id", "") not in known:
                    templates.append(item)
                    known.add(getattr(item, "id", ""))
        except Exception:
            pass
        payloads = [self._template_payload(item) for item in templates]
        try:
            from opc.market.talent_presets import get_all_talent_presets
            for preset in get_all_talent_presets():
                preset_id = str(preset.get("id", "") or "")
                if preset_id and preset_id not in known:
                    payloads.append({
                        "template_id": preset_id,
                        "id": preset_id,
                        "name": preset.get("name", preset_id),
                        "description": preset.get("description", ""),
                        "category": preset.get("category", ""),
                        "domains": list(preset.get("domains", []) or []),
                        "tags": list(preset.get("tags", []) or []),
                        "preferred_external_agent": preset.get("preferred_external_agent"),
                        "source_repo": "builtin",
                        "emoji": preset.get("emoji", ""),
                        "color": preset.get("color", ""),
                        "vibe": preset.get("vibe", ""),
                    })
        except Exception:
            pass
        payloads.sort(key=lambda item: (str(item.get("category", "")), str(item.get("name", "")).lower()))
        return ServiceResult({"templates": payloads, "talent_dir": str(market.opc_home / "prompts" / "talent")})

    async def employees(self) -> ServiceResult:
        employees = self.market.list_employees()
        return ServiceResult({"employees": [self._employee_payload(item) for item in employees]})

    async def scan(self) -> ServiceResult:
        templates = self.market.scan_local_talent()
        return ServiceResult({"templates": [self._template_payload(item) for item in templates]})

    async def import_repo(self, path: str) -> ServiceResult:
        self._ensure_custom_org_editable()
        repo_path = Path(path).expanduser().resolve()
        if not repo_path.is_dir():
            raise ServiceError("directory_not_found", f"directory not found: {path}", {"path": path})
        imported = self.market.import_from_repo(repo_path)
        self._persist_config()
        return ServiceResult({"action": "talent_imported", "imported": [self._template_payload(item) for item in imported], "count": len(imported)})

    async def import_selected(self, template_ids: list[str]) -> ServiceResult:
        self._ensure_custom_org_editable()
        if not template_ids:
            raise ServiceError("missing_template_ids", "No templates selected")
        imported = self.market.import_local_templates(template_ids)
        self._persist_config()
        return ServiceResult({"count": len(imported), "imported": [self._template_payload(item) for item in imported]})

    async def _load_target_org_for_hire(self, organization_id: str | None = None) -> None:
        target_org_id = str(organization_id or "").strip()
        if not target_org_id and self.context.get_active_saved_org_name is not None:
            try:
                target_org_id = str(await self.context.get_active_saved_org_name() or "").strip()
            except Exception:
                target_org_id = ""
        if not target_org_id:
            return

        cfg_org = getattr(getattr(self.context.engine, "config", None), "org", None)
        current_org_id = str(getattr(cfg_org, "organization_id", "") or "").strip()
        current_profile = str(getattr(cfg_org, "company_profile", "") or "").strip().lower()
        if current_org_id == target_org_id and current_profile == "custom":
            return
        if self.context.load_active_org_config is None:
            return
        try:
            loaded = self.context.load_active_org_config(target_org_id)
        except Exception as exc:
            raise ServiceError(
                "saved_org_load_failed",
                f"Failed to load organization '{target_org_id}' before hiring.",
                {"organization_id": target_org_id},
            ) from exc
        if not loaded:
            raise ServiceError(
                "saved_org_not_found",
                f"Organization '{target_org_id}' is not available for hiring.",
                {"organization_id": target_org_id},
            )

    async def hire(
        self,
        *,
        template_id: str,
        role_id: str,
        employee_name: str | None = None,
        employee_id: str | None = None,
        organization_id: str | None = None,
    ) -> ServiceResult:
        await self._load_target_org_for_hire(organization_id)
        self._ensure_custom_org_editable()
        if not template_id or not role_id:
            raise ServiceError("missing_hire_fields", "template_id and role_id required")
        role_exists = any(
            str(getattr(role, "id", getattr(role, "role_id", "")) or "") == role_id
            for role in getattr(self.context.engine.config.org, "roles", []) or []
        )
        org = getattr(self.context.engine, "org_engine", None)
        if not role_exists and org and hasattr(org, "get_agent"):
            role_exists = bool(org.get_agent(role_id))
        if not role_exists:
            raise ServiceError("role_not_found", f"Role '{role_id}' does not exist", {"role_id": role_id})
        displaced_placeholder_ids = [
            item.employee_id
            for item in getattr(self.context.engine.config.org, "employees", []) or []
            if item.role_id == role_id
            and (
                dict(getattr(item, "metadata", {}) or {}).get("is_default_employee")
                or dict(getattr(item, "metadata", {}) or {}).get("is_fallback_employee")
            )
        ]
        try:
            employee = self.market.hire_template(template_id, role_id, employee_name=employee_name, employee_id=employee_id)
            self._persist_config()
        except Exception as exc:
            raise ServiceError("talent_hire_failed", str(exc), {"template_id": template_id, "role_id": role_id}) from exc

        for displaced_id in displaced_placeholder_ids:
            try:
                remover = getattr(self.context.agent_store, "remove_agent", None)
                if callable(remover):
                    await remover(f"emp-{displaced_id}")
            except Exception:
                pass

        employee_payload = self._employee_payload(employee)
        payload: dict[str, Any] = {
            "ok": True,
            "action": "talent_hired",
            "employee_id": employee.employee_id,
            "name": employee.name,
            "role_id": employee.role_id,
            "employee": employee_payload,
        }
        events: list[ServiceEvent] = []
        if self.context.mode_state.exec_mode in {"org", "custom"}:
            try:
                agents: list[dict[str, Any]] = []
                if self.context.ensure_custom_role_agents is not None:
                    agents = list(await self.context.ensure_custom_role_agents() or [])
                else:
                    creator = getattr(self.context.agent_store, "create_agent_from_employee", None)
                    if callable(creator):
                        await creator(employee_payload)
                if self.context.sync_role_map is not None:
                    await self.context.sync_role_map()
                sync = getattr(self.context.agent_store, "sync_custom_shadow", None)
                if callable(sync):
                    await sync()
                if not agents:
                    getter = getattr(self.context.agent_store, "get_all", None)
                    agents = await getter() if callable(getter) else []
                events.append(ServiceEvent("ack", {"ok": True, "action": "agent_spawned", "agents": agents}))
                payload["deploy_ok"] = True
            except Exception as exc:
                payload["deploy_ok"] = False
                payload["deploy_error"] = str(exc)
        return ServiceResult(payload, events)

    async def employee_detail(self, employee_id: str) -> ServiceResult:
        employee = next((item for item in self.market.list_employees() if item.employee_id == employee_id), None)
        if not employee:
            raise ServiceError("employee_not_found", "Employee not found", {"employee_id": employee_id})
        payload = self._employee_payload(employee)
        org = getattr(self.context.engine, "org_engine", None)
        evolution = getattr(org, "employee_evolution", None) if org else None
        if evolution:
            try:
                payload["experience_score"] = evolution.get_experience_score(
                    employee.employee_id,
                    role_id=employee.role_id,
                    domains=list(getattr(employee, "domains", []) or []),
                )
                payload["learned_skill_refs"] = evolution.get_learned_skill_refs(employee.employee_id)
                payload["delta_context"] = evolution.build_employee_delta_context(employee.employee_id)
                payload["profile"] = evolution.get_employee_profile(employee.employee_id)
            except Exception:
                pass
        return ServiceResult({"employee": payload})

    async def import_employee_as_agent(self, *, employee_id: str, office_id: str = "office-0") -> ServiceResult:
        from .agent import AgentService

        return await AgentService(self.context).import_employee(employee_id=employee_id, office_id=office_id)

    def _persist_config(self) -> None:
        if self.context.persist_runtime_config is not None:
            self.context.persist_runtime_config()
        else:
            self.context.engine.config.save()

    @staticmethod
    def _template_payload(template: Any) -> dict[str, Any]:
        return {
            "template_id": getattr(template, "id", ""),
            "id": getattr(template, "id", ""),
            "name": getattr(template, "name", ""),
            "description": getattr(template, "description", ""),
            "category": getattr(template, "category", ""),
            "domains": list(getattr(template, "domains", []) or []),
            "tags": list(getattr(template, "tags", []) or []),
            "preferred_external_agent": getattr(template, "preferred_external_agent", None),
            "source_repo": getattr(template, "source_repo", ""),
            "emoji": getattr(template, "emoji", "") or "",
            "color": getattr(template, "color", "") or "",
            "vibe": getattr(template, "vibe", "") or "",
        }

    @staticmethod
    def _employee_payload(employee: Any) -> dict[str, Any]:
        return {
            "employee_id": getattr(employee, "employee_id", ""),
            "name": getattr(employee, "name", ""),
            "role_id": getattr(employee, "role_id", ""),
            "category": getattr(employee, "category", ""),
            "domains": list(getattr(employee, "domains", []) or []),
            "seniority": getattr(employee, "seniority", "junior"),
            "status": getattr(employee, "status", "active"),
            "tags": list(getattr(employee, "tags", []) or []),
            "prompt_refs": list(getattr(employee, "prompt_refs", []) or []),
            "skill_refs": list(getattr(employee, "skill_refs", []) or []),
            "preferred_external_agent": getattr(employee, "preferred_external_agent", None),
        }
