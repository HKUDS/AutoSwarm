"""Custom/org runtime entrypoint.

Custom mode runs with company-mode semantics, but with a user-selected
organization config.  The runner owns the custom isolation boundary:

- company mode's engine/config objects are not mutated;
- the custom runtime uses its own OPCEngine instance and company executor;
- the runtime is rebound to the caller's store so UI session/task/transcript
  state remains in the same project context as the chat that started it.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, TYPE_CHECKING

from opc.core.config import OPCConfig
from opc.core.models import UserMessage
from opc.layer0_interaction.coordinator import InteractionDecisionLease
from opc.core.org_config import (
    apply_org_config_payload_to_config,
    load_org_config_payload,
    write_runtime_org_config_payload,
    validate_runnable_org_config,
)

if TYPE_CHECKING:
    from opc.engine import OPCEngine


class CustomRuntimeRunner:
    """Run custom/org turns through an isolated company-runtime engine."""

    def __init__(self, parent: OPCEngine) -> None:
        self.parent = parent
        self._runtime_ingress_tasks: dict[int, Any] = {}

    async def execute_adaptive_team(
        self, recruitment_plan: Any, decision: Any, runtime_spec: Any,
        original_message: str, *, staffing_selections: dict[str, Any] | None = None,
        role_agent_overrides: dict[str, str] | None = None, **execution_kwargs: Any,
    ) -> str:
        """Persist the approved roster and execute it in the existing org isolation boundary."""
        from opc.core.employee_registry import is_placeholder_employee
        from opc.core.models import RecruitmentEmployeeRecommendation
        from opc.layer2_organization.adaptive_team import AdaptiveTeamPlan
        from opc.layer2_organization.recruiter import (
            build_fallback_role_ids, build_staffing_experience_modes, build_staffing_overrides,
        )
        from opc.layer2_organization.talent_market import TalentMarket

        team = AdaptiveTeamPlan.model_validate(recruitment_plan.metadata["adaptive_team_plan"])
        roles = {role.id: role for role in team.roles}
        if {proposal.role_id for proposal in recruitment_plan.proposals} != set(roles):
            raise ValueError("The approved roster must match the proposed organization.")
        org_id = str(recruitment_plan.metadata["adaptive_organization_id"])
        config = self.parent.config.model_copy(deep=True)
        employee_pool = self.parent.talent_market.list_adaptive_staffing_employees(
            project_id=self.parent.project_id or "default",
        )
        source_employees = {employee.employee_id: employee for employee in employee_pool
                            if not is_placeholder_employee(employee)}
        config.org.employees = []
        market = TalentMarket(self.parent.opc_home, config)
        selections = dict(staffing_selections or {})
        for proposal in recruitment_plan.proposals:
            raw = selections.get(proposal.role_id)
            selection = self.parent._normalize_staffing_selection(raw) if raw is not None else None
            if selection is not None:
                kind, selected_id = selection["kind"], selection.get("id", "")
            elif proposal.existing_employee and proposal.status == "existing_staff":
                kind, selected_id = "employee", proposal.existing_employee.employee_id
            elif proposal.candidate and proposal.status == "proposed_hire":
                kind, selected_id = "template", proposal.candidate.template_id
            else:
                kind, selected_id = "fallback", ""
            if kind == "employee":
                if selected_id not in source_employees:
                    raise ValueError(f"Selected employee `{selected_id}` is no longer available.")
                employee = next((e for e in config.org.employees if e.employee_id == selected_id), None)
                if employee is None:
                    employee = source_employees[selected_id].model_copy(deep=True)
                    config.org.employees.append(employee)
                market._attach_employee_to_role(employee, proposal.role_id)
                proposal.status = "existing_staff"
                proposal.existing_employee = RecruitmentEmployeeRecommendation(
                    employee_id=employee.employee_id, employee_name=employee.name, role_id=proposal.role_id,
                )
                roles[proposal.role_id].status = "existing_staff"
                roles[proposal.role_id].employee_id = selected_id
                roles[proposal.role_id].template_id = ""
            elif kind == "template":
                name = proposal.candidate.proposed_employee_name if proposal.candidate and proposal.candidate.template_id == selected_id else None
                employee = market.ensure_hire_template(selected_id, proposal.role_id, employee_name=name)
                # Runtime assignment only needs the selected employee identity.
                proposal.status = "existing_staff"
                proposal.existing_employee = RecruitmentEmployeeRecommendation(
                    employee_id=employee.employee_id, employee_name=employee.name, role_id=proposal.role_id,
                )
                proposal.metadata["adaptive_template_only"] = True
                roles[proposal.role_id].status = "proposed_hire"
                roles[proposal.role_id].employee_id = ""
                roles[proposal.role_id].template_id = selected_id
                roles[proposal.role_id].proposed_employee_name = employee.name
            elif kind == "fallback":
                proposal.status = "fallback_role_only"
                roles[proposal.role_id].status = "fallback_role_only"
                roles[proposal.role_id].employee_id = ""
                roles[proposal.role_id].template_id = ""
            else:
                raise ValueError(f"Unknown staffing selection kind `{kind}`.")
        payload = team.org_payload(config, organization_id=org_id)
        payload["employees"] = [employee.model_dump() for employee in config.org.employees]
        payload["metadata"].update(
            adaptive_source_organization_id=self.parent.config.org.organization_id,
            adaptive_source_project_id=self.parent.project_id or "default",
        )
        # No active-org index or corporate config is rewritten.
        snapshot_path = write_runtime_org_config_payload(self.parent.opc_home / "config", org_id, payload)
        runtime, _ = await self._create_runtime(project_id=self.parent.project_id, org_id=org_id, base_config=config)
        try:
            scoped_decision = copy.deepcopy(decision)
            scoped_decision.company_profile = "custom"
            scoped_decision.org_id = org_id
            scoped_decision.metadata["company_team_strategy"] = "adaptive"
            scoped_spec = copy.deepcopy(runtime_spec)
            scoped_spec.profile = "custom"
            scoped_spec.metadata = {
                **scoped_spec.metadata, "company_profile": "custom", "org_id": org_id,
                "organization_id": org_id, "organization_name": "Task-adaptive team",
                "organization_config_file": f"{snapshot_path.parent.name}/{snapshot_path.name}",
                "adaptive_team_plan": team.model_dump(mode="json"),
            }
            experience_modes = build_staffing_experience_modes(recruitment_plan)
            for proposal in recruitment_plan.proposals:
                if proposal.metadata.get("adaptive_template_only"):
                    experience_modes[proposal.role_id] = "template_only"
            selected_agents = {key: value for key, value in dict(role_agent_overrides or {}).items() if key in roles}
            staffing = build_staffing_overrides(recruitment_plan)
            fallback_roles = build_fallback_role_ids(recruitment_plan)
            # Reuse the same project + organization-scoped persistence as fixed
            # staffing, so reloads cannot replace an approved executor choice.
            runtime._save_project_company_staffing_defaults(
                scoped_decision, company_profile="custom", role_ids=set(roles),
                staffing_overrides=staffing, staffing_experience_modes=experience_modes,
                fallback_role_ids=fallback_roles, role_agent_overrides=selected_agents,
            )
            await self.parent._mark_session_recruitment_confirmation_completed(
                execution_kwargs.get("session_id"), source="adaptive_team_approval",
            )
            return await runtime._continue_company_mode_execution(
                scoped_decision, original_message, scoped_spec,
                staffing_overrides=staffing,
                staffing_experience_modes=experience_modes,
                fallback_role_ids=fallback_roles,
                role_agent_overrides=selected_agents,
                **execution_kwargs,
            )
        finally:
            await self._shutdown_runtime(runtime)

    def _build_org_config(self, organization_id: str | None, *, base_config: OPCConfig | None = None) -> tuple[OPCConfig, str | None]:
        config_dir = self.parent.opc_home / "config"
        payload, source_path = load_org_config_payload(config_dir, organization_id)
        if base_config is None:
            try:
                base_config = OPCConfig.load(config_dir) if config_dir.exists() else copy.deepcopy(self.parent.config)
            except Exception:
                base_config = copy.deepcopy(self.parent.config)
        loaded_config = apply_org_config_payload_to_config(
            base_config,
            payload,
            source_path=source_path,
        )
        resolved_org_id = str(getattr(loaded_config.org, "organization_id", "") or organization_id or "").strip() or None
        validate_runnable_org_config(loaded_config, organization_id=resolved_org_id or "")
        return loaded_config, resolved_org_id

    async def _create_runtime(
        self,
        *,
        project_id: str | None,
        org_id: str | None,
        base_config: OPCConfig | None = None,
    ) -> tuple[OPCEngine, str]:
        """Build the one isolated Engine used by every custom-org ingress."""

        from opc.engine import OPCEngine
        from opc.plugins.office_ui.services.models import ServiceError

        normalized_org_id = str(org_id or "").strip()
        if not normalized_org_id:
            raise ServiceError(
                "org_id_required",
                "org_id_required",
                {
                    "project_id": project_id or self.parent.project_id or "default",
                    "reason": "custom_company_run_requires_durable_org_id",
                },
            )
        org_config, resolved_org_id = (
            self._build_org_config(normalized_org_id, base_config=base_config)
            if base_config is not None else self._build_org_config(normalized_org_id)
        )
        if not resolved_org_id:
            raise ServiceError(
                "org_id_required",
                "org_id_required",
                {
                    "project_id": project_id or self.parent.project_id or "default",
                    "reason": "custom_company_run_requires_durable_org_id",
                },
            )
        normalized_project_id = str(
            project_id or self.parent.project_id or "default"
        ).strip() or "default"
        shared_store = getattr(self.parent, "store", None)
        runtime = OPCEngine(
            config=org_config,
            opc_home=self.parent.opc_home,
            project_id=normalized_project_id,
            store=shared_store,
            owns_store=shared_store is None,
            run_startup_reconcile=shared_store is None,
            active_task_run_registry=getattr(
                self.parent,
                "_active_task_run_registry",
                None,
            ),
            owns_active_task_run_registry=False,
            on_progress=self.parent.on_progress,
            on_runtime_event=self.parent.on_runtime_event,
            on_escalation=None,
            interaction_coordinator=getattr(
                self.parent,
                "interaction_coordinator",
                None,
            ),
            pre_delivery_validator=getattr(
                self.parent,
                "pre_delivery_validator",
                None,
            ),
        )
        # Registration is synchronous and precedes initialization's first
        # await.  The Store-owning parent can therefore always find and cancel
        # this ingress if shutdown crosses custom runtime construction.
        ingress_task = self.parent._register_runtime_child(runtime)
        self._runtime_ingress_tasks[id(runtime)] = ingress_task
        runtime.on_company_runtime_children = self.parent.on_company_runtime_children
        try:
            await runtime.initialize()
        except BaseException:
            shutdown_completed = False
            try:
                await runtime.shutdown()
                shutdown_completed = True
            finally:
                if shutdown_completed:
                    self.parent._unregister_runtime_child(runtime, ingress_task)
                    self._runtime_ingress_tasks.pop(id(runtime), None)
            raise
        # Native permissions are live, root-chat-scoped operator state. Org
        # execution stays isolated, while permission changes must reach every
        # already-running member of this project's custom runtimes.
        if self.parent.approval_engine is not None and runtime.approval_engine is not None:
            runtime.approval_engine.native_policy = self.parent.approval_engine.native_policy
            runtime.approval_engine._tool_approval_locks = self.parent.approval_engine._tool_approval_locks
            runtime.approval_engine._session_allowlist = self.parent.approval_engine._session_allowlist
        company_executor = getattr(runtime, "company_executor", None)
        if company_executor is not None:
            callback_factory = getattr(
                self.parent,
                "on_company_kanban_callback_factory",
                None,
            )
            if callable(callback_factory):
                company_executor.on_kanban_changed = callback_factory(runtime)
            else:
                parent_executor = getattr(self.parent, "company_executor", None)
                company_executor.on_kanban_changed = getattr(
                    parent_executor,
                    "on_kanban_changed",
                    None,
                )
        return runtime, resolved_org_id

    async def _shutdown_runtime(self, runtime: OPCEngine) -> None:
        from opc.layer2_organization.phase_hooks import unregister_dispatcher_wake

        company_executor = getattr(runtime, "company_executor", None)
        wake = getattr(company_executor, "_signal_dispatcher_wake", None)
        if wake is not None:
            unregister_dispatcher_wake(wake)
        await runtime.shutdown()
        ingress_task = self._runtime_ingress_tasks.pop(id(runtime), None)
        self.parent._unregister_runtime_child(runtime, ingress_task)

    async def dispatch_interaction_decision(
        self,
        lease: InteractionDecisionLease,
        *,
        org_id: str,
    ) -> Any:
        """Consume a claimed owner decision under its durable org config."""

        runtime, _ = await self._create_runtime(
            project_id=lease.checkpoint.project_id,
            org_id=org_id,
        )
        try:
            return await runtime._dispatch_interaction_decision(lease)
        finally:
            await self._shutdown_runtime(runtime)

    async def process_message(
        self,
        content: str,
        *,
        project_id: str | None,
        session_id: str | None,
        org_id: str | None,
        preferred_agent: str | None,
        domains: list[str] | None,
        origin_task_id: str | None,
        attachment_refs: list[dict[str, Any]] | None,
        message_metadata: dict[str, Any] | None,
    ) -> str:
        runtime, resolved_org_id = await self._create_runtime(
            project_id=project_id,
            org_id=org_id,
        )
        normalized_project_id = str(
            project_id or self.parent.project_id or "default"
        ).strip() or "default"
        org_config = runtime.config

        try:
            normalized_attachment_refs = runtime._normalize_attachment_refs(attachment_refs)
            metadata = {
                "mode": "company",
                "exec_mode": "org",
                "org_id": resolved_org_id,
                "organization_id": resolved_org_id,
                "organization_name": str(getattr(org_config.org, "organization_name", "") or "").strip(),
                "organization_config_file": str(getattr(org_config.org, "organization_config_file", "") or "").strip(),
                "preferred_agent": preferred_agent,
                "domains": domains or [],
                "company_profile": "custom",
                "origin_task_id": origin_task_id,
                "attachment_refs": normalized_attachment_refs,
            }
            if message_metadata:
                metadata.update(dict(message_metadata))
                metadata["mode"] = "company"
                metadata["exec_mode"] = "org"
                metadata["company_profile"] = "custom"
                metadata["org_id"] = resolved_org_id
                metadata["organization_id"] = resolved_org_id

            message = UserMessage(
                channel="cli",
                user_id="owner",
                content=content,
                attachments=normalized_attachment_refs,
                session_id=session_id or str(uuid.uuid4()),
                project_context=normalized_project_id,
                metadata=metadata,
            )
            response = await runtime.message_bus.process_single(message)
            return response.content if response else "No response generated."
        finally:
            await self._shutdown_runtime(runtime)
