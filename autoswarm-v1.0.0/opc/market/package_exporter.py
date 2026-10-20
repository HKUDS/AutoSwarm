"""Export the current org configuration as an .opcpkg package."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .package_format import OPCPackage, OPCPackageManifest, PackageAuthor, PackageContents

if TYPE_CHECKING:
    from opc.core.config import OPCConfig

logger = logging.getLogger(__name__)


class PackageExporter:
    """Exports the current organisation as a self-contained .opcpkg directory."""

    def __init__(self, config: OPCConfig, opc_home: Path) -> None:
        self.config = config
        self.opc_home = opc_home

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_current(
        self,
        package_id: str,
        name: str,
        description: str = "",
        version: str = "1.0.0",
        author_name: str = "",
        author_github: str = "",
    ) -> OPCPackage:
        """Build an OPCPackage from the live org config."""
        org = self.config.org

        roles = [r.model_dump() for r in org.roles]
        employees = [e.model_dump() for e in org.employees]
        templates_by_id = {
            str(getattr(template, "id", "") or ""): template
            for template in list(getattr(org, "talent_templates", []) or [])
            if str(getattr(template, "id", "") or "")
        }
        employee_template_ids = {
            str(employee.get("template_id", "") or "").strip()
            for employee in employees
            if str(employee.get("template_id", "") or "").strip()
        }
        try:
            from opc.layer2_organization.talent_market import TalentMarket

            catalog = {template.id: template for template in TalentMarket(self.opc_home, self.config).list_available_templates()}
            for template_id in employee_template_ids:
                if template_id in catalog:
                    templates_by_id.setdefault(template_id, catalog[template_id])
        except Exception:
            pass
        templates = [template.model_dump() for template in templates_by_id.values()]

        # Serialize runtime policy for the current profile
        wf_policy = None
        profile = org.company_profile
        if profile in org.runtime_policies:
            wf_policy = org.runtime_policies[profile].model_dump()

        # Collect referenced prompt files
        prompt_contents = self._collect_prompts(roles, templates, employees)

        manifest = OPCPackageManifest(
            id=package_id,
            name=name,
            description=description,
            version=version,
            author=PackageAuthor(name=author_name, github=author_github),
            contents=PackageContents(
                roles=len(roles),
                work_item_templates=0,
                gates=0,
                prompts=len(prompt_contents),
            ),
        )

        return OPCPackage(
            manifest=manifest,
            roles=roles,
            runtime_policy=wf_policy,
            talent_templates=templates,
            employees=employees,
            prompt_contents=prompt_contents,
            readme=self._generate_readme(manifest, roles, None),
        )

    def write_to_path(self, package: OPCPackage, out_dir: Path) -> Path:
        """Write an OPCPackage to disk as a .opcpkg directory."""
        pkg_dir = out_dir / f"{package.manifest.id}.opcpkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # manifest.yaml
        with open(pkg_dir / "manifest.yaml", "w", encoding="utf-8") as f:
            yaml.dump(package.manifest.model_dump(), f, default_flow_style=False, allow_unicode=True)

        # org_config.yaml
        org_data: dict = {
            "roles": package.roles,
            "talent_templates": package.talent_templates,
            "employees": package.employees,
            "work_item_templates": package.work_item_templates,
        }
        if package.runtime_policy:
            org_data["runtime_policy"] = package.runtime_policy
        with open(pkg_dir / "org_config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(org_data, f, default_flow_style=False, allow_unicode=True)

        # prompts/
        if package.prompt_contents:
            prompts_dir = pkg_dir / "prompts"
            prompts_dir.mkdir(exist_ok=True)
            for filename, content in package.prompt_contents.items():
                with open(prompts_dir / filename, "w", encoding="utf-8") as f:
                    f.write(content)

        # README.md
        with open(pkg_dir / "README.md", "w", encoding="utf-8") as f:
            f.write(package.readme)

        logger.info("Exported package %s to %s", package.manifest.id, pkg_dir)
        return pkg_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_prompts(
        self,
        roles: list[dict],
        templates: list[dict],
        employees: list[dict],
    ) -> dict[str, str]:
        """Read all referenced prompt files and return {filename: content}."""
        refs: set[str] = set()
        for r in roles:
            refs.update(r.get("prompt_refs") or [])
        for t in templates:
            ref = t.get("prompt_ref", "")
            if ref:
                refs.add(ref)
        for e in employees:
            refs.update(e.get("prompt_refs") or [])

        contents: dict[str, str] = {}
        # ``refs`` come from package/role definitions as bare strings. An absolute or
        # traversing value (e.g. "/etc/passwd" or "../../.aws/credentials") must not be
        # bundled into the exported package, so confine each path to opc_home.
        base = self.opc_home.resolve()
        for ref in sorted(refs):
            path = (self.opc_home / ref).resolve()
            try:
                path.relative_to(base)
            except ValueError:
                logger.debug("Skipping prompt ref outside opc_home: %s", ref)
                continue
            if path.is_file():
                try:
                    contents[path.name] = path.read_text(encoding="utf-8")
                except Exception:
                    logger.debug("Failed to read prompt %s", path)
        return contents

    def _generate_readme(
        self,
        manifest: OPCPackageManifest,
        roles: list[dict],
        work_item_templates: list[dict] | None,
    ) -> str:
        """Auto-generate a README.md for the package."""
        lines = [
            f"# {manifest.name}",
            "",
            manifest.description or "An AutoSwarm architecture package.",
            "",
            f"- **Version**: {manifest.version}",
            f"- **Category**: {manifest.category}",
            f"- **Roles**: {manifest.contents.roles}",
            f"- **Work Item Templates**: {manifest.contents.work_item_templates}",
            "",
            "## Roles",
            "",
        ]
        for r in roles:
            lines.append(f"- **{r.get('name', r.get('id', '?'))}** (`{r.get('id', '?')}`): {r.get('responsibility', '')}")
        if work_item_templates:
            lines.extend(["", "## Work Item Templates", ""])
            for item in work_item_templates:
                lines.append(f"- **{item.get('title', item.get('id', '?'))}** (`{item.get('id', '?')}`)")
        lines.extend([
            "",
            "---",
            f"*Exported at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        ])
        return "\n".join(lines) + "\n"
