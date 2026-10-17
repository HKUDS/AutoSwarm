"""AutoSwarm Market package format — data models for .opcpkg packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models (persisted in YAML / org_config)
# ---------------------------------------------------------------------------

class PackageAuthor(BaseModel):
    name: str = ""
    github: str = ""


class PackageContents(BaseModel):
    roles: int = 0
    work_item_templates: int = 0
    gates: int = 0
    prompts: int = 0
    skills: int = 0


class OPCPackageManifest(BaseModel):
    """manifest.yaml schema for an .opcpkg package."""

    opc_package: str = "1.0"
    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: PackageAuthor = Field(default_factory=PackageAuthor)
    license: str = "MIT"

    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    industry: list[str] = Field(default_factory=list)
    team_size: str = ""
    use_cases: list[str] = Field(default_factory=list)

    opc_version: str = ">=0.1.0"
    contents: PackageContents = Field(default_factory=PackageContents)


class InstalledPackageInfo(BaseModel):
    """Tracks a package installed into the current org."""

    package_id: str
    name: str = ""
    version: str = "1.0.0"
    installed_at: str = ""
    source_path: str = ""
    role_ids: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    work_item_template_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dataclasses (in-memory only, not persisted)
# ---------------------------------------------------------------------------

@dataclass
class OPCPackage:
    """A fully parsed .opcpkg package ready for install."""

    manifest: OPCPackageManifest
    roles: list[dict[str, Any]] = field(default_factory=list)
    work_item_templates: list[dict[str, Any]] = field(default_factory=list)
    runtime_policy: dict[str, Any] | None = None
    talent_templates: list[dict[str, Any]] = field(default_factory=list)
    employees: list[dict[str, Any]] = field(default_factory=list)
    prompt_contents: dict[str, str] = field(default_factory=dict)
    skill_contents: dict[str, str] = field(default_factory=dict)
    readme: str = ""


@dataclass
class SandboxReport:
    """Result of security validation."""

    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ConflictReport:
    """Result of conflict detection against current org."""

    role_conflicts: list[str] = field(default_factory=list)
    template_conflicts: list[str] = field(default_factory=list)
    work_item_template_conflicts: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.role_conflicts or self.template_conflicts or self.work_item_template_conflicts)
