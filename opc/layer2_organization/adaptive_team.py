"""Task-specific organization and staffing, decided together before execution."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import BaseModel, Field, model_validator

from opc.core.config import OPCConfig, RoleConfig, build_company_org_payload_from_config


TEAM_RECRUITER_PROMPT = """You are the designated organization and recruitment agent.
Design the responsibilities AND select their staff in ONE decision for the original task.
Return only a complete JSON object matching schema. Do not execute the task yet.
Exactly one role has parent_id=null. Other parent_id values name roles in this
plan; the human owner is outside the team, not a role or a parent_id value.
For existing_staff, copy employee_id from employee_pool and leave template_id="".
For proposed_hire, copy template_id from candidate_pool and leave employee_id="".
For direct_role_execution/fallback_role_only, leave BOTH staff IDs empty.
dependencies contains only role IDs for hard prerequisites to starting the work,
never explanatory sentences or artifact names. Independent work uses []. Do not
make a manager's intake depend on a report that it must first delegate, or make
the child depend on that manager's final report. Put phase-specific coordination
and integration handoffs in coordination_notes instead of creating a role cycle.

Start with a feasible solo owner, then compare smaller/merged alternatives. Use solo
when additional people do not justify their cost. There is no prescribed team size
or hierarchy depth. Respect explicit user constraints and the available time/budget.
Add a member only for a concrete complementary contribution: missing expertise,
independently useful parallel work, or independent verification of a material risk.
Different titles or copies of the same model do not automatically add capability.
Judge expected quality, elapsed time AND total cost, including all members' context
loading, model/tool calls, planning, messages, handoffs, waiting, review, rework and
integration. Parallelism does not make computation free. Do not invent numeric
savings, prices, confidence scores or extra task requirements.

For each role explain its incremental_value, merge_alternative, artifact ownership,
deliverables, acceptance criteria and dependencies. Avoid overlapping investigations,
tiny handoffs and idle seats. An intermediate manager must own useful work and remove
a concrete recurring coordination burden compared with a flatter structure. Review
specialists are useful only when their task-specific independent contribution warrants
their cost; implementers still self-check and managers retain acceptance responsibility.
The root owns integration and final delivery and may implement work personally.
Direct managers delegate executable work only to direct reports, review actual evidence,
request specific rework and integrate accepted work. Return a work-item turn after
delegating so the runtime can schedule children/reviews; do not poll while blocking it.

Recruitment is part of this design, not a second role-filling pass. Compare existing
employees' relevant experience with candidate capabilities, descriptions, domains and
tags. Reuse credible existing staff when beneficial, but do not treat tenure, titles
or category labels as proof of fit. A persona does not grant tools or expertise the
executor lacks. Explain the fit to the specific responsibility. Reuse one employee
across compatible responsibilities when sensible; account for serialized work and
context switching rather than claiming independent parallel capacity. Use only IDs
from employee_pool/candidate_pool. Use direct_role_execution when a specialized hire
adds no useful capability; fallback_role_only when no offered specialist is credible.
Do not hire to fill a quota or preserve the existing organization by default.

Keep team_size_review concise: compare a smaller feasible arrangement, record the
removal/merge check, expected net benefit and hierarchy tradeoff. These are qualitative
decision notes, not proof of optimality. Preserve sound decisions when repairing JSON.
User feedback and explicit team constraints take precedence over catalog personas.
If validation_feedback is present, correct the reported issues in previous_response
yourself and return the complete revised plan. The runtime does not choose a different
team or staff on your behalf.
"""


class TeamSizeReview(BaseModel):
    smaller_alternative: str = ""
    removal_merge_check: str = ""
    retained_team_benefit: str = ""
    hierarchy_tradeoff: str = ""


class AdaptiveTeamRole(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    parent_id: str | None = Field(default=None, description="null for exactly one accountable root; otherwise the id of another role in this plan. Do not use owner.")
    deliverables: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    ownership: list[str] = Field(min_length=1)
    dependencies: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]] = Field(
        default_factory=list, description="Only IDs of roles that must finish before this role starts; [] for independent work. Never prose, artifact names, or routine manager intake/review handoffs. Must form a DAG.",
    )
    coordination_notes: list[str] = Field(default_factory=list, description="Narrative phase-specific handoffs, manager intake, integration and review coordination; these do not block role startup.")
    incremental_value: str = ""
    merge_alternative: str = ""
    status: Literal["existing_staff", "proposed_hire", "direct_role_execution", "fallback_role_only"]
    employee_id: str = Field(default="", description="Only existing_staff: an employee_pool employee_id; otherwise empty. Existing staff must leave template_id empty.")
    template_id: str = Field(default="", description="Only proposed_hire: a candidate_pool template_id; otherwise empty. New hires must leave employee_id empty.")
    proposed_employee_name: str = ""
    rationale: str = Field(min_length=1)


class AdaptiveTeamPlan(BaseModel):
    decision: Literal["solo", "team"]
    motivations: list[Literal["faster", "better", "fewer_tokens", "lower_cost"]] = Field(default_factory=list)
    team_size_review: TeamSizeReview = Field(default_factory=TeamSizeReview)
    comparison_with_solo: str = Field(min_length=1)
    coordination_costs: str = Field(min_length=1)
    integration_strategy: str = Field(min_length=1)
    roles: list[AdaptiveTeamRole] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_structure(self) -> AdaptiveTeamPlan:
        roles = {role.id: role for role in self.roles}
        if len(roles) != len(self.roles):
            raise ValueError("Role IDs must be unique.")
        if len([r for r in self.roles if r.parent_id is None]) != 1:
            raise ValueError("Choose exactly one accountable root with parent_id=null; every other role must reference a role id as parent_id.")
        if (self.decision == "solo") != (len(roles) == 1):
            raise ValueError("Solo means one role; team means multiple roles.")
        if self.decision == "team" and not self.motivations:
            raise ValueError("Explain the expected benefit of adding members.")
        for role in self.roles:
            seen = {role.id}
            cursor = role
            while cursor.parent_id is not None:
                if cursor.parent_id not in roles or cursor.parent_id in seen:
                    raise ValueError("Reporting must form a connected, acyclic tree.")
                seen.add(cursor.parent_id)
                cursor = roles[cursor.parent_id]
            if any(dep not in roles or dep == role.id for dep in role.dependencies):
                raise ValueError("Dependencies must reference other existing roles.")
            if role.status == "existing_staff" and (not role.employee_id or role.template_id):
                raise ValueError("Existing staff needs only an employee_id.")
            if role.status == "proposed_hire" and (not role.template_id or role.employee_id):
                raise ValueError("A hire needs only a template_id.")
            if role.status in {"direct_role_execution", "fallback_role_only"} and (role.employee_id or role.template_id):
                raise ValueError("Role-only execution must not select staff IDs.")
        # Validate artifact dependencies separately from the reporting tree.
        pending = {r.id: set(r.dependencies) for r in self.roles}
        while pending:
            ready = {key for key, deps in pending.items() if not deps}
            if not ready:
                raise ValueError("Artifact dependencies must be acyclic.")
            pending = {key: deps - ready for key, deps in pending.items() if key not in ready}
        return self

    @property
    def root_id(self) -> str:
        return next(role.id for role in self.roles if role.parent_id is None)

    def org_payload(self, base: OPCConfig, *, organization_id: str) -> dict[str, Any]:
        """Compile a session runtime snapshot without mutating the active org."""
        from opc.layer2_organization.company_runtime_profiles import get_builtin_roles

        config = base.model_copy(deep=True)
        config.org.organization_id = organization_id
        config.org.organization_name = "Task-adaptive team"
        config.org.company_name = "Task-adaptive team"
        config.org.company_profile = "custom"
        config.org.final_decider_role_id = self.root_id
        config.org.teams = []
        config.org.external_team_bindings = []
        config.org.escalation_rules = []
        config.org.roles = []
        # Personas cannot expand the executor's tools. Use the existing company
        # presets; the registry/permission gateway still enforces availability.
        tools = list(dict.fromkeys(tool for role in get_builtin_roles("corporate") for tool in role.tools))
        for role in self.roles:
            children = [r.id for r in self.roles if r.parent_id == role.id]
            contract = {
                "deliverables": role.deliverables,
                "acceptance_criteria": role.acceptance_criteria,
                "artifact_ownership": role.ownership,
                "dependencies": role.dependencies,
                "coordination_notes": role.coordination_notes,
                "integration_strategy": self.integration_strategy,
            }
            config.org.roles.append(RoleConfig(
                id=role.id, name=role.name,
                responsibility=role.responsibility + "\n" + json.dumps(contract, ensure_ascii=False),
                reports_to=role.parent_id or "owner", can_spawn=children,
                role_type="coordinator" if children else "worker", tools=tools,
            ))
        payload = build_company_org_payload_from_config(config, organization_id=organization_id, force_profile="custom")
        payload["metadata"]["adaptive_team_plan"] = self.model_dump(mode="json")
        payload["metadata"]["organization_persistence"] = "session"
        return payload


async def build_team_recruitment_plan(
    recruiter: Any, runtime_spec: Any, *, project_id: str,
    recruiter_feedback: list[str] | None = None, llm: Any = None,
    recruitment_agent: str = "native",
) -> Any:
    """One valid model response supplies both topology and staff assignments."""
    from opc.core.models import RecruitmentNeed, RecruitmentPlan
    from opc.layer2_organization.recruiter import ensure_recruitment_plan_default_agents

    if llm is None:
        raise ValueError("Automatic team formation requires an available planning agent.")
    candidates = recruiter.talent_market.list_available_templates()
    from opc.core.employee_registry import is_placeholder_employee

    employees = [e for e in recruiter.talent_market.list_adaptive_staffing_employees(project_id=project_id)
                 if not is_placeholder_employee(e)]
    metadata = dict(getattr(runtime_spec, "metadata", {}) or {})
    config = getattr(recruiter.org_engine, "config", None)
    payload = {
        "user_request": runtime_spec.original_request or metadata.get("original_request", ""),
        "project_id": project_id,
        "recruiter_feedback": list(recruiter_feedback or []),
        "constraints": metadata.get("team_constraints", {}),
        "budget_constraints": metadata.get("budget_constraints", {}),
        "task_context": metadata.get("team_planning_context", ""),
        "execution_context": {
            "model": str(getattr(getattr(config, "llm", None), "default_model", "") or ""),
            "domains": list(metadata.get("domains", []) or []),
            "tools": sorted({tool for agent in recruiter.org_engine.list_agents() for tool in agent.tools}),
            "note": "Each executing role consumes model calls; a persona does not grant additional tools.",
        },
        "candidate_pool": [recruiter.talent_market.build_candidate_summary(c) for c in candidates],
        "employee_pool": [recruiter._build_existing_employee_summary(
            e, role_id=e.role_id, domains=[], project_id=project_id,
        ) for e in employees],
        "schema": AdaptiveTeamPlan.model_json_schema(),
    }
    started = time.monotonic()
    feedback: list[str] = []
    previous = ""
    attempt = 0
    # The selected agent owns planning and corrections. Only validate the
    # proposed runtime data; do not impose a deadline or a correction quota.
    # Provider failures and caller cancellation propagate without substitution.
    while True:
        attempt += 1
        prompt = dict(payload)
        if feedback:
            prompt.update(validation_feedback=list(feedback), previous_response=previous)
        previous = await llm.simple_chat(
            prompt=json.dumps(prompt, ensure_ascii=False), system=TEAM_RECRUITER_PROMPT,
            task_type="team_formation",
        )
        try:
            plan = AdaptiveTeamPlan.model_validate(recruiter._parse_llm_json(previous))
            prepared = {
                role.id: {"need": RecruitmentNeed(
                    role_id=role.id, role_name=role.name,
                    role_responsibility=role.responsibility, request_text=payload["user_request"],
                ), "existing_employees": employees, "triage_action": "joint_team_staffing"}
                for role in plan.roles
            }
            proposals = recruiter._build_global_proposals_from_response(
                [{**role.model_dump(), "role_id": role.id} for role in plan.roles],
                prepared_by_role=prepared, role_order=[r.id for r in plan.roles],
                project_id=project_id, attempt=attempt, candidate_pool=candidates, employee_pool=employees,
            )
        except (ValueError, TypeError) as exc:
            logger.warning("Adaptive team plan validation failed (planner={}, attempt={}): {}",
                           recruitment_agent, attempt, str(exc))
            feedback.append(str(exc))
            continue
        result = ensure_recruitment_plan_default_agents(RecruitmentPlan(
            company_profile=str(runtime_spec.profile or "corporate"), proposals=proposals,
            recruiter_feedback=list(recruiter_feedback or []),
            metadata={"project_id": project_id, "execution_mode": "company_mode",
                      "recruitment_agent": recruitment_agent, "adaptive_team_plan": plan.model_dump(mode="json"),
                      "planning_usage": list(getattr(llm, "usage_records", [])),
                      "planning_attempts": attempt, "planning_elapsed_seconds": time.monotonic() - started},
        ))
        result.summary = recruiter.render_recruitment_summary(result)
        return result
