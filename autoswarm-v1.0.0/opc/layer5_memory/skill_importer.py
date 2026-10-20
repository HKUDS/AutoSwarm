"""Import and normalize skills from multiple sources for immediate project use."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from loguru import logger

from opc.layer5_memory.secretary_policy import SecretaryPolicyManager
from opc.layer5_memory.skill_library import SkillLibrary

ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "metadata",
    "always",
    "license",
    "allowed-tools",
    "homepage",
}
ALLOWED_RESOURCE_DIRS = {"scripts", "references", "assets"}
MAX_SKILL_NAME_LENGTH = 64
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PLACEHOLDER_MARKERS = ("[todo", "todo:")
_STOPWORDS = {
    "clawhub",
    "skill",
    "skills",
    "search",
    "install",
    "update",
    "results",
    "latest",
}

CommandRunner = Callable[[list[str], Path], Awaitable[tuple[int, str, str]]]


class SkillImportError(RuntimeError):
    """Raised when importing or normalizing a skill fails."""


@dataclass
class SkillImportResult:
    skill_name: str
    skill_path: str
    source_slug: str
    validation_message: str
    available: bool = True
    enabled_domains: list[str] = field(default_factory=list)
    search_output: str = ""
    warnings: list[str] = field(default_factory=list)


class ExternalSkillImporter:
    """Imports skills from external or local sources and normalizes them."""

    def __init__(
        self,
        skill_library: SkillLibrary,
        policies: SecretaryPolicyManager | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.skill_library = skill_library
        self.policies = policies
        self.opc_home = skill_library.projects_dir.parent
        self.command_runner = command_runner or self._run_command

    async def import_skill(
        self,
        *,
        project_id: str,
        source: str = "clawhub",
        query: str = "",
        slug: str = "",
        path: str = "",
        domains: list[str] | None = None,
        enable: bool = True,
    ) -> SkillImportResult:
        if not project_id:
            raise SkillImportError("Skill import requires a project context.")

        cleaned_query = str(query).strip()
        cleaned_slug = self.normalize_skill_name(slug)
        cleaned_source = str(source or "clawhub").strip().lower()
        source_path = str(path).strip()
        search_output = ""
        project_root = self.opc_home / "projects" / project_id
        skills_root = project_root / "skills"
        project_root.mkdir(parents=True, exist_ok=True)
        skills_root.mkdir(parents=True, exist_ok=True)

        imported_dir: Path
        if cleaned_source == "clawhub":
            if not cleaned_slug:
                cleaned_slug, search_output = await self._resolve_slug(cleaned_query)
            imported_dir = await self._install_from_clawhub(
                project_root=project_root,
                skills_root=skills_root,
                slug=cleaned_slug,
            )
        elif cleaned_source in {"path", "directory", "local"}:
            imported_dir = self._prepare_local_source(
                skills_root=skills_root,
                source_path=source_path,
                suggested_name=cleaned_slug or Path(source_path or "imported-skill").name,
            )
            cleaned_slug = cleaned_slug or self.normalize_skill_name(Path(source_path).name)
        else:
            raise SkillImportError(
                f"Unsupported skill source '{cleaned_source}'. Supported sources: clawhub, path."
            )

        final_dir, final_name, warnings = self._normalize_imported_dir(
            imported_dir,
            source=cleaned_source,
            source_slug=cleaned_slug,
            query=cleaned_query,
            source_path=source_path,
        )
        valid, validation_message = validate_skill_directory(final_dir)
        if not valid:
            raise SkillImportError(validation_message)

        self.skill_library.load_all(project_id)
        available = self.skill_library.get(final_name) is not None
        if not available:
            raise SkillImportError(f"Imported skill '{final_name}' was normalized but did not load into the skill library.")

        enabled_domains = self._enable_domains(final_name, project_id, domains or [], enable=enable)
        return SkillImportResult(
            skill_name=final_name,
            skill_path=str(final_dir),
            source_slug=cleaned_slug,
            validation_message=validation_message,
            available=True,
            enabled_domains=enabled_domains,
            search_output=search_output,
            warnings=warnings,
        )

    async def _install_from_clawhub(self, *, project_root: Path, skills_root: Path, slug: str) -> Path:
        before = {child.name for child in skills_root.iterdir() if child.is_dir()}
        exit_code, stdout, stderr = await self.command_runner(
            ["npx", "--yes", "clawhub@latest", "install", slug, "--workdir", str(project_root)],
            self.opc_home,
        )
        if exit_code != 0:
            message = (stderr or stdout).strip()
            if "Unsupported engine" in message or "Node.js v" in message:
                raise SkillImportError(
                    "ClawHub install failed. This environment needs Node.js >= 20 to run "
                    "`npx clawhub@latest`."
                )
            raise SkillImportError(message or f"ClawHub install failed for '{slug}'.")
        return self._locate_imported_dir(skills_root, before, slug)

    def _prepare_local_source(self, *, skills_root: Path, source_path: str, suggested_name: str) -> Path:
        if not source_path:
            raise SkillImportError("Path-based skill import requires a `path`.")
        resolved = Path(source_path).expanduser().resolve(strict=False)
        if not resolved.exists() or not resolved.is_dir():
            raise SkillImportError(f"Local skill source does not exist or is not a directory: {resolved}")

        prepared_name = self.normalize_skill_name(suggested_name) or "imported-skill"
        prepared_dir = self._build_prepare_dir(skills_root, prepared_name)
        shutil.copytree(resolved, prepared_dir, dirs_exist_ok=False)
        return prepared_dir

    async def _resolve_slug(self, query: str) -> tuple[str, str]:
        cleaned = str(query).strip().strip("`\"'")
        if not cleaned:
            raise SkillImportError("Need a skill query or exact slug to import.")
        if self._looks_like_slug(cleaned):
            return self.normalize_skill_name(cleaned), ""

        exit_code, stdout, stderr = await self.command_runner(
            ["npx", "--yes", "clawhub@latest", "search", cleaned, "--limit", "5"],
            self.opc_home,
        )
        if exit_code != 0:
            message = (stderr or stdout).strip()
            if "Unsupported engine" in message or "Node.js v" in message:
                raise SkillImportError(
                    "ClawHub search failed. This environment needs Node.js >= 20 to run "
                    "`npx clawhub@latest`."
                )
            raise SkillImportError(message or f"ClawHub search failed for '{cleaned}'.")

        candidates = self._extract_slug_candidates(stdout)
        if not candidates:
            raise SkillImportError(
                "Could not determine a ClawHub slug from search results. Please specify the exact skill slug."
            )
        return candidates[0], stdout.strip()

    def _locate_imported_dir(self, skills_root: Path, before: set[str], slug: str) -> Path:
        preferred = skills_root / self.normalize_skill_name(slug)
        if preferred.exists():
            return preferred

        after_dirs = [child for child in skills_root.iterdir() if child.is_dir()]
        new_dirs = [child for child in after_dirs if child.name not in before]
        if len(new_dirs) == 1:
            return new_dirs[0]

        matches = [child for child in after_dirs if self.normalize_skill_name(slug) in child.name]
        if len(matches) == 1:
            return matches[0]

        if after_dirs:
            after_dirs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return after_dirs[0]
        raise SkillImportError(f"Could not locate the installed skill directory for '{slug}'.")

    def _normalize_imported_dir(
        self,
        skill_dir: Path,
        *,
        source: str,
        source_slug: str,
        query: str,
        source_path: str,
    ) -> tuple[Path, str, list[str]]:
        source_skill_md = skill_dir / "SKILL.md"
        warnings: list[str] = []
        if not source_skill_md.exists():
            matches = sorted(skill_dir.rglob("SKILL.md"))
            if not matches:
                raise SkillImportError(f"Installed skill at {skill_dir} does not contain a SKILL.md file.")
            source_skill_md = matches[0]
            if source_skill_md.parent != skill_dir:
                warnings.append("Imported skill had nested content; normalized from the nested SKILL.md root.")

        frontmatter, body = _load_skill_document(source_skill_md.read_text(encoding="utf-8"))
        proposed_name = (
            str(frontmatter.get("name", "")).strip()
            or source_slug
            or skill_dir.name
            or "imported-skill"
        )
        desired_name = self.normalize_skill_name(proposed_name) or self.normalize_skill_name(source_slug) or "imported-skill"
        final_name = self._dedupe_skill_name(skill_dir.parent, desired_name, current_dir=skill_dir)

        description = self._normalize_description(frontmatter.get("description"), body, final_name)
        normalized_frontmatter: dict[str, Any] = {
            "name": final_name,
            "description": description,
        }
        if isinstance(frontmatter.get("always"), bool):
            normalized_frontmatter["always"] = frontmatter["always"]

        metadata: dict[str, Any] = {}
        if isinstance(frontmatter.get("metadata"), dict):
            metadata.update(frontmatter["metadata"])

        imported_extra_frontmatter: dict[str, Any] = {}
        for key, value in frontmatter.items():
            if key in {"name", "description", "always", "metadata"}:
                continue
            if key in {"license", "allowed-tools", "homepage"} and value not in (None, ""):
                normalized_frontmatter[key] = value
            else:
                imported_extra_frontmatter[key] = value

        metadata.setdefault("imported_from", {
            "source": source,
            "slug": source_slug,
            "query": query,
            "path": source_path,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        })
        if imported_extra_frontmatter:
            metadata["imported_frontmatter"] = imported_extra_frontmatter
        if metadata:
            normalized_frontmatter["metadata"] = metadata

        normalized_body = body.strip()
        if not normalized_body:
            normalized_body = (
                f"# {final_name}\n\n"
                f"Imported and normalized from {source}. Fill in more guidance if this skill needs project-specific detail.\n"
            )
            warnings.append("Imported skill had no body content; inserted a minimal placeholder body.")

        prepare_dir = self._build_prepare_dir(skill_dir.parent, final_name)
        prepare_dir.mkdir(parents=True, exist_ok=False)
        try:
            skill_md = prepare_dir / "SKILL.md"
            skill_md.write_text(
                _render_skill_document(normalized_frontmatter, normalized_body),
                encoding="utf-8",
            )

            source_root = source_skill_md.parent
            for child in source_root.iterdir():
                if child.name == "SKILL.md":
                    continue
                if child.is_dir() and child.name in ALLOWED_RESOURCE_DIRS:
                    shutil.copytree(child, prepare_dir / child.name, dirs_exist_ok=True)
                    continue
                fallback = prepare_dir / "assets" / "imported-root" / child.name
                _copy_path(child, fallback)

            final_dir = skill_dir.parent / final_name
            backup_dir = self._build_backup_dir(skill_dir.parent, skill_dir.name)
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            if skill_dir.exists():
                skill_dir.rename(backup_dir)
            prepare_dir.rename(final_dir)
            shutil.rmtree(backup_dir, ignore_errors=True)
            return final_dir, final_name, warnings
        except Exception:
            shutil.rmtree(prepare_dir, ignore_errors=True)
            raise

    def _enable_domains(self, skill_name: str, project_id: str, domains: list[str], *, enable: bool) -> list[str]:
        if not enable or not self.policies:
            return []

        normalized_domains: list[str] = []
        for item in domains:
            value = str(item).strip()
            if value and value not in normalized_domains:
                normalized_domains.append(value)
        if not normalized_domains:
            return []

        existing_rules = self.policies.load_project(project_id).get("skill_injection_rules", [])
        for rule in existing_rules:
            if not rule.get("enabled", True):
                continue
            current_domains = [str(item).strip() for item in rule.get("domains", []) if str(item).strip()]
            current_skills = [str(item).strip() for item in rule.get("skill_names", []) if str(item).strip()]
            if current_domains == normalized_domains and skill_name in current_skills:
                return normalized_domains

        self.policies.add_rule(
            "skill_injection_rules",
            {
                "domains": normalized_domains,
                "skill_names": [skill_name],
                "rationale": "Imported by the secretary and enabled for immediate project use.",
            },
            project_id=project_id,
        )
        return normalized_domains

    def _extract_slug_candidates(self, output: str) -> list[str]:
        candidates: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            matches = [
                re.search(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`", line),
                re.search(r"^\d+[\).\s-]+([a-z0-9]+(?:-[a-z0-9]+)*)\b", line),
                re.search(r"^\|\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*\|", line),
                re.search(r"^[-*]\s*([a-z0-9]+(?:-[a-z0-9]+)*)\b", line),
            ]
            chosen = next((match.group(1) for match in matches if match), "")
            if not chosen:
                for token in _SLUG_RE.findall(line):
                    if token in _STOPWORDS:
                        continue
                    if "-" not in token and len(token) < 6:
                        continue
                    chosen = token
                    break
            if chosen and chosen not in candidates:
                candidates.append(chosen)
        return candidates

    @staticmethod
    def normalize_skill_name(raw: str) -> str:
        normalized = raw.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
        normalized = normalized.strip("-")
        normalized = re.sub(r"-{2,}", "-", normalized)
        return normalized[:MAX_SKILL_NAME_LENGTH]

    @staticmethod
    def _looks_like_slug(value: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value.strip().lower()))

    def _normalize_description(self, description: Any, body: str, fallback_name: str) -> str:
        if isinstance(description, str) and _validate_description(description.strip()) is None:
            return description.strip()

        heading_match = re.search(r"^\s*#\s+(.+?)\s*$", body, re.MULTILINE)
        first_sentence = ""
        for line in body.splitlines():
            stripped = line.strip().strip("#").strip()
            if not stripped:
                continue
            if stripped.lower().startswith("description:"):
                continue
            first_sentence = stripped
            break
        fallback = heading_match.group(1).strip() if heading_match else first_sentence
        fallback = fallback.strip()
        if not fallback:
            fallback = f"Imported skill `{fallback_name}`."
        if len(fallback) > 1024:
            fallback = fallback[:1021].rstrip() + "..."
        if _validate_description(fallback) is not None:
            return f"Imported skill `{fallback_name}` for project use."
        return fallback

    def _dedupe_skill_name(self, parent: Path, desired_name: str, *, current_dir: Path) -> str:
        candidate = desired_name[:MAX_SKILL_NAME_LENGTH]
        if not candidate:
            candidate = "imported-skill"
        if not (parent / candidate).exists() or current_dir.name == candidate:
            return candidate
        for index in range(2, 100):
            suffix = f"-{index}"
            trimmed = candidate[: MAX_SKILL_NAME_LENGTH - len(suffix)].rstrip("-")
            attempt = f"{trimmed}{suffix}"
            if not (parent / attempt).exists():
                return attempt
        raise SkillImportError(f"Could not find an available normalized name for imported skill '{desired_name}'.")

    @staticmethod
    def _build_prepare_dir(parent: Path, final_name: str) -> Path:
        for index in range(1, 100):
            temp_dir = parent / f".{final_name}.normalize-{index}"
            if not temp_dir.exists():
                return temp_dir
        raise SkillImportError(f"Could not allocate a temporary normalization directory for '{final_name}'.")

    @staticmethod
    def _build_backup_dir(parent: Path, original_name: str) -> Path:
        for index in range(1, 100):
            backup = parent / f".{original_name}.backup-{index}"
            if not backup.exists():
                return backup
        raise SkillImportError(f"Could not allocate a backup directory for '{original_name}'.")

    @staticmethod
    async def _run_command(argv: list[str], cwd: Path) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace")


def validate_skill_directory(skill_path: Path) -> tuple[bool, str]:
    skill_path = Path(skill_path).resolve()
    if not skill_path.exists():
        return False, f"Skill folder not found: {skill_path}"
    if not skill_path.is_dir():
        return False, f"Path is not a directory: {skill_path}"

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    frontmatter, _ = _load_skill_document(skill_md.read_text(encoding="utf-8"))
    unexpected_keys = sorted(set(frontmatter.keys()) - ALLOWED_FRONTMATTER_KEYS)
    if unexpected_keys:
        allowed = ", ".join(sorted(ALLOWED_FRONTMATTER_KEYS))
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(unexpected_keys)}. "
            f"Allowed properties are: {allowed}"
        )

    name = frontmatter.get("name")
    if not isinstance(name, str):
        return False, "Missing or invalid 'name' in frontmatter"
    name_error = _validate_skill_name(name.strip(), skill_path.name)
    if name_error:
        return False, name_error

    description = frontmatter.get("description")
    if not isinstance(description, str):
        return False, "Missing or invalid 'description' in frontmatter"
    description_error = _validate_description(description.strip())
    if description_error:
        return False, description_error

    always = frontmatter.get("always")
    if always is not None and not isinstance(always, bool):
        return False, f"'always' must be a boolean, got {type(always).__name__}"

    metadata = frontmatter.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return False, f"'metadata' must be a dictionary, got {type(metadata).__name__}"

    for child in skill_path.iterdir():
        if child.name == "SKILL.md":
            continue
        if child.is_dir() and child.name in ALLOWED_RESOURCE_DIRS:
            continue
        if child.is_symlink():
            continue
        return (
            False,
            f"Unexpected file or directory in skill root: {child.name}. "
            "Only SKILL.md, scripts/, references/, and assets/ are allowed.",
        )
    return True, "Skill is valid!"


def _load_skill_document(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        parts = text.split("\n")
        for index in range(1, len(parts)):
            if parts[index].strip() == "---":
                frontmatter_text = "\n".join(parts[1:index])
                body = "\n".join(parts[index + 1 :]).lstrip("\n")
                try:
                    frontmatter = yaml.safe_load(frontmatter_text) or {}
                except yaml.YAMLError as exc:
                    logger.warning(f"Failed to parse skill frontmatter: {exc}")
                    frontmatter = {}
                return frontmatter if isinstance(frontmatter, dict) else {}, body
    return {}, text


def _render_skill_document(frontmatter: dict[str, Any], body: str) -> str:
    fm = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.rstrip()}\n"


def _copy_path(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def _validate_skill_name(name: str, folder_name: str) -> str | None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        return (
            f"Name '{name}' should be hyphen-case "
            "(lowercase letters, digits, and single hyphens only)"
        )
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return (
            f"Name is too long ({len(name)} characters). Maximum is {MAX_SKILL_NAME_LENGTH} characters."
        )
    if name != folder_name:
        return f"Skill name '{name}' must match directory name '{folder_name}'"
    return None


def _validate_description(description: str) -> str | None:
    trimmed = description.strip()
    if not trimmed:
        return "Description cannot be empty"
    lowered = trimmed.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return "Description still contains TODO placeholder text"
    if "<" in trimmed or ">" in trimmed:
        return "Description cannot contain angle brackets (< or >)"
    if len(trimmed) > 1024:
        return f"Description is too long ({len(trimmed)} characters). Maximum is 1024 characters."
    return None
