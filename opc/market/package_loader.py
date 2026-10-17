"""Load, install, and uninstall AutoSwarm Market packages."""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .package_format import (
    ConflictReport,
    InstalledPackageInfo,
    OPCPackage,
    OPCPackageManifest,
)
from .sandbox_checker import SandboxChecker

if TYPE_CHECKING:
    from opc.core.config import OPCConfig

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)
# A package id doubles as a directory name under prompts/market, so it must be a safe
# path component (no separators, no "..", no traversal). Mirrors sandbox_checker.
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PackageLoader:
    """Loads .opcpkg packages from disk and installs/uninstalls them."""

    def __init__(self, config: OPCConfig, opc_home: Path) -> None:
        self.config = config
        self.opc_home = opc_home

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_from_path(self, pkg_path: Path) -> OPCPackage:
        """Parse a .opcpkg directory into an OPCPackage."""
        pkg_path = pkg_path.expanduser().resolve()
        if not pkg_path.is_dir():
            raise FileNotFoundError(f"Package directory not found: {pkg_path}")

        # manifest.yaml
        manifest_path = pkg_path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing manifest.yaml in {pkg_path}")
        with open(manifest_path, encoding="utf-8") as f:
            manifest_data = yaml.safe_load(f) or {}
        manifest = OPCPackageManifest.model_validate(manifest_data)

        # org_config.yaml
        org_path = pkg_path / "org_config.yaml"
        org_data: dict[str, Any] = {}
        if org_path.exists():
            with open(org_path, encoding="utf-8") as f:
                org_data = yaml.safe_load(f) or {}

        # prompts/
        prompt_contents: dict[str, str] = {}
        prompts_dir = pkg_path / "prompts"
        if prompts_dir.is_dir():
            for p in sorted(prompts_dir.rglob("*.md")):
                try:
                    prompt_contents[p.name] = p.read_text(encoding="utf-8")
                except Exception:
                    logger.debug("Failed to read prompt %s", p)

        # README.md
        readme = ""
        readme_path = pkg_path / "README.md"
        if readme_path.exists():
            readme = readme_path.read_text(encoding="utf-8")

        return OPCPackage(
            manifest=manifest,
            roles=org_data.get("roles", []),
            work_item_templates=org_data.get("work_item_templates", []),
            runtime_policy=org_data.get("runtime_policy"),
            talent_templates=org_data.get("talent_templates", []),
            employees=org_data.get("employees", []),
            prompt_contents=prompt_contents,
            readme=readme,
        )

    # ------------------------------------------------------------------
    # Conflict Detection
    # ------------------------------------------------------------------

    def detect_conflicts(self, package: OPCPackage) -> ConflictReport:
        """Check for ID collisions between package and current org."""
        report = ConflictReport()
        existing_role_ids = {r.id for r in self.config.org.roles}
        existing_template_ids = self._current_talent_template_ids()

        for role in package.roles:
            rid = role.get("id", "")
            if rid and rid in existing_role_ids:
                report.role_conflicts.append(rid)

        for tmpl in package.talent_templates:
            tid = tmpl.get("id", "")
            if tid and tid in existing_template_ids:
                report.template_conflicts.append(tid)

        return report

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def install(
        self,
        package: OPCPackage,
        strategy: str = "namespace",
    ) -> InstalledPackageInfo:
        """Install a package into the current org config.

        Args:
            package: Parsed package to install.
            strategy: Conflict resolution — "namespace" (prefix IDs) or "overwrite".

        Returns:
            InstalledPackageInfo to be appended to config.org.installed_packages.
            Caller must call config.save() to persist.
        """
        pkg_id = package.manifest.id
        prefix = f"{pkg_id}:" if strategy == "namespace" else ""

        # 1. Write prompt files
        self._write_prompts(pkg_id, package.prompt_contents)

        # 2. Prepare roles with optional namespace prefix
        role_ids: list[str] = []
        for role_data in package.roles:
            role_data = dict(role_data)  # shallow copy
            original_id = role_data.get("id", "")
            new_id = f"{prefix}{original_id}" if original_id else original_id
            role_data["id"] = new_id
            # Rewrite internal references
            if prefix:
                if role_data.get("reports_to") and role_data["reports_to"] != "owner":
                    role_data["reports_to"] = f"{prefix}{role_data['reports_to']}"
                if role_data.get("can_spawn"):
                    role_data["can_spawn"] = [f"{prefix}{s}" for s in role_data["can_spawn"]]
            # Rewrite prompt_refs to market directory
            if role_data.get("prompt_refs"):
                role_data["prompt_refs"] = [
                    f"prompts/market/{pkg_id}/{Path(ref).name}"
                    for ref in role_data["prompt_refs"]
                ]
            from opc.core.config import RoleConfig
            self.config.org.roles.append(RoleConfig.model_validate(role_data))
            role_ids.append(new_id)

        # 3. Prepare talent templates
        template_ids: list[str] = []
        for tmpl_data in package.talent_templates:
            tmpl_data = dict(tmpl_data)
            original_id = tmpl_data.get("id", "")
            new_id = f"{prefix}{original_id}" if original_id else original_id
            tmpl_data["id"] = new_id
            if tmpl_data.get("prompt_ref"):
                tmpl_data["prompt_ref"] = (
                    f"prompts/market/{pkg_id}/{Path(tmpl_data['prompt_ref']).name}"
                )
            self._write_talent_template_prompt(pkg_id, tmpl_data, package.prompt_contents)
            template_ids.append(new_id)

        # 4. Prepare employees
        for emp_data in package.employees:
            emp_data = dict(emp_data)
            if prefix:
                if emp_data.get("role_id"):
                    emp_data["role_id"] = f"{prefix}{emp_data['role_id']}"
                if emp_data.get("template_id"):
                    emp_data["template_id"] = f"{prefix}{emp_data['template_id']}"
                if emp_data.get("employee_id"):
                    emp_data["employee_id"] = f"{prefix}{emp_data['employee_id']}"
            if emp_data.get("prompt_refs"):
                emp_data["prompt_refs"] = [
                    f"prompts/market/{pkg_id}/{Path(ref).name}"
                    for ref in emp_data["prompt_refs"]
                ]
            from opc.core.config import EmployeeConfig
            self.config.org.employees.append(
                EmployeeConfig.model_validate(emp_data)
            )

        # 5. Apply runtime policy if present in package
        if package.runtime_policy:
            from opc.core.config import RuntimePolicyConfig
            try:
                self.config.org.runtime_policies["custom"] = (
                    RuntimePolicyConfig.model_validate(package.runtime_policy)
                )
            except Exception:
                logger.debug("Failed to apply runtime_policy from package %s", pkg_id)

        # 6. Build installation record
        info = InstalledPackageInfo(
            package_id=pkg_id,
            name=package.manifest.name,
            version=package.manifest.version,
            installed_at=datetime.now(timezone.utc).isoformat(),
            source_path=str(package.manifest.id),
            role_ids=role_ids,
            template_ids=template_ids,
        )
        self.config.org.installed_packages.append(info)

        logger.info(
            "Installed package %s: %d roles, %d templates",
            pkg_id, len(role_ids), len(template_ids),
        )
        return info

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(self, package_id: str) -> bool:
        """Remove an installed package from the org config.

        Caller must call config.save() to persist.
        """
        # Validate up front: package_id is used to locate a directory that is later
        # removed with shutil.rmtree, so a traversal value must be rejected before any
        # lookup — even for ids that are not currently installed.
        self._market_prompts_dir(package_id)
        # Find the installed package record
        installed = None
        for pkg in self.config.org.installed_packages:
            pid = pkg.package_id if isinstance(pkg, InstalledPackageInfo) else pkg.get("package_id", "")
            if pid == package_id:
                installed = pkg
                break
        if installed is None:
            logger.warning("Package %s not found in installed_packages", package_id)
            return False

        if isinstance(installed, InstalledPackageInfo):
            role_ids = set(installed.role_ids)
            template_ids = set(installed.template_ids)
        else:
            role_ids = set(installed.get("role_ids", []))
            template_ids = set(installed.get("template_ids", []))

        # Remove roles
        self.config.org.roles = [
            r for r in self.config.org.roles if r.id not in role_ids
        ]
        # Remove materialized package talent templates.
        for template_id in template_ids:
            try:
                self._talent_prompt_path(template_id).unlink(missing_ok=True)
            except OSError:
                pass
        # Remove employees belonging to removed roles
        self.config.org.employees = [
            e for e in self.config.org.employees
            if e.role_id not in role_ids
        ]
        # Remove installed package record
        self.config.org.installed_packages = [
            p for p in self.config.org.installed_packages
            if (p.package_id if isinstance(p, InstalledPackageInfo) else p.get("package_id", "")) != package_id
        ]

        # Remove prompt files
        prompts_dir = self._market_prompts_dir(package_id)
        if prompts_dir.exists():
            shutil.rmtree(prompts_dir, ignore_errors=True)

        logger.info("Uninstalled package %s", package_id)
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_prompts(self, package_id: str, prompt_contents: dict[str, str]) -> None:
        """Write prompt files to {opc_home}/prompts/market/{package_id}/."""
        if not prompt_contents:
            return
        target_dir = self._market_prompts_dir(package_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        # ``prompt_contents`` keys are filenames supplied by the package; confine each
        # written file to target_dir so a crafted name (e.g. "../escape.md") cannot
        # escape via Path traversal.
        base = target_dir.resolve()
        for filename, content in prompt_contents.items():
            dest = (target_dir / filename)
            if base not in dest.resolve().parents and dest.resolve() != base:
                raise ValueError(f"Prompt filename escapes package directory: {filename!r}")
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)

    def _market_prompts_dir(self, package_id: str) -> Path:
        """Resolve ``{opc_home}/prompts/market/{package_id}`` with validation.

        ``package_id`` originates from an untrusted package manifest and is used both to
        write files (``_write_prompts``) and to recursively delete a directory
        (``uninstall``). Without validation a value such as ``../../projects/<victim>``
        traverses out of the market tree and yields arbitrary file write or arbitrary
        directory deletion. Reject ids that are not lowercase alphanumeric (hyphens/
        underscores allowed) and confirm the resolved path stays inside the market base.
        """
        normalized = str(package_id or "").strip()
        if not _PACKAGE_ID_RE.match(normalized):
            raise ValueError(f"Invalid package id: {package_id!r}")
        market_base = (self.opc_home / "prompts" / "market").resolve()
        target = (market_base / normalized).resolve()
        if target != market_base and market_base not in target.parents:
            raise ValueError(f"Package id escapes market directory: {package_id!r}")
        return self.opc_home / "prompts" / "market" / normalized

    def _current_talent_template_ids(self) -> set[str]:
        try:
            from opc.layer2_organization.talent_market import TalentMarket

            return {template.id for template in TalentMarket(self.opc_home, self.config).list_available_templates()}
        except Exception:
            return set()

    def _write_talent_template_prompt(
        self,
        package_id: str,
        template_data: dict[str, Any],
        prompt_contents: dict[str, str],
    ) -> None:
        template_id = str(template_data.get("id") or "").strip()
        if not template_id:
            return
        prompt_ref = str(template_data.get("prompt_ref") or "").strip()
        source_name = Path(prompt_ref).name if prompt_ref else ""
        content = prompt_contents.get(source_name, "") if source_name else ""
        body = _FRONTMATTER_RE.sub("", content, count=1).strip()
        name = str(template_data.get("name") or template_id).strip() or template_id
        description = str(template_data.get("description") or "").strip()
        if not body:
            body = f"# {name}\n"
            if description:
                body += f"\n{description}\n"
        frontmatter = {
            "id": template_id,
            "name": name,
            "description": description,
            "category": str(template_data.get("category") or "general").strip() or "general",
            "source_package": package_id,
        }
        for key in ("domains", "tags", "preferred_external_agent"):
            value = template_data.get(key)
            if value not in (None, "", [], {}):
                frontmatter[key] = value
        path = self._talent_prompt_path(template_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + body.rstrip()
            + "\n",
            encoding="utf-8",
        )

    def _talent_prompt_path(self, template_id: str) -> Path:
        filename = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(template_id or "").strip()).strip("-") or "template"
        return self.opc_home / "prompts" / "talent" / f"{filename}.md"
