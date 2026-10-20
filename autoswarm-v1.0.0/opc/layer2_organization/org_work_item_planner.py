"""Org-driven company work-item runtime planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkItemDependencySpec:
    """Dependency from one projected work item to another."""

    projection_id: str
    dependency_projection_id: str
    dependency_class: str = "hard"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "dependency_projection_id": self.dependency_projection_id,
            "dependency_class": self.dependency_class,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkItemDependencySpec":
        payload = dict(data or {})
        return cls(
            projection_id=str(payload.get("projection_id", "") or "").strip(),
            dependency_projection_id=str(payload.get("dependency_projection_id", "") or "").strip(),
            dependency_class=str(payload.get("dependency_class", "") or "hard").strip() or "hard",
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass
class WorkItemGatePolicy:
    """Projection-first gate policy for company work items."""

    gate_type: str = "review"
    instructions: str = ""
    reviewer_role: str | None = None
    requires_human: bool = False
    on_reject: str = "halt"
    rework_projection_id: str | None = None
    max_retries: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata or {})
        rework_projection_id = str(
            self.metadata.get("rework_projection_id")
            or self.rework_projection_id
            or ""
        ).strip()
        self.rework_projection_id = rework_projection_id or None
        self.gate_type = str(self.gate_type or "review").strip().lower() or "review"
        self.on_reject = str(self.on_reject or "halt").strip().lower() or "halt"
        if rework_projection_id:
            self.metadata["rework_projection_id"] = rework_projection_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.gate_type,
            "instructions": self.instructions,
            "reviewer_role": self.reviewer_role,
            "requires_human": bool(self.requires_human),
            "on_reject": self.on_reject,
            "rework_projection_id": self.rework_projection_id,
            "max_retries": int(self.max_retries),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkItemGatePolicy | None":
        if not data:
            return None
        payload = dict(data or {})
        metadata = dict(payload.get("metadata", {}) or {})
        rework_projection_id = str(
            payload.get("rework_projection_id")
            or metadata.get("rework_projection_id")
            or ""
        ).strip()
        return cls(
            gate_type=str(payload.get("type", "") or payload.get("gate_type", "") or "review"),
            instructions=str(payload.get("instructions", "") or ""),
            reviewer_role=payload.get("reviewer_role"),
            requires_human=bool(payload.get("requires_human", False)),
            on_reject=str(payload.get("on_reject", "") or "halt"),
            rework_projection_id=rework_projection_id or None,
            max_retries=int(payload.get("max_retries", 1) or 1),
            metadata=metadata,
        )


@dataclass
class WorkItemReviewPolicy:
    """Review owner policy for a projected work item."""

    review_owner_role_id: str = ""
    review_level: str = "manager"
    max_reworks: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_owner_role_id": self.review_owner_role_id,
            "review_level": self.review_level,
            "max_reworks": int(self.max_reworks),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkItemReviewPolicy":
        payload = dict(data or {})
        return cls(
            review_owner_role_id=str(payload.get("review_owner_role_id", "") or "").strip(),
            review_level=str(payload.get("review_level", "") or "manager").strip() or "manager",
            max_reworks=int(payload.get("max_reworks", 10) or 10),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass
class WorkItemDeliveryPolicy:
    """Delivery policy for a projected company work item."""

    user_visible: bool = False
    authoritative_output: bool = False
    requires_user_feedback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_visible": bool(self.user_visible),
            "authoritative_output": bool(self.authoritative_output),
            "requires_user_feedback": bool(self.requires_user_feedback),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkItemDeliveryPolicy":
        payload = dict(data or {})
        return cls(
            user_visible=bool(payload.get("user_visible", False)),
            authoritative_output=bool(payload.get("authoritative_output", False)),
            requires_user_feedback=bool(payload.get("requires_user_feedback", False)),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass
class WorkItemProjectionSpec:
    """Projection-first spec consumed by the company work-item runtime."""

    projection_id: str
    turn_type: str
    role_id: str
    title: str
    summary: str = ""
    dependency_projection_ids: list[str] = field(default_factory=list)
    dependency_classes: dict[str, str] = field(default_factory=dict)
    team_id: str = ""
    seat_id: str = ""
    manager_role_id: str = ""
    manager_seat_id: str = ""
    execution_strategy: str = "auto"
    preferred_external_agent: str | None = None
    parallel_group: str | None = None
    prompt_refs: list[str] = field(default_factory=list)
    skill_refs: list[str] = field(default_factory=list)
    handoff_template_ref: str | None = None
    memory_policy_ref: str | None = None
    artifact_contract_ref: str | None = None
    allowed_delegate_role_ids: list[str] = field(default_factory=list)
    contact_role_ids: list[str] = field(default_factory=list)
    gate_policy: WorkItemGatePolicy | None = None
    review_policy: WorkItemReviewPolicy = field(default_factory=WorkItemReviewPolicy)
    delivery_policy: WorkItemDeliveryPolicy = field(default_factory=WorkItemDeliveryPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "turn_type": self.turn_type,
            "role_id": self.role_id,
            "title": self.title,
            "summary": self.summary,
            "dependency_projection_ids": list(self.dependency_projection_ids),
            "dependency_classes": dict(self.dependency_classes),
            "team_id": self.team_id,
            "seat_id": self.seat_id,
            "manager_role_id": self.manager_role_id,
            "manager_seat_id": self.manager_seat_id,
            "execution_strategy": self.execution_strategy,
            "preferred_external_agent": self.preferred_external_agent,
            "parallel_group": self.parallel_group,
            "prompt_refs": list(self.prompt_refs),
            "skill_refs": list(self.skill_refs),
            "handoff_template_ref": self.handoff_template_ref,
            "memory_policy_ref": self.memory_policy_ref,
            "artifact_contract_ref": self.artifact_contract_ref,
            "allowed_delegate_role_ids": list(self.allowed_delegate_role_ids),
            "contact_role_ids": list(self.contact_role_ids),
            "gate_policy": self.gate_policy.to_dict() if self.gate_policy else None,
            "review_policy": self.review_policy.to_dict(),
            "delivery_policy": self.delivery_policy.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkItemProjectionSpec":
        payload = dict(data or {})
        return cls(
            projection_id=str(payload.get("projection_id", "") or "").strip(),
            turn_type=str(payload.get("turn_type", "") or "execute").strip().lower() or "execute",
            role_id=str(payload.get("role_id", "") or "").strip(),
            title=str(payload.get("title", "") or payload.get("projection_id", "") or "Work Item").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
            dependency_projection_ids=_clean_list(payload.get("dependency_projection_ids", [])),
            dependency_classes={
                str(key).strip(): str(value).strip()
                for key, value in dict(payload.get("dependency_classes", {}) or {}).items()
                if str(key).strip() and str(value).strip()
            },
            team_id=str(payload.get("team_id", "") or "").strip(),
            seat_id=str(payload.get("seat_id", "") or "").strip(),
            manager_role_id=str(payload.get("manager_role_id", "") or "").strip(),
            manager_seat_id=str(payload.get("manager_seat_id", "") or "").strip(),
            execution_strategy=str(payload.get("execution_strategy", "") or "auto").strip() or "auto",
            preferred_external_agent=payload.get("preferred_external_agent"),
            parallel_group=payload.get("parallel_group"),
            prompt_refs=_clean_list(payload.get("prompt_refs", [])),
            skill_refs=_clean_list(payload.get("skill_refs", [])),
            handoff_template_ref=payload.get("handoff_template_ref"),
            memory_policy_ref=payload.get("memory_policy_ref"),
            artifact_contract_ref=payload.get("artifact_contract_ref"),
            allowed_delegate_role_ids=_clean_list(payload.get("allowed_delegate_role_ids", [])),
            contact_role_ids=_clean_list(payload.get("contact_role_ids", [])),
            gate_policy=WorkItemGatePolicy.from_dict(payload.get("gate_policy")),
            review_policy=WorkItemReviewPolicy.from_dict(payload.get("review_policy")),
            delivery_policy=WorkItemDeliveryPolicy.from_dict(payload.get("delivery_policy")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


@dataclass
class CompanyWorkItemRuntimePlan:
    """Company mode plan expressed only as projected work items."""

    profile: str = "corporate"
    final_decider_role_id: str = ""
    top_level_role_ids: list[str] = field(default_factory=list)
    root_projection_id: str = ""
    projections: list[WorkItemProjectionSpec] = field(default_factory=list)
    dependencies: list[WorkItemDependencySpec] = field(default_factory=list)
    collaboration_links: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "runtime_model": "multi_team_org",
            "work_item_driven": True,
            "final_decider_role_id": self.final_decider_role_id,
            "top_level_role_ids": list(self.top_level_role_ids),
            "root_projection_id": self.root_projection_id,
            "projections": [projection.to_dict() for projection in self.projections],
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "collaboration_links": [dict(link) for link in self.collaboration_links],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CompanyWorkItemRuntimePlan":
        payload = dict(data or {})
        projections = [
            WorkItemProjectionSpec.from_dict(item)
            for item in list(payload.get("projections", []) or payload.get("seeds", []) or [])
            if isinstance(item, dict)
        ]
        return cls(
            profile=str(payload.get("profile", "") or "corporate").strip() or "corporate",
            final_decider_role_id=str(payload.get("final_decider_role_id", "") or "").strip(),
            top_level_role_ids=_clean_list(payload.get("top_level_role_ids", [])),
            root_projection_id=str(payload.get("root_projection_id", "") or "").strip(),
            projections=projections,
            dependencies=[
                WorkItemDependencySpec.from_dict(item)
                for item in list(payload.get("dependencies", []) or [])
                if isinstance(item, dict)
            ],
            collaboration_links=[dict(item) for item in list(payload.get("collaboration_links", []) or []) if isinstance(item, dict)],
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def projection_by_id(self) -> dict[str, WorkItemProjectionSpec]:
        return {spec.projection_id: spec for spec in self.projections if spec.projection_id}

    def projection_order_map(self) -> dict[str, int]:
        return {
            spec.projection_id: index
            for index, spec in enumerate(self.projections)
            if spec.projection_id
        }

    def dependencies_for(self, projection_id: str) -> list[str]:
        spec = self.projection_by_id().get(str(projection_id or "").strip())
        return list(spec.dependency_projection_ids) if spec is not None else []

    def dependent_projection_ids(self, source_projection_id: str) -> list[str]:
        source = str(source_projection_id or "").strip()
        if not source:
            return []
        return [
            spec.projection_id
            for spec in self.projections
            if source in {str(item).strip() for item in list(spec.dependency_projection_ids or [])}
            and spec.projection_id
        ]


def serialize_company_work_item_plan(plan: CompanyWorkItemRuntimePlan | None) -> dict[str, Any]:
    return plan.to_dict() if plan is not None else {}


def deserialize_company_work_item_plan(data: dict[str, Any] | None) -> CompanyWorkItemRuntimePlan:
    return CompanyWorkItemRuntimePlan.from_dict(data)


@dataclass
class OrgWorkItemSeed:
    """A projection-first template for a work item owned by an org seat."""

    projection_id: str
    turn_type: str
    role_id: str
    team_id: str
    seat_id: str
    manager_role_id: str
    title: str
    summary: str
    skill_refs: list[str] = field(default_factory=list)
    prompt_refs: list[str] = field(default_factory=list)
    allowed_delegate_role_ids: list[str] = field(default_factory=list)
    review_owner_role_id: str = ""
    dependency_work_item_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "turn_type": self.turn_type,
            "role_id": self.role_id,
            "team_id": self.team_id,
            "seat_id": self.seat_id,
            "manager_role_id": self.manager_role_id,
            "title": self.title,
            "summary": self.summary,
            "skill_refs": list(self.skill_refs),
            "prompt_refs": list(self.prompt_refs),
            "allowed_delegate_role_ids": list(self.allowed_delegate_role_ids),
            "review_owner_role_id": self.review_owner_role_id,
            "dependency_work_item_ids": list(self.dependency_work_item_ids),
            "metadata": dict(self.metadata),
        }


@dataclass
class OrgWorkItemRuntimeBlueprint:
    """The custom org collaboration plan consumed by the work-item runtime."""

    profile: str = "custom"
    final_decider_role_id: str = ""
    top_level_role_ids: list[str] = field(default_factory=list)
    root_projection_id: str = ""
    seeds: list[OrgWorkItemSeed] = field(default_factory=list)
    collaboration_links: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "runtime_model": "multi_team_org",
            "work_item_driven": True,
            "final_decider_role_id": self.final_decider_role_id,
            "top_level_role_ids": list(self.top_level_role_ids),
            "root_projection_id": self.root_projection_id,
            "seeds": [seed.to_dict() for seed in self.seeds],
            "collaboration_links": [dict(link) for link in self.collaboration_links],
            "metadata": dict(self.metadata),
        }


def _clean_list(values: Any) -> list[str]:
    return [str(item).strip() for item in list(values or []) if str(item).strip()]


def _projection_id(*parts: str) -> str:
    return "::".join(str(part or "").strip().replace(" ", "_") for part in parts if str(part or "").strip())


def _role_review_owner(agent: Any, manager_role_id: str, final_decider_role_id: str) -> str:
    policy = getattr(agent, "runtime_policy", {}) or {}
    if isinstance(policy, dict):
        review_role = str(policy.get("review_role", "") or "").strip()
        if review_role:
            return review_role
    return manager_role_id or final_decider_role_id


def build_custom_org_work_item_blueprint(
    org_engine: Any,
    *,
    runtime_topology: dict[str, Any],
    original_request: str = "",
    runtime_policy: dict[str, Any] | None = None,
) -> OrgWorkItemRuntimeBlueprint:
    """Build the custom runtime collaboration blueprint from org structure.

    This function deliberately consumes only org topology and runtime policy.
    Custom mode is org-first.
    """

    final_decider_role_id = str(runtime_topology.get("final_decider_role_id", "") or "").strip()
    top_level_role_ids = _clean_list(runtime_topology.get("top_level_role_ids", []))
    if not final_decider_role_id:
        if len(top_level_role_ids) == 1:
            final_decider_role_id = top_level_role_ids[0]
        else:
            raise ValueError("custom org runtime requires final_decider_role_id when multiple top-level roles exist")

    seats = [dict(item) for item in list(runtime_topology.get("seats", []) or []) if isinstance(item, dict)]
    teams = [dict(item) for item in list(runtime_topology.get("teams", []) or []) if isinstance(item, dict)]
    team_by_id = {str(team.get("team_id", "") or "").strip(): team for team in teams if str(team.get("team_id", "") or "").strip()}
    seeds: list[OrgWorkItemSeed] = []
    links: list[dict[str, Any]] = []

    root_projection_id = _projection_id("custom", "intake", final_decider_role_id)
    seen_projection_ids: set[str] = set()

    for seat in seats:
        role_id = str(seat.get("role_id", "") or "").strip()
        seat_id = str(seat.get("seat_id", "") or "").strip()
        team_id = str(seat.get("team_id", "") or "").strip()
        if not role_id or not seat_id:
            continue
        agent = org_engine.get_agent(role_id) if hasattr(org_engine, "get_agent") else None
        manager_role_id = str(seat.get("manager_role_id", "") or getattr(agent, "reports_to", "") or "").strip()
        if manager_role_id == "owner":
            manager_role_id = ""
        allowed_delegate_role_ids = _clean_list(
            seat.get("allowed_delegate_role_ids")
            or (org_engine.get_allowed_downstream_roles(role_id) if hasattr(org_engine, "get_allowed_downstream_roles") else [])
        )
        contact_role_ids = _clean_list(seat.get("contact_role_ids", []))
        is_final_decider = role_id == final_decider_role_id
        is_manager = bool(allowed_delegate_role_ids or str(seat.get("managed_team_id", "") or "").strip() or seat.get("is_team_lead"))
        turn_type = "intake" if is_final_decider else ("dispatch" if is_manager else "execute")
        projection_id = root_projection_id if is_final_decider else _projection_id("custom", turn_type, seat_id)
        if projection_id in seen_projection_ids:
            projection_id = _projection_id(projection_id, role_id)
        seen_projection_ids.add(projection_id)
        role_name = str(getattr(agent, "name", "") or role_id).strip()
        responsibility = str(getattr(agent, "responsibility", "") or "").strip()
        seeds.append(
            OrgWorkItemSeed(
                projection_id=projection_id,
                turn_type=turn_type,
                role_id=role_id,
                team_id=team_id,
                seat_id=seat_id,
                manager_role_id=manager_role_id,
                title=f"{role_name} {turn_type.title()}",
                summary=responsibility or original_request or f"{role_name} work item",
                skill_refs=list(getattr(agent, "skill_refs", []) or []),
                prompt_refs=list(getattr(agent, "prompt_refs", []) or []),
                allowed_delegate_role_ids=allowed_delegate_role_ids,
                review_owner_role_id=_role_review_owner(agent, manager_role_id, final_decider_role_id),
                metadata={
                    "source": "custom_org_work_item_runtime",
                    "role_name": role_name,
                    "responsibility": responsibility,
                    "team_id": team_id,
                    "team_name": str((team_by_id.get(team_id, {}) or {}).get("metadata", {}).get("lead_name", "") or ""),
                    "contact_role_ids": contact_role_ids,
                    "managed_team_id": str(seat.get("managed_team_id", "") or "").strip(),
                    "preferred_external_agent": str(seat.get("preferred_external_agent", "") or getattr(agent, "preferred_external_agent", "") or "").strip(),
                    "selected_execution_agent": str(seat.get("selected_execution_agent", "") or "").strip(),
                    "runtime_policy": dict(getattr(agent, "runtime_policy", {}) or {}),
                },
            )
        )
        for delegate_role_id in allowed_delegate_role_ids:
            links.append({
                "source_role_id": role_id,
                "target_role_id": delegate_role_id,
                "link_type": "delegates_to",
                "source_projection_id": projection_id,
            })
        if manager_role_id:
            links.append({
                "source_role_id": role_id,
                "target_role_id": manager_role_id,
                "link_type": "reports_to",
                "source_projection_id": projection_id,
            })

    if root_projection_id not in {seed.projection_id for seed in seeds}:
        final_agent = org_engine.get_agent(final_decider_role_id) if hasattr(org_engine, "get_agent") else None
        seeds.insert(
            0,
            OrgWorkItemSeed(
                projection_id=root_projection_id,
                turn_type="intake",
                role_id=final_decider_role_id,
                team_id=f"team::{final_decider_role_id}",
                seat_id=f"seat::team::{final_decider_role_id}::{final_decider_role_id}",
                manager_role_id="",
                title=f"{getattr(final_agent, 'name', final_decider_role_id)} Intake",
                summary=original_request or "Custom organization intake",
                skill_refs=list(getattr(final_agent, "skill_refs", []) or []),
                prompt_refs=list(getattr(final_agent, "prompt_refs", []) or []),
                allowed_delegate_role_ids=_clean_list(
                    org_engine.get_allowed_downstream_roles(final_decider_role_id)
                    if hasattr(org_engine, "get_allowed_downstream_roles")
                    else []
                ),
                review_owner_role_id=final_decider_role_id,
                metadata={"source": "custom_org_work_item_runtime", "fallback_root": True},
            ),
        )

    return OrgWorkItemRuntimeBlueprint(
        final_decider_role_id=final_decider_role_id,
        top_level_role_ids=top_level_role_ids,
        root_projection_id=root_projection_id,
        seeds=seeds,
        collaboration_links=links,
        metadata={
            "source": "custom_org_work_item_runtime",
            "team_count": len(teams),
            "seat_count": len(seats),
            "runtime_policy": dict(runtime_policy or {}),
        },
    )


def build_company_work_item_runtime_plan(
    org_engine: Any,
    *,
    profile: str = "corporate",
    runtime_topology: dict[str, Any],
    original_request: str = "",
    runtime_policy: dict[str, Any] | None = None,
) -> CompanyWorkItemRuntimePlan:
    """Build the company runtime plan from org topology, not a fixed step list."""

    normalized_profile = str(profile or "corporate").strip() or "corporate"
    blueprint = build_custom_org_work_item_blueprint(
        org_engine,
        runtime_topology=runtime_topology,
        original_request=original_request,
        runtime_policy=runtime_policy,
    )
    root_projection_id = blueprint.root_projection_id.replace("custom::", f"{normalized_profile}::", 1)
    projections: list[WorkItemProjectionSpec] = []
    dependencies: list[WorkItemDependencySpec] = []
    for seed in blueprint.seeds:
        projection_id = seed.projection_id.replace("custom::", f"{normalized_profile}::", 1)
        dependency_projection_ids = [
            item.replace("custom::", f"{normalized_profile}::", 1)
            for item in list(seed.dependency_work_item_ids or [])
        ]
        if projection_id != root_projection_id and root_projection_id and root_projection_id not in dependency_projection_ids:
            dependency_projection_ids.insert(0, root_projection_id)
        for dependency_projection_id in dependency_projection_ids:
            dependencies.append(
                WorkItemDependencySpec(
                    projection_id=projection_id,
                    dependency_projection_id=dependency_projection_id,
                    dependency_class="hard",
                )
            )
        metadata = {
            **dict(seed.metadata or {}),
            "source": "company_work_item_runtime_plan",
            "seed_source": dict(seed.metadata or {}).get("source", ""),
            "work_kind": seed.turn_type,
            "delegation_turn_kind": seed.turn_type,
            "allowed_delegate_role_ids": list(seed.allowed_delegate_role_ids),
            "dependency_projection_ids": list(dependency_projection_ids),
            "runtime_policy": dict(runtime_policy or {}),
        }
        is_root = projection_id == root_projection_id
        projections.append(
            WorkItemProjectionSpec(
                projection_id=projection_id,
                turn_type=seed.turn_type,
                role_id=seed.role_id,
                title=seed.title,
                summary=seed.summary,
                dependency_projection_ids=dependency_projection_ids,
                team_id=seed.team_id,
                seat_id=seed.seat_id,
                manager_role_id=seed.manager_role_id,
                prompt_refs=list(seed.prompt_refs),
                skill_refs=list(seed.skill_refs),
                allowed_delegate_role_ids=list(seed.allowed_delegate_role_ids),
                contact_role_ids=_clean_list(seed.metadata.get("contact_role_ids", [])),
                preferred_external_agent=str(seed.metadata.get("preferred_external_agent", "") or "").strip() or None,
                gate_policy=(
                    WorkItemGatePolicy(
                        gate_type="review",
                        reviewer_role=seed.review_owner_role_id or None,
                        on_reject="rework",
                        rework_projection_id=projection_id,
                        metadata={"source": "work_item_review_policy"},
                    )
                    if seed.review_owner_role_id and not is_root
                    else None
                ),
                review_policy=WorkItemReviewPolicy(
                    review_owner_role_id=seed.review_owner_role_id,
                    review_level="manager" if seed.review_owner_role_id else "human",
                ),
                delivery_policy=WorkItemDeliveryPolicy(
                    user_visible=is_root,
                    authoritative_output=is_root,
                    requires_user_feedback=is_root,
                ),
                metadata=metadata,
            )
        )

    return CompanyWorkItemRuntimePlan(
        profile=normalized_profile,
        final_decider_role_id=blueprint.final_decider_role_id,
        top_level_role_ids=list(blueprint.top_level_role_ids),
        root_projection_id=root_projection_id,
        projections=projections,
        dependencies=dependencies,
        collaboration_links=[dict(link) for link in blueprint.collaboration_links],
        metadata={
            **dict(blueprint.metadata or {}),
            "source": "company_work_item_runtime_plan",
            "runtime_model": "multi_team_org",
            "work_item_driven": True,
            "runtime_policy": dict(runtime_policy or {}),
        },
    )
