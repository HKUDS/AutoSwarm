"""Local talent-market helpers for importing and hiring agency agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from yaml import YAMLError

from opc.core.config import EmployeeConfig, OPCConfig, TalentTemplateConfig


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "academic": "Academic research and scholarly analysis across humanities and social science disciplines.",
    "design": "Visual design, brand systems, UX thinking, and creative asset direction.",
    "engineering": "Software engineering, architecture, implementation, integration, and technical delivery.",
    "examples": "Reference or demo-style prompts that illustrate processes rather than specialized staffing.",
    "finance": "Finance, investment research, financial modeling, valuation, accounting, tax, and portfolio analysis.",
    "general": "General-purpose execution support for broad, lightweight, or fallback work across roles.",
    "gis": "Geospatial data, mapping, spatial analysis, remote sensing and GIS engineering.",
    "healthcare": "Healthcare operations, clinical workflows and domain-specific health analysis.",
    "game-development": "Game systems, content, technical art, narrative, and interactive development.",
    "marketing": "Audience growth, messaging, campaigns, content strategy, and go-to-market execution.",
    "paid-media": "Performance marketing, media buying, attribution, and campaign optimization.",
    "product": "Product strategy, prioritization, discovery, user insight, and roadmap decisions.",
    "project-management": "Planning, delivery coordination, process management, and operational tracking.",
    "sales": "Pipeline development, customer discovery, solution positioning, and deal support.",
    "security": "Threat modeling, application security, incident response and security verification.",
    "research": "Research design, investigation, synthesis and evidence-based analysis.",
    "spatial-computing": "XR, visionOS, 3D interfaces, immersive experiences, and spatial product work.",
    "specialized": "Specialized domain experts for niche industries, compliance, process design, and custom operations.",
    "strategy": "High-level planning, operating models, and cross-functional execution playbooks.",
    "support": "Operational support, reporting, compliance, maintenance, and service continuity.",
    "testing": "QA, validation, benchmarking, audit, and evidence-based quality checks.",
}
_CATEGORY_PREFIXES = tuple(sorted(_CATEGORY_DESCRIPTIONS.keys(), key=len, reverse=True))
_IGNORED_TEMPLATE_STEMS = {
    "readme",
    "integration-readme",
    "integrations-readme",
    "pull_request_template",
    "issue_template",
    "contributing",
    "changelog",
    "license",
}
_NON_TALENT_RECURSIVE_DIRS = {"integrations", "scripts"}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", value.strip().lower()).strip("-")
    return slug or "talent"


def _tokenize_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9+-]{2,}", text.lower())


def _extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    frontmatter_text = match.group(1)
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except YAMLError:
        frontmatter = _parse_relaxed_frontmatter(frontmatter_text)
    body = text[match.end():].strip()
    return frontmatter, body


def _parse_relaxed_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    """Parse simple frontmatter that looks YAML-like but isn't strictly valid YAML."""
    parsed: dict[str, Any] = {}
    active_key: str | None = None

    for raw_line in frontmatter_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and active_key:
            current = parsed.setdefault(active_key, [])
            if not isinstance(current, list):
                current = [str(current)]
            current.append(stripped[2:].strip())
            parsed[active_key] = current
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        active_key = key.strip()
        parsed[active_key] = _coerce_frontmatter_value(value.strip())

    return parsed


def _coerce_frontmatter_value(value: str) -> Any:
    if not value:
        return ""
    if value.startswith(('"', "'", "[", "{")):
        try:
            return yaml.safe_load(value)
        except YAMLError:
            return value.strip().strip("\"'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return value.strip().strip("\"'")


def _infer_category_from_stem(stem: str) -> str | None:
    normalized = stem.strip().lower()
    for category in _CATEGORY_PREFIXES:
        if normalized == category or normalized.startswith(f"{category}-"):
            return category
    return None


def _should_ignore_template_path(path: Path) -> bool:
    filename = path.name.strip().lower()
    stem = path.stem.strip().lower().lstrip(".")
    if filename.startswith("."):
        return True
    if stem in _IGNORED_TEMPLATE_STEMS:
        return True
    if stem.endswith("-readme") or stem.endswith("_readme"):
        return True
    return False


def _has_named_frontmatter_template(path: Path) -> bool:
    try:
        frontmatter, _ = _extract_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return bool(str(frontmatter.get("name", "")).strip())


def _looks_like_prompt_path(ref: str) -> bool:
    value = str(ref or "").strip()
    if not value:
        return False
    if "\n" in value:
        return False
    if len(value) > 260:
        return False
    if value.startswith(("You ", "Act ", "Focus ", "Review ", "Write ")):
        return False
    if " " in value and "/" not in value and "\\" not in value:
        return False
    return True


def resolve_prompt_refs(refs: list[str], opc_home: Path) -> list[str]:
    resolved: list[str] = []
    for ref in refs:
        value = str(ref or "").strip()
        if not value:
            continue
        if _looks_like_prompt_path(value):
            try:
                path = Path(value)
                if not path.is_absolute():
                    path = opc_home / path
                if path.exists() and path.is_file():
                    resolved.append(path.read_text(encoding="utf-8").strip())
                    continue
            except OSError:
                pass
        resolved.append(value)
    return [item for item in resolved if item]


def _is_placeholder_employee(employee: EmployeeConfig) -> bool:
    metadata = dict(employee.metadata or {})
    return bool(metadata.get("is_default_employee") or metadata.get("is_fallback_employee"))


class HireError(Exception):
    """Base class for recoverable failures in TalentMarket.hire_template."""


class RoleAlreadyHiredError(HireError):
    """Raised when a role already has a non-placeholder employee."""

    def __init__(self, role_id: str, existing_employee_id: str) -> None:
        self.role_id = role_id
        self.existing_employee_id = existing_employee_id
        super().__init__(
            f"Role '{role_id}' already has employee '{existing_employee_id}'.",
        )


class TalentMarket:
    """Imports talent templates from local markdown files and hires employees."""

    def __init__(self, opc_home: Path, config: OPCConfig) -> None:
        self.opc_home = opc_home
        self.config = config

    def list_templates(self) -> list[TalentTemplateConfig]:
        return self.list_available_templates()

    def list_employees(self) -> list[EmployeeConfig]:
        return sorted(self.config.org.employees, key=lambda item: (item.role_id, item.name.lower()))

    def list_adaptive_staffing_employees(self, *, project_id: str) -> list[EmployeeConfig]:
        """Recall staff from this org and its approved adaptive runs in this project."""
        from opc.core.employee_registry import is_placeholder_employee, load_employee_registry
        from opc.core.org_config import RUNTIME_ORG_CONFIGS_DIRNAME

        employees = {e.employee_id: e for e in self.list_employees() if not is_placeholder_employee(e)}
        source_org_id = self.config.org.organization_id
        paths = [path for directory in ("company_orgs", RUNTIME_ORG_CONFIGS_DIRNAME)
                 for path in (self.opc_home / "config" / directory).glob("org_adaptive_*_config.yaml")]
        for path in sorted(paths):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                metadata = data.get("metadata", {})
                if (metadata.get("adaptive_source_organization_id") != source_org_id
                        or metadata.get("adaptive_source_project_id") != project_id):
                    continue
                org_id = str(data["organization_id"])
                for employee in load_employee_registry(self.opc_home, org_id):
                    if not is_placeholder_employee(employee) and employee.employee_id not in employees:
                        employee.metadata["staffing_source_organization_id"] = org_id
                        employees[employee.employee_id] = employee
            except (OSError, ValueError, KeyError, TypeError, AttributeError, YAMLError):
                continue
        return sorted(employees.values(), key=lambda item: (item.role_id, item.name.lower()))

    def get_template(self, template_id: str) -> TalentTemplateConfig | None:
        normalized = str(template_id or "").strip()
        if not normalized:
            return None
        return next((item for item in self.list_available_templates() if item.id == normalized), None)

    def list_available_templates(self) -> list[TalentTemplateConfig]:
        """Return every template the talent market can hire from the talent catalog."""
        templates_by_id: dict[str, TalentTemplateConfig] = {}
        for template in self.scan_local_talent():
            templates_by_id[template.id] = template
        try:
            from opc.market.talent_presets import get_all_talent_presets

            for raw in get_all_talent_presets():
                template = self._template_from_preset(raw)
                templates_by_id.setdefault(template.id, template)
        except Exception:
            pass
        return sorted(templates_by_id.values(), key=lambda item: (item.category, item.name.lower()))

    def ensure_template_available(self, template_id: str) -> TalentTemplateConfig | None:
        """Resolve a template from the talent catalog without mutating org config."""
        normalized = str(template_id or "").strip()
        if not normalized:
            return None
        return self.get_template(normalized)

    def build_employee_id(self, *, role_id: str, template_id: str) -> str:
        _ = role_id
        return str(template_id or "").strip()

    @staticmethod
    def _attach_employee_to_role(employee: EmployeeConfig, role_id: str) -> EmployeeConfig:
        normalized_role_id = str(role_id or "").strip()
        if not normalized_role_id:
            return employee
        metadata = dict(employee.metadata or {})
        for key in ("home_role_ids", "staffed_role_ids"):
            values = [
                str(item).strip()
                for item in list(metadata.get(key, []) or [])
                if str(item).strip()
            ]
            if normalized_role_id not in values:
                values.append(normalized_role_id)
            metadata[key] = values
        metadata.setdefault("home_role_id", str(employee.role_id or normalized_role_id).strip() or normalized_role_id)
        employee.metadata = metadata
        return employee

    def _remove_placeholders_for_role(self, role_id: str) -> None:
        normalized_role_id = str(role_id or "").strip()
        if not normalized_role_id:
            return
        self.config.org.employees = [
            item
            for item in self.config.org.employees
            if not (item.role_id == normalized_role_id and _is_placeholder_employee(item))
        ]

    def build_candidate_summary(self, template: TalentTemplateConfig) -> dict[str, Any]:
        return {
            "template_id": template.id,
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "category_description": self.describe_category(template.category),
            "domains": list(template.domains),
            "tags": list(template.tags),
        }

    def describe_category(self, category: str) -> str:
        normalized = (category or "").strip().lower()
        if normalized in _CATEGORY_DESCRIPTIONS:
            return _CATEGORY_DESCRIPTIONS[normalized]
        return f"{normalized.replace('-', ' ').strip().title()} related talent and execution support."

    def list_category_catalog(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for template in self.list_available_templates():
            category = (template.category or "general").strip().lower() or "general"
            counts[category] = counts.get(category, 0) + 1
        catalog = [
            {
                "category": category,
                "description": self.describe_category(category),
                "template_count": count,
            }
            for category, count in counts.items()
        ]
        return sorted(catalog, key=lambda item: (item["category"] != "engineering", item["category"]))

    def list_templates_by_categories(self, *, categories: list[str]) -> list[TalentTemplateConfig]:
        selected = {(category or "").strip().lower() for category in categories if str(category).strip()}
        if not selected:
            return []
        return [
            template for template in self.list_available_templates()
            if (template.category or "general").strip().lower() in selected
        ]

    def shortlist_templates_by_categories(
        self,
        *,
        categories: list[str],
        role_descriptions: list[str],
        limit: int = 6,
    ) -> list[TalentTemplateConfig]:
        selected = {(category or "").strip().lower() for category in categories if str(category).strip()}
        templates = [
            template for template in self.list_available_templates()
            if not selected or (template.category or "general").strip().lower() in selected
        ]
        if not templates:
            return []
        need_tokens = set(_tokenize_text(" ".join(role_descriptions)))
        scored: list[tuple[float, TalentTemplateConfig]] = []
        for template in templates:
            template_tokens = set(_tokenize_text(f"{template.name} {template.description}"))
            score = float(len(need_tokens & template_tokens))
            if template.description:
                score += 0.25
            scored.append((score, template))
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [template for _, template in scored[:limit]]

    def search_templates_for_need(
        self,
        *,
        role_id: str,
        domains: list[str],
        role_descriptions: list[str],
        limit: int = 5,
    ) -> list[TalentTemplateConfig]:
        scored: list[tuple[float, TalentTemplateConfig]] = []
        role_text = " ".join(role_descriptions).lower()
        need_tokens = {
            role_id.lower(),
            *[domain.lower() for domain in domains],
            *re.findall(r"[a-z0-9][a-z0-9+-]{2,}", role_text),
        }
        for template in self.list_available_templates():
            template_tokens = {
                template.category.lower(),
                template.name.lower(),
                *[domain.lower() for domain in template.domains],
                *[tag.lower() for tag in template.tags],
                *re.findall(r"[a-z0-9][a-z0-9+-]{2,}", template.description.lower()),
            }
            score = 0.0
            if role_id.lower() in template_tokens:
                score += 6.0
            score += float(len(need_tokens & template_tokens))
            if template.preferred_external_agent:
                score += 0.5
            if score > 0:
                scored.append((score, template))
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [template for _, template in scored[:limit]]

    def import_from_repo(self, repo_path: Path) -> list[TalentTemplateConfig]:
        repo_root = repo_path.expanduser().resolve()
        imported: list[TalentTemplateConfig] = []
        for category_dir in sorted(repo_root.iterdir()):
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue
            for markdown_path in self._iter_repo_template_paths(category_dir):
                template = self._parse_template(markdown_path, repo_root)
                if template is None:
                    continue
                self._write_prompt(template.id, markdown_path)
                imported.append(template)

        unique = {template.id: template for template in imported if template.id}
        return sorted(unique.values(), key=lambda item: (item.category, item.name.lower()))

    def _iter_repo_template_paths(self, category_dir: Path) -> list[Path]:
        top_level = category_dir.name.strip().lower()
        paths: list[Path] = []
        for markdown_path in sorted(category_dir.rglob("*.md")):
            if _should_ignore_template_path(markdown_path):
                continue
            if markdown_path.parent == category_dir:
                if top_level in {"examples", "strategy", *_NON_TALENT_RECURSIVE_DIRS} and not _has_named_frontmatter_template(markdown_path):
                    continue
                paths.append(markdown_path)
                continue
            if top_level in _NON_TALENT_RECURSIVE_DIRS:
                continue
            if _has_named_frontmatter_template(markdown_path):
                paths.append(markdown_path)
        return paths

    def _resolve_template(self, template_id: str) -> TalentTemplateConfig | None:
        return self.get_template(template_id)

    def hire_template(
        self,
        template_id: str,
        role_id: str,
        *,
        employee_name: str | None = None,
        employee_id: str | None = None,
    ) -> EmployeeConfig:
        template = self.ensure_template_available(template_id)
        if template is None:
            raise ValueError(f"Unknown talent template `{template_id}`.")

        employee_name = (employee_name or template.name).strip()
        if not employee_name:
            raise ValueError("Employee name cannot be empty.")

        displaced_ids = {
            item.employee_id
            for item in self.config.org.employees
            if item.role_id == role_id and _is_placeholder_employee(item)
        }
        chosen_id = self.build_employee_id(role_id=role_id, template_id=template.id)
        if any(
            item.employee_id == chosen_id and item.employee_id not in displaced_ids
            for item in self.config.org.employees
        ):
            existing = next(item for item in self.config.org.employees if item.employee_id == chosen_id)
            if not _is_placeholder_employee(existing):
                self._attach_employee_to_role(existing, role_id)
                self._remove_placeholders_for_role(role_id)
                return existing
            raise ValueError(f"Employee `{chosen_id}` already exists.")

        new_employee = EmployeeConfig(
            employee_id=chosen_id,
            template_id=template.id,
            name=employee_name,
            role_id=role_id,
            description=template.description,
            category=template.category,
            domains=[],
            tags=list(template.tags),
            prompt_refs=[template.prompt_ref] if template.prompt_ref else [],
            skill_refs=[],
            preferred_external_agent=template.preferred_external_agent,
            metadata={
                "source_repo": template.source_repo,
                "source_path": template.source_path,
                "source_revision": template.source_revision,
                "talent_template_name": template.name,
                "talent_template_category": template.category,
                "home_role_id": role_id,
                "home_role_ids": [role_id],
                "staffed_role_ids": [role_id],
            },
        )
        self.config.org.employees = [
            item for item in self.config.org.employees if item.employee_id not in displaced_ids
        ] + [
            new_employee,
        ]
        return new_employee

    def ensure_hire_template(
        self,
        template_id: str,
        role_id: str,
        *,
        employee_name: str | None = None,
        employee_id: str | None = None,
    ) -> EmployeeConfig:
        template = self.ensure_template_available(template_id)
        if template is None:
            raise ValueError(f"Unknown talent template `{template_id}`.")
        _ = employee_id
        desired_id = self.build_employee_id(role_id=role_id, template_id=template.id)
        existing = next((item for item in self.config.org.employees if item.employee_id == desired_id), None)
        if existing and not _is_placeholder_employee(existing):
            self._attach_employee_to_role(existing, role_id)
            self._remove_placeholders_for_role(role_id)
            return existing

        resolved_name = (employee_name or template.name).strip()
        if not resolved_name:
            raise ValueError("Employee name cannot be empty.")

        displaced_ids = {
            item.employee_id
            for item in self.config.org.employees
            if item.role_id == role_id
            and _is_placeholder_employee(item)
        }
        if existing is not None:
            displaced_ids.add(existing.employee_id)

        new_employee = EmployeeConfig(
            employee_id=desired_id,
            template_id=template.id,
            name=resolved_name,
            role_id=role_id,
            description=template.description,
            category=template.category,
            domains=[],
            tags=list(template.tags),
            prompt_refs=[template.prompt_ref] if template.prompt_ref else [],
            skill_refs=[],
            preferred_external_agent=template.preferred_external_agent,
            metadata={
                "source_repo": template.source_repo,
                "source_path": template.source_path,
                "source_revision": template.source_revision,
                "talent_template_name": template.name,
                "talent_template_category": template.category,
                "home_role_id": role_id,
                "home_role_ids": [role_id],
                "staffed_role_ids": [role_id],
            },
        )
        self.config.org.employees = [
            item for item in self.config.org.employees if item.employee_id not in displaced_ids
        ] + [
            new_employee,
        ]
        return new_employee

    def scan_local_talent(self) -> list[TalentTemplateConfig]:
        """Scan ``prompts/talent/*.md`` and return the local talent catalog."""
        talent_dir = self.opc_home / "prompts" / "talent"
        if not talent_dir.is_dir():
            return []
        found: dict[str, TalentTemplateConfig] = {}
        for md_path in sorted(talent_dir.glob("*.md")):
            tpl = self._parse_template(md_path, talent_dir)
            if tpl:
                found[tpl.id] = tpl
        return sorted(found.values(), key=lambda item: (item.category, item.name.lower()))

    def _template_from_preset(self, data: dict[str, Any]) -> TalentTemplateConfig:
        template_id = str(data.get("id") or data.get("template_id") or "").strip()
        name = str(data.get("name") or template_id).strip() or template_id
        return TalentTemplateConfig(
            id=template_id,
            name=name,
            description=str(data.get("description", "") or ""),
            category=str(data.get("category", "") or ""),
            domains=[
                str(item).strip()
                for item in list(data.get("domains", []) or [])
                if str(item).strip()
            ],
            tags=[
                str(item).strip()
                for item in list(data.get("tags", []) or [])
                if str(item).strip()
            ],
            prompt_ref=str(data.get("prompt_ref", "") or ""),
            preferred_external_agent=data.get("preferred_external_agent"),
            source_repo="builtin",
            source_path=str(data.get("source_path", "") or ""),
            source_revision=str(data.get("source_revision", "") or "builtin"),
            metadata={
                key: value
                for key, value in data.items()
                if key not in {"id", "template_id", "name", "description", "category", "domains", "tags", "prompt_ref", "preferred_external_agent", "source_path", "source_revision"}
            },
        )

    def import_local_templates(self, template_ids: list[str]) -> list[TalentTemplateConfig]:
        """Resolve selected templates from the local talent directory."""
        available = {t.id: t for t in self.scan_local_talent()}
        imported: list[TalentTemplateConfig] = []
        for tid in template_ids:
            tpl = available.get(tid)
            if tpl:
                imported.append(tpl)
        return sorted(imported, key=lambda t: (t.category, t.name.lower()))

    def _parse_template(self, path: Path, repo_root: Path) -> TalentTemplateConfig | None:
        if _should_ignore_template_path(path):
            return None
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _extract_frontmatter(text)
        if (repo_root.name.lower() == "talent"
                and _infer_category_from_stem(path.stem) in {"examples", "strategy"}
                and not frontmatter.get("name")):
            # Older imports flattened operational playbooks into this directory.
            # Keep their files, but do not present them as people to recruit.
            return None
        # If no frontmatter, derive name from first H1 heading or filename
        if not frontmatter or not frontmatter.get("name"):
            h1_match = re.match(r"^#\s+(.+)", body or text, re.MULTILINE)
            derived_name = h1_match.group(1).strip() if h1_match else path.stem.replace("-", " ").title()
            if not derived_name:
                return None
            frontmatter = dict(frontmatter) if frontmatter else {}
            frontmatter["name"] = derived_name
        category = self._resolve_template_category(path, repo_root, frontmatter)
        rel_path = str(path.relative_to(repo_root))
        name = str(frontmatter.get("name", path.stem)).strip() or path.stem
        description = str(frontmatter.get("description", "")).strip()
        template_id = self._build_template_id(path, repo_root, frontmatter, category)
        domains = [
            str(item).strip()
            for item in list(frontmatter.get("domains", []) or [])
            if str(item).strip()
        ]
        raw_tags = [
            str(item).strip()
            for item in list(frontmatter.get("tags", []) or [])
            if str(item).strip()
        ]
        tags = raw_tags or self._infer_tags(category, name, rel_path)
        if repo_root.name.strip().lower() == "talent" and path.parent == repo_root:
            prompt_ref = f"prompts/talent/{path.name}"
        else:
            prompt_ref = f"prompts/talent/{template_id}.md"
        preferred_external_agent = self._infer_preferred_external_agent(body, category)
        metadata = {
            key: value
            for key, value in frontmatter.items()
            if key not in {"id", "name", "description", "domains", "tags", "category"}
        }
        return TalentTemplateConfig(
            id=template_id,
            name=name,
            description=description,
            category=category,
            domains=domains,
            tags=tags,
            prompt_ref=prompt_ref,
            preferred_external_agent=preferred_external_agent,
            source_repo=str(repo_root),
            source_path=rel_path,
            source_revision="local",
            metadata=metadata,
        )

    def _resolve_template_category(self, path: Path, repo_root: Path, frontmatter: dict[str, Any]) -> str:
        explicit = str(frontmatter.get("category", "")).strip().lower()
        if explicit:
            return explicit

        parent_category = path.parent.name.strip().lower()
        repo_root_name = repo_root.name.strip().lower()
        try:
            first_part = path.relative_to(repo_root).parts[0].strip().lower()
        except (IndexError, ValueError):
            first_part = ""
        if first_part in _CATEGORY_DESCRIPTIONS:
            return first_part
        if parent_category and parent_category != repo_root_name:
            return parent_category

        inferred = _infer_category_from_stem(path.stem)
        if inferred:
            return inferred

        if repo_root_name == "talent":
            return "general"
        return parent_category or "general"

    def _build_template_id(
        self,
        path: Path,
        repo_root: Path,
        frontmatter: dict[str, Any],
        category: str,
    ) -> str:
        explicit = frontmatter.get("id")
        if explicit:
            return _slugify(str(explicit))

        if path.parent.name.strip().lower() == repo_root.name.strip().lower():
            inferred = _infer_category_from_stem(path.stem)
            if inferred == category:
                return _slugify(path.stem)

        return _slugify(f"{category}-{path.stem}")

    def _write_prompt(self, template_id: str, source_path: Path) -> None:
        text = source_path.read_text(encoding="utf-8")
        prompt_dir = self.opc_home / "prompts" / "talent"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / f"{template_id}.md"
        prompt_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    def _infer_domains(
        self,
        category: str,
        name: str,
        description: str,
        body: str,
        rel_path: str,
    ) -> list[str]:
        _ = (category, name, description, body, rel_path)
        return []

    def _infer_tags(self, category: str, name: str, rel_path: str) -> list[str]:
        parts = [category.lower(), *re.findall(r"[a-z0-9][a-z0-9+-]{2,}", f"{name} {rel_path}".lower())]
        tags: list[str] = []
        for part in parts:
            if part not in tags:
                tags.append(part)
        return tags[:10]

    def _infer_preferred_external_agent(self, body: str, category: str) -> str | None:
        text = f"{category}\n{body}".lower()
        if "opencode" in text or "open code" in text:
            return "opencode"
        if any(token in text for token in ("code", "engineering", "terminal", "implementation", "repository")):
            return "codex"
        if any(token in text for token in ("infrastructure", "deployment", "operations", "visionos", "cursor")):
            return "cursor"
        return None
