"""Org-first architecture blueprint registry for AutoSwarm Market.

Blueprints define organization structure and optional work-item templates.
Operational parameters (prompt_refs, runtime_policy,
preferred_external_agent, RuntimePolicyConfig) are inferred at install-time
from role hierarchy and template hints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from opc.core.config import (
    CommunicationPolicyConfig,
    HandoffPolicyConfig,
    MemoryPolicyConfig,
    ParallelPolicyConfig,
    ReviewPolicyConfig,
    RoleConfig,
    RoleRuntimePolicyConfig,
    RuntimePolicyConfig,
    slugify_organization_name,
)
from opc.market.package_format import InstalledPackageInfo

# ---------------------------------------------------------------------------
# Keywords used to infer preferred_external_agent
# ---------------------------------------------------------------------------

_ENG_KEYWORDS: set[str] = {
    "code", "coding", "develop", "developer", "development",
    "engineer", "engineering", "implement", "implementation",
    "test", "testing", "qa", "quality assurance",
    "devops", "infrastructure", "ci/cd", "deploy", "deployment",
    "software", "backend", "frontend", "api", "debug", "debugging",
    "kubernetes", "cloud", "pipeline",
}


# ---------------------------------------------------------------------------
# ArchitectureBlueprint — unified preset model
# ---------------------------------------------------------------------------

class ArchitectureBlueprint(BaseModel):
    """Unified architecture template — pure structure + display metadata.

    Roles and work_item_templates contain only structural fields
    (id, name, responsibility, reports_to, can_spawn for roles;
     id, title, role_id, dependencies, parallel_group, gate for templates).

    Operational config is inferred at install-time via
    ``infer_collaboration_config()``.
    """

    # ── Display metadata (frontend Marketplace) ──────────────────────
    id: str
    name: str
    description: str
    category: str
    collaboration_pattern: str  # descriptive label for UI filtering
    dag_summary: str
    tags: list[str] = Field(default_factory=list)
    team_size: str = ""
    emoji: str = ""
    color: str = ""

    # ── Pure structural definitions ──────────────────────────────────
    roles: list[dict[str, Any]]
    work_item_templates: list[dict[str, Any]]

    def to_display_card(self) -> dict[str, Any]:
        """Summary card for frontend browse — matches existing WS format."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "team_size": self.team_size,
            "emoji": self.emoji,
            "color": self.color,
            "roles_count": len(self.roles),
            "work_item_templates_count": len(self.work_item_templates),
            "gates_count": sum(
                1 for s in self.work_item_templates if s.get("gate")
            ),
            "collaboration_pattern": self.collaboration_pattern,
            "dag_summary": self.dag_summary,
        }

    def to_detail(self) -> dict[str, Any]:
        """Full detail for frontend preview — matches existing WS format."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "collaboration_pattern": self.collaboration_pattern,
            "dag_summary": self.dag_summary,
            "tags": self.tags,
            "team_size": self.team_size,
            "emoji": self.emoji,
            "color": self.color,
            "roles": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "responsibility": r.get("responsibility", ""),
                    "reports_to": r.get("reports_to", "owner"),
                    "can_spawn": r.get("can_spawn", []),
                }
                for r in self.roles
            ],
            "work_item_templates": [
                {
                    "id": s["id"],
                    "title": s.get("title", s["id"]),
                    "role_id": s.get("role_id", ""),
                    "dependencies": s.get("dependencies", []),
                    "parallel_group": s.get("parallel_group"),
                    "gate": (
                        {
                            "type": s["gate"]["type"],
                            "reviewer_role": s["gate"].get("reviewer_role"),
                        }
                        if s.get("gate")
                        else None
                    ),
                }
                for s in self.work_item_templates
            ],
        }


# ---------------------------------------------------------------------------
# YAML-backed built-in blueprints
# ---------------------------------------------------------------------------

_BUILTIN_PRESETS_DIR = Path(__file__).with_name("builtin_presets")


def _expand_yaml_preset(data: dict[str, Any]) -> dict[str, Any]:
    """Expand small YAML conveniences into the ArchitectureBlueprint schema."""
    expanded = dict(data)
    toolsets = {
        str(name): list(tools or [])
        for name, tools in dict(expanded.pop("toolsets", {}) or {}).items()
    }
    roles: list[dict[str, Any]] = []
    for raw_role in list(expanded.get("roles", []) or []):
        role = dict(raw_role or {})
        toolset_name = str(role.pop("toolset", "") or "").strip()
        if toolset_name and not role.get("tools"):
            role["tools"] = list(toolsets.get(toolset_name, []))
        roles.append(role)
    expanded["roles"] = roles
    return expanded


def load_architecture_presets_from_yaml(
    presets_dir: Path | None = None,
) -> list[ArchitectureBlueprint]:
    """Load built-in architecture presets from YAML files."""
    source_dir = Path(presets_dir or _BUILTIN_PRESETS_DIR)
    if not source_dir.is_dir():
        return []

    presets: list[ArchitectureBlueprint] = []
    for path in sorted(source_dir.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Expected YAML mapping in architecture preset: {path}")
        presets.append(ArchitectureBlueprint.model_validate(_expand_yaml_preset(data)))
    return presets


# ---------------------------------------------------------------------------
# Collaboration inference — derives config from org topology
# ---------------------------------------------------------------------------

def infer_collaboration_config(
    roles: list[dict[str, Any]],
    work_item_templates: list[dict[str, Any]],
) -> tuple[list[RoleConfig], list[dict[str, Any]], RuntimePolicyConfig]:
    """Infer full collaboration config from pure structural data.

    Parameters
    ----------
    roles : list of dicts with keys ``id, name, responsibility, reports_to, can_spawn``
    work_item_templates : optional template hints with keys ``id, title, role_id, dependencies, parallel_group, gate``

    Returns
    -------
    (enriched_roles, enriched_work_item_templates, runtime_policy)
        Ready-to-install ``RoleConfig``, work-item template hints, and
        ``RuntimePolicyConfig`` objects with operational fields filled.
    """

    # ── 1. Build topology graph ──────────────────────────────────────
    role_map: dict[str, dict[str, Any]] = {r["id"]: r for r in roles}

    # children_of[role_id] = [child dicts that report_to this role]
    children_of: dict[str, list[dict[str, Any]]] = {r["id"]: [] for r in roles}
    for r in roles:
        parent = r.get("reports_to", "owner")
        if parent in children_of:
            children_of[parent].append(r)

    # reviewer_roles = set of role_ids that appear as gate reviewers
    reviewer_roles: set[str] = set()
    for s in work_item_templates:
        gate = s.get("gate")
        if gate and gate.get("reviewer_role"):
            reviewer_roles.add(gate["reviewer_role"])

    # Compute hierarchy depth per role
    def _depth(role_id: str, seen: set[str] | None = None) -> int:
        if seen is None:
            seen = set()
        if role_id in seen or role_id not in role_map:
            return 0
        seen.add(role_id)
        parent = role_map[role_id].get("reports_to", "owner")
        if parent == "owner" or parent not in role_map:
            return 0
        return 1 + _depth(parent, seen)

    max_depth = max((_depth(r["id"]) for r in roles), default=0)

    # ── 2. Classify each role and build RoleConfig ───────────────────
    enriched_roles: list[RoleConfig] = []

    for r in roles:
        rid = r["id"]
        name = r.get("name", rid)
        responsibility = r.get("responsibility", "")
        reports_to = r.get("reports_to", "owner")
        can_spawn = r.get("can_spawn", [])
        children = children_of.get(rid, [])

        is_coordinator = bool(children) or bool(can_spawn)
        is_reviewer = rid in reviewer_roles
        is_worker = not is_coordinator and not is_reviewer

        # ── runtime_policy ──
        if is_coordinator:
            downstream = [c["id"] for c in children]
            if can_spawn:
                for candidate in can_spawn:
                    if candidate not in downstream:
                        downstream.append(candidate)
            runtime_policy = RoleRuntimePolicyConfig(
                execution_strategy="native",
                allowed_downstream_roles=downstream,
            )
        elif is_reviewer:
            runtime_policy = RoleRuntimePolicyConfig(
                execution_strategy="native",
                default_turn_type="review",
            )
        else:
            runtime_policy = RoleRuntimePolicyConfig(
                execution_strategy="auto",
            )

        # ── prompt_refs ──
        prompt_parts = [f"You are the {name}. {responsibility}"]
        if is_coordinator:
            child_names = ", ".join(c.get("name", c["id"]) for c in children)
            if child_names:
                prompt_parts.append(
                    f"Coordinate work with: {child_names}. "
                    "Delegate tasks appropriately and aggregate results."
                )
        if is_reviewer:
            prompt_parts.append(
                "Review outputs for quality and correctness. "
                "Approve quality work or request changes."
            )
        if is_worker:
            prompt_parts.append(
                "Focus on delivering quality work within your area of expertise."
            )
        prompt_refs = [" ".join(prompt_parts), *list(r.get("prompt_refs") or [])]

        # ── preferred_external_agent ──
        responsibility_lower = responsibility.lower()
        preferred_external_agent = r.get("preferred_external_agent")
        if any(kw in responsibility_lower for kw in _ENG_KEYWORDS):
            preferred_external_agent = preferred_external_agent or "claude_code"

        raw_runtime_policy = r.get("runtime_policy")
        if isinstance(raw_runtime_policy, dict):
            runtime_policy = RoleRuntimePolicyConfig.model_validate({
                **runtime_policy.model_dump(),
                **raw_runtime_policy,
            })

        enriched_roles.append(RoleConfig(
            id=rid,
            name=name,
            responsibility=responsibility,
            reports_to=reports_to,
            icon=r.get("icon"),
            can_spawn=can_spawn,
            tools=list(r.get("tools") or []),
            prompt_refs=prompt_refs,
            skill_refs=list(r.get("skill_refs") or []),
            runtime_policy=runtime_policy,
            preferred_external_agent=preferred_external_agent,
            capabilities=list(r.get("capabilities") or []),
            role_type=str(r.get("role_type") or ("coordinator" if is_coordinator else "reviewer" if is_reviewer else "worker")),
        ))

    # ── 3. Build enriched work-item template hints ───────────────────
    # Lookup: role_id → enriched RoleConfig
    role_config_map: dict[str, RoleConfig] = {rc.id: rc for rc in enriched_roles}

    enriched_templates: list[dict[str, Any]] = []
    for s in work_item_templates:
        sid = s["id"]
        role_id = s.get("role_id", "")
        rc = role_config_map.get(role_id)
        role_name = rc.name if rc else role_id

        # Inherit execution strategy from role
        exec_strategy = (
            rc.runtime_policy.execution_strategy if rc else "auto"
        )
        ext_agent = rc.preferred_external_agent if rc else None

        raw_gate = s.get("gate")
        description = s.get(
            "description",
            f"{s.get('title', sid)} work item owned by {role_name}",
        )
        enriched_templates.append({
            "id": sid,
            "title": s.get("title", sid),
            "description": description,
            "role_id": role_id,
            "dependencies": list(s.get("dependencies", []) or []),
            "parallel_group": s.get("parallel_group"),
            "turn_type": str(s.get("turn_type") or "execute"),
            "execution_strategy": exec_strategy,
            "preferred_external_agent": ext_agent,
            "review_owner_role_id": str((raw_gate or {}).get("reviewer_role", "") or ""),
            "metadata": {
                "source": "market_work_item_template",
                "template_role_name": role_name,
                "gate": dict(raw_gate or {}),
            },
        })

    # ── 4. Infer RuntimePolicyConfig from org/template characteristics ──
    has_gates = any(s.get("gate") for s in work_item_templates)
    has_parallel = any(s.get("parallel_group") for s in work_item_templates)

    comm_cfg = CommunicationPolicyConfig(
        default_mode="broadcast" if max_depth > 3 else "dm",
        blocking_default=has_gates,
        allow_broadcast=True,
    )
    memory_cfg = MemoryPolicyConfig(
        include_role_memory=False,
        include_project_memory=False,
        recent_history_lines=12,
    )
    handoff_cfg = HandoffPolicyConfig(
        require_structured_handoff=True,
        require_ack=has_gates,
        include_risks=True,
        include_open_questions=True,
    )
    review_cfg = ReviewPolicyConfig(
        strict_gate_inference=has_gates,
        require_reviewer_role=has_gates,
        allow_human_override=True,
    )
    parallel_cfg = ParallelPolicyConfig(
        auto_dispatch=has_parallel,
    )

    wf_policy = RuntimePolicyConfig(
        communication=comm_cfg,
        memory=memory_cfg,
        handoff=handoff_cfg,
        review=review_cfg,
        parallel=parallel_cfg,
    )

    return enriched_roles, enriched_templates, wf_policy


def _prefix_role_id(prefix: str, role_id: str) -> str:
    return f"{prefix}{role_id}" if prefix else role_id


def apply_architecture_preset_to_config(
    config: Any,
    preset_id: str,
    *,
    strategy: str = "namespace",
    clear_existing: bool = True,
    organization_id: str | None = None,
    organization_name: str | None = None,
) -> InstalledPackageInfo:
    """Apply a built-in architecture preset as the active custom org.

    This is the shared implementation for UI and CLI entry points. It keeps
    architecture presets org-first: roles define the hierarchy, and custom mode
    derives the runtime collaboration plan from that hierarchy at execution time.
    """

    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"Preset '{preset_id}' not found")
    if strategy not in {"namespace", "overwrite"}:
        raise ValueError("strategy must be 'namespace' or 'overwrite'")

    prefix = f"{preset_id}:" if strategy == "namespace" else ""
    enriched_roles, enriched_templates, runtime_policy = infer_collaboration_config(
        preset.roles,
        preset.work_item_templates,
    )

    if clear_existing:
        config.org.roles = []
        config.org.employees = []
        config.org.installed_packages = []

    role_ids: list[str] = []
    for role in enriched_roles:
        role_copy = role.model_copy(deep=True)
        role_copy.id = _prefix_role_id(prefix, role_copy.id)
        if prefix and role_copy.reports_to and role_copy.reports_to != "owner":
            role_copy.reports_to = _prefix_role_id(prefix, role_copy.reports_to)
        if prefix:
            role_copy.can_spawn = [_prefix_role_id(prefix, role_id) for role_id in role_copy.can_spawn]
            role_copy.runtime_policy.allowed_downstream_roles = [
                _prefix_role_id(prefix, role_id)
                for role_id in role_copy.runtime_policy.allowed_downstream_roles
            ]
            if role_copy.runtime_policy.review_role:
                role_copy.runtime_policy.review_role = _prefix_role_id(prefix, role_copy.runtime_policy.review_role)
        config.org.roles.append(role_copy)
        role_ids.append(role_copy.id)

    work_item_template_ids = [
        _prefix_role_id(prefix, str(template.get("id", "") or "").strip())
        for template in enriched_templates
        if str(template.get("id", "") or "").strip()
    ]

    preset_org_name = str(organization_name or preset.name).strip()
    preset_org_id = str(organization_id or slugify_organization_name(preset_id)).strip()
    config.org.organization_id = preset_org_id
    config.org.organization_name = preset_org_name
    config.org.company_name = preset_org_name
    config.org.company_profile = "custom"
    if "custom" not in config.org.company_profiles:
        config.org.company_profiles.append("custom")

    role_id_set = {role.id for role in config.org.roles}
    top_level_role_ids = [
        role.id
        for role in config.org.roles
        if role.id in role_ids and (role.reports_to == "owner" or role.reports_to not in role_id_set)
    ]
    config.org.final_decider_role_id = top_level_role_ids[0] if len(top_level_role_ids) == 1 else None
    config.org.runtime_policies["custom"] = runtime_policy

    info = InstalledPackageInfo(
        package_id=preset_id,
        name=preset.name,
        version="1.0.0",
        installed_at=datetime.now(timezone.utc).isoformat(),
        source_path="builtin",
        role_ids=role_ids,
        template_ids=work_item_template_ids,
        work_item_template_ids=work_item_template_ids,
    )
    config.org.installed_packages.append(info)
    return info


# ---------------------------------------------------------------------------
# Built-in architecture presets (pure structure)
# ---------------------------------------------------------------------------

ARCHITECTURE_PRESETS: list[ArchitectureBlueprint] = [
    *load_architecture_presets_from_yaml(),

    # ── Startup Studio ────────────────────────────────────────────────
    ArchitectureBlueprint(
        id="startup-studio",
        name="Startup Studio",
        description="Lean full-stack startup team. CEO sets strategy, CTO drives tech decisions, engineers and designers build in parallel, QA validates.",
        category="startup",
        collaboration_pattern="hub_spoke",
        dag_summary="CEO\u2192CTO\u2192[Eng\u2225Des]\u2192QA\u2192Review\u2192Deploy",
        tags=["full-stack", "agile", "mvp", "small-team"],
        team_size="3-8",
        emoji="\U0001F680",
        color="#3498db",
        roles=[
            {"id": "ceo", "name": "CEO", "responsibility": "Product vision, strategy, and final decisions", "reports_to": "owner", "can_spawn": ["cto"]},
            {"id": "cto", "name": "CTO", "responsibility": "Technical architecture and engineering leadership", "reports_to": "ceo", "can_spawn": ["engineer", "designer"]},
            {"id": "engineer", "name": "Engineer", "responsibility": "Full-stack development and implementation", "reports_to": "cto", "can_spawn": []},
            {"id": "designer", "name": "Designer", "responsibility": "UI/UX design, prototyping, and user research", "reports_to": "cto", "can_spawn": []},
            {"id": "qa", "name": "QA Engineer", "responsibility": "Testing, quality assurance, and bug tracking", "reports_to": "cto", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "planning", "title": "Planning", "role_id": "ceo", "dependencies": [], "parallel_group": None},
            {"id": "architecture", "title": "Architecture", "role_id": "cto", "dependencies": ["planning"], "parallel_group": None},
            {"id": "design", "title": "Design", "role_id": "designer", "dependencies": ["architecture"], "parallel_group": "build"},
            {"id": "development", "title": "Development", "role_id": "engineer", "dependencies": ["architecture"], "parallel_group": "build"},
            {"id": "testing", "title": "Testing", "role_id": "qa", "dependencies": ["design", "development"], "parallel_group": None},
            {"id": "review", "title": "Review", "role_id": "cto", "dependencies": ["testing"], "parallel_group": None, "gate": {"type": "review", "reviewer_role": "ceo"}},
            {"id": "deploy", "title": "Deploy", "role_id": "engineer", "dependencies": ["review"], "parallel_group": None},
        ],
    ),

    # ── Enterprise Corp ───────────────────────────────────────────────
    ArchitectureBlueprint(
        id="enterprise-corp",
        name="Enterprise Corporation",
        description="Formal corporate hierarchy with department heads, approval gates, and compliance checkpoints. Suited for regulated industries.",
        category="enterprise",
        collaboration_pattern="hierarchical",
        dag_summary="PM\u2192CEO\u2713\u2192[Tech\u2225UX]\u2192Impl\u2192QA\u2192Security\u2713\u2192Prep\u2192Release\u2713",
        tags=["corporate", "compliance", "governance", "large-team"],
        team_size="10-50",
        emoji="\U0001F3E2",
        color="#2c3e50",
        roles=[
            {"id": "ceo", "name": "CEO", "responsibility": "Executive leadership, vision, investor relations", "reports_to": "owner", "can_spawn": ["vp_eng", "vp_product", "cfo"]},
            {"id": "vp_eng", "name": "VP Engineering", "responsibility": "Engineering organization leadership", "reports_to": "ceo", "can_spawn": ["tech_lead", "devops_lead"]},
            {"id": "vp_product", "name": "VP Product", "responsibility": "Product strategy and roadmap", "reports_to": "ceo", "can_spawn": ["product_manager", "ux_lead"]},
            {"id": "cfo", "name": "CFO", "responsibility": "Finance, budgeting, compliance", "reports_to": "ceo", "can_spawn": []},
            {"id": "tech_lead", "name": "Tech Lead", "responsibility": "Technical execution and code quality", "reports_to": "vp_eng", "can_spawn": ["engineer"]},
            {"id": "devops_lead", "name": "DevOps Lead", "responsibility": "Infrastructure, CI/CD, monitoring", "reports_to": "vp_eng", "can_spawn": []},
            {"id": "product_manager", "name": "Product Manager", "responsibility": "Feature specs, user stories, prioritization", "reports_to": "vp_product", "can_spawn": []},
            {"id": "ux_lead", "name": "UX Lead", "responsibility": "User experience design and research", "reports_to": "vp_product", "can_spawn": ["designer"]},
            {"id": "engineer", "name": "Software Engineer", "responsibility": "Development and implementation", "reports_to": "tech_lead", "can_spawn": []},
            {"id": "designer", "name": "UI Designer", "responsibility": "Visual design and prototyping", "reports_to": "ux_lead", "can_spawn": []},
            {"id": "qa_lead", "name": "QA Lead", "responsibility": "Quality assurance strategy and testing", "reports_to": "vp_eng", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "requirements", "title": "Requirements", "role_id": "product_manager", "dependencies": [], "parallel_group": None},
            {"id": "exec_review", "title": "Executive Review", "role_id": "ceo", "dependencies": ["requirements"], "parallel_group": None, "gate": {"type": "approval", "reviewer_role": "ceo"}},
            {"id": "tech_design", "title": "Technical Design", "role_id": "tech_lead", "dependencies": ["exec_review"], "parallel_group": "design"},
            {"id": "ux_design", "title": "UX Design", "role_id": "ux_lead", "dependencies": ["exec_review"], "parallel_group": "design"},
            {"id": "implementation", "title": "Implementation", "role_id": "engineer", "dependencies": ["tech_design", "ux_design"], "parallel_group": None},
            {"id": "qa", "title": "Quality Assurance", "role_id": "qa_lead", "dependencies": ["implementation"], "parallel_group": None},
            {"id": "security_review", "title": "Security Review", "role_id": "devops_lead", "dependencies": ["qa"], "parallel_group": None, "gate": {"type": "review", "reviewer_role": "vp_eng"}},
            {"id": "preprod_deploy", "title": "Preprod Deploy", "role_id": "devops_lead", "dependencies": ["security_review"], "parallel_group": None},
            {"id": "release", "title": "Production Release", "role_id": "devops_lead", "dependencies": ["preprod_deploy"], "parallel_group": None, "gate": {"type": "approval", "reviewer_role": "vp_eng"}},
        ],
    ),

    # ── Creative Agency ───────────────────────────────────────────────
    ArchitectureBlueprint(
        id="creative-agency",
        name="Creative Agency",
        description="Client-driven creative team. Account manager handles client relations, creative director sets the vision, specialists execute across disciplines.",
        category="agency",
        collaboration_pattern="review_loop",
        dag_summary="Brief\u2192Concept\u2192[Visual\u2225Copy]\u2192Build\u2192Review\u2713\u2192Client\u2713",
        tags=["creative", "client-work", "design", "marketing"],
        team_size="5-15",
        emoji="\U0001F3A8",
        color="#e74c3c",
        roles=[
            {"id": "account_manager", "name": "Account Manager", "responsibility": "Client relations, project scoping, deliverable tracking", "reports_to": "owner", "can_spawn": ["creative_director"]},
            {"id": "creative_director", "name": "Creative Director", "responsibility": "Creative vision, brand consistency, quality standards", "reports_to": "account_manager", "can_spawn": ["designer", "copywriter", "developer"]},
            {"id": "designer", "name": "Visual Designer", "responsibility": "Graphics, layouts, brand assets, UI mockups", "reports_to": "creative_director", "can_spawn": []},
            {"id": "copywriter", "name": "Copywriter", "responsibility": "Copy, content strategy, messaging, tone of voice", "reports_to": "creative_director", "can_spawn": []},
            {"id": "developer", "name": "Web Developer", "responsibility": "Frontend development, CMS, landing pages", "reports_to": "creative_director", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "brief", "title": "Client Brief", "role_id": "account_manager", "dependencies": [], "parallel_group": None},
            {"id": "concept", "title": "Creative Concept", "role_id": "creative_director", "dependencies": ["brief"], "parallel_group": None},
            {"id": "visual_design", "title": "Visual Design", "role_id": "designer", "dependencies": ["concept"], "parallel_group": "create"},
            {"id": "copy", "title": "Copywriting", "role_id": "copywriter", "dependencies": ["concept"], "parallel_group": "create"},
            {"id": "build", "title": "Development", "role_id": "developer", "dependencies": ["visual_design", "copy"], "parallel_group": None},
            {"id": "creative_review", "title": "Creative Review", "role_id": "creative_director", "dependencies": ["build"], "parallel_group": None, "gate": {"type": "review", "reviewer_role": "creative_director"}},
            {"id": "client_approval", "title": "Client Approval", "role_id": "account_manager", "dependencies": ["creative_review"], "parallel_group": None, "gate": {"type": "approval", "reviewer_role": "account_manager"}},
        ],
    ),

    # ── Research Lab ──────────────────────────────────────────────────
    ArchitectureBlueprint(
        id="research-lab",
        name="Research Lab",
        description="Academic-style research team. Principal investigator leads hypothesis-driven research with peer review and reproducibility checks.",
        category="research",
        collaboration_pattern="pipeline",
        dag_summary="Hypothesis\u2192LitReview\u2192Design\u2192[Data\u2225Experiment]\u2192Analysis\u2192PeerReview\u2713\u2192Publish",
        tags=["academic", "research", "data-science", "peer-review"],
        team_size="3-10",
        emoji="\U0001F52C",
        color="#9b59b6",
        roles=[
            {"id": "pi", "name": "Principal Investigator", "responsibility": "Research direction, hypothesis formulation, publication oversight", "reports_to": "owner", "can_spawn": ["researcher", "data_engineer"]},
            {"id": "researcher", "name": "Research Scientist", "responsibility": "Experiment design, analysis, paper writing", "reports_to": "pi", "can_spawn": []},
            {"id": "data_engineer", "name": "Data Engineer", "responsibility": "Data pipelines, infrastructure, reproducibility", "reports_to": "pi", "can_spawn": []},
            {"id": "reviewer", "name": "Peer Reviewer", "responsibility": "Critical review, methodology validation, feedback", "reports_to": "pi", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "hypothesis", "title": "Hypothesis", "role_id": "pi", "dependencies": [], "parallel_group": None},
            {"id": "lit_review", "title": "Literature Review", "role_id": "researcher", "dependencies": ["hypothesis"], "parallel_group": None},
            {"id": "experiment_design", "title": "Experiment Design", "role_id": "researcher", "dependencies": ["lit_review"], "parallel_group": None},
            {"id": "data_collection", "title": "Data Pipeline", "role_id": "data_engineer", "dependencies": ["experiment_design"], "parallel_group": "exec"},
            {"id": "experiment", "title": "Run Experiment", "role_id": "researcher", "dependencies": ["experiment_design"], "parallel_group": "exec"},
            {"id": "analysis", "title": "Analysis", "role_id": "researcher", "dependencies": ["data_collection", "experiment"], "parallel_group": None},
            {"id": "peer_review", "title": "Peer Review", "role_id": "reviewer", "dependencies": ["analysis"], "parallel_group": None, "gate": {"type": "review", "reviewer_role": "pi"}},
            {"id": "publication", "title": "Publication", "role_id": "pi", "dependencies": ["peer_review"], "parallel_group": None},
        ],
    ),

    # ── DevOps Pipeline ───────────────────────────────────────────────
    ArchitectureBlueprint(
        id="devops-pipeline",
        name="DevOps Pipeline",
        description="Infrastructure-focused team optimized for CI/CD, monitoring, and rapid deployment cycles with SRE practices.",
        category="engineering",
        collaboration_pattern="pipeline",
        dag_summary="Plan\u2192Dev\u2192CI\u2192[Security\u2225Tests]\u2192Prep\u2192Canary\u2713\u2192Production",
        tags=["devops", "sre", "infrastructure", "automation"],
        team_size="4-12",
        emoji="\u2699\uFE0F",
        color="#27ae60",
        roles=[
            {"id": "sre_lead", "name": "SRE Lead", "responsibility": "Reliability strategy, incident response, SLO management", "reports_to": "owner", "can_spawn": ["platform_eng", "security_eng"]},
            {"id": "platform_eng", "name": "Platform Engineer", "responsibility": "Infrastructure as code, Kubernetes, cloud architecture", "reports_to": "sre_lead", "can_spawn": []},
            {"id": "security_eng", "name": "Security Engineer", "responsibility": "Security audits, vulnerability scanning, compliance", "reports_to": "sre_lead", "can_spawn": []},
            {"id": "developer", "name": "Developer", "responsibility": "Application development and feature delivery", "reports_to": "sre_lead", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "plan", "title": "Sprint Planning", "role_id": "sre_lead", "dependencies": [], "parallel_group": None},
            {"id": "develop", "title": "Development", "role_id": "developer", "dependencies": ["plan"], "parallel_group": None},
            {"id": "ci", "title": "CI Pipeline", "role_id": "platform_eng", "dependencies": ["develop"], "parallel_group": None},
            {"id": "security_scan", "title": "Security Scan", "role_id": "security_eng", "dependencies": ["ci"], "parallel_group": "validate"},
            {"id": "integration_test", "title": "Integration Tests", "role_id": "platform_eng", "dependencies": ["ci"], "parallel_group": "validate"},
            {"id": "preprod_deploy", "title": "Preprod Deploy", "role_id": "platform_eng", "dependencies": ["security_scan", "integration_test"], "parallel_group": None},
            {"id": "canary", "title": "Canary Release", "role_id": "sre_lead", "dependencies": ["preprod_deploy"], "parallel_group": None, "gate": {"type": "approval", "reviewer_role": "sre_lead"}},
            {"id": "production", "title": "Production", "role_id": "platform_eng", "dependencies": ["canary"], "parallel_group": None},
        ],
    ),

    # ── Content Studio ────────────────────────────────────────────────
    ArchitectureBlueprint(
        id="content-studio",
        name="Content Studio",
        description="Content creation pipeline for blogs, social media, and documentation. Editor-in-chief oversees quality and publishing cadence.",
        category="media",
        collaboration_pattern="pipeline",
        dag_summary="Topic\u2192[Research\u2225SEO]\u2192Draft\u2192EditReview\u2713\u2192Publish\u2192Distribute",
        tags=["content", "writing", "social-media", "publishing"],
        team_size="3-8",
        emoji="\U0001F4DD",
        color="#f39c12",
        roles=[
            {"id": "editor_in_chief", "name": "Editor-in-Chief", "responsibility": "Editorial strategy, content calendar, quality standards", "reports_to": "owner", "can_spawn": ["writer", "seo_specialist"]},
            {"id": "writer", "name": "Content Writer", "responsibility": "Research, drafting, and revising articles", "reports_to": "editor_in_chief", "can_spawn": []},
            {"id": "seo_specialist", "name": "SEO Specialist", "responsibility": "Keyword research, optimization, analytics", "reports_to": "editor_in_chief", "can_spawn": []},
            {"id": "social_manager", "name": "Social Media Manager", "responsibility": "Distribution, engagement, cross-promotion", "reports_to": "editor_in_chief", "can_spawn": []},
        ],
        work_item_templates=[
            {"id": "topic_planning", "title": "Topic Planning", "role_id": "editor_in_chief", "dependencies": [], "parallel_group": None},
            {"id": "research", "title": "Research & Outline", "role_id": "writer", "dependencies": ["topic_planning"], "parallel_group": None},
            {"id": "seo_research", "title": "SEO Research", "role_id": "seo_specialist", "dependencies": ["topic_planning"], "parallel_group": None},
            {"id": "drafting", "title": "Drafting", "role_id": "writer", "dependencies": ["research", "seo_research"], "parallel_group": None},
            {"id": "editorial_review", "title": "Editorial Review", "role_id": "editor_in_chief", "dependencies": ["drafting"], "parallel_group": None, "gate": {"type": "review", "reviewer_role": "editor_in_chief"}},
            {"id": "publish", "title": "Publish", "role_id": "editor_in_chief", "dependencies": ["editorial_review"], "parallel_group": None},
            {"id": "distribute", "title": "Social Distribution", "role_id": "social_manager", "dependencies": ["publish"], "parallel_group": None},
        ],
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_presets() -> list[ArchitectureBlueprint]:
    """Return all built-in architecture presets."""
    return ARCHITECTURE_PRESETS


def get_preset(preset_id: str) -> ArchitectureBlueprint | None:
    """Return a single preset by ID."""
    for p in ARCHITECTURE_PRESETS:
        if p.id == preset_id:
            return p
    return None


def get_preset_categories() -> list[str]:
    """Return unique categories across all presets."""
    return sorted({p.category for p in ARCHITECTURE_PRESETS})
