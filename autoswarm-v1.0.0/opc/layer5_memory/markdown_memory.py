"""Markdown-backed storage for durable global/project memory."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


_STRUCTURED_BEGIN = "<!-- OPC_STRUCTURED_MEMORY:BEGIN -->"
_STRUCTURED_END = "<!-- OPC_STRUCTURED_MEMORY:END -->"

_EMPTY_SECRETARY = {
    "memory_notes": [],
    "authorization_rules": [],
    "workspace_guardrails": [],
    "skill_injection_rules": [],
}


class MarkdownMemoryStore:
    """Persists pure Markdown memory at global and project scopes."""

    def __init__(self, opc_home: Path) -> None:
        self.opc_home = Path(opc_home)
        self.global_memory_dir = self.opc_home / "memory"
        self.project_memory_dir = self.global_memory_dir / "projects"
        self.global_memory_dir.mkdir(parents=True, exist_ok=True)
        self.project_memory_dir.mkdir(parents=True, exist_ok=True)
        self.migrate_legacy_memory()

    def memory_path(self, project_id: str | None = None) -> Path:
        project = str(project_id or "").strip()
        if project:
            return self.project_memory_dir / f"{project}.md"
        return self.global_memory_dir / "global.md"

    def legacy_memory_path(self, project_id: str | None = None) -> Path:
        project = str(project_id or "").strip()
        if project:
            return self.opc_home / "projects" / project / "MEMORY.md"
        return self.global_memory_dir / "MEMORY.md"

    def history_path(self, project_id: str | None = None) -> Path:
        project = str(project_id or "").strip()
        if project:
            return self.opc_home / "projects" / project / "HISTORY.md"
        return self.global_memory_dir / "HISTORY.md"

    def load_raw_text(self, project_id: str | None = None) -> str:
        path = self.memory_path(project_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def load_visible_text(self, project_id: str | None = None) -> str:
        return self._strip_structured_block(self.load_raw_text(project_id)).strip()

    def save_visible_text(self, content: str, project_id: str | None = None) -> None:
        self._write_file(project_id, str(content).strip())

    def append_visible_entry(self, entry: str, project_id: str | None = None) -> None:
        existing = self.load_visible_text(project_id)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        updated = f"{existing}\n\n## [{timestamp}]\n{entry}".strip()
        self.save_visible_text(updated, project_id)

    def ensure_memory_file(self, project_id: str | None = None, heading: str | None = None) -> Path:
        path = self.memory_path(project_id)
        if not path.exists():
            title = heading or (f"# Project Memory ({project_id})" if project_id else "# Global Memory")
            self._write_file(project_id, title)
        return path

    def delete_project(self, project_id: str) -> None:
        path = self.memory_path(project_id)
        if path.exists():
            path.unlink()
        for legacy in (
            self.legacy_memory_path(project_id),
            self.history_path(project_id),
            self.opc_home / "projects" / project_id / "project_profile.yaml",
            self.opc_home / "projects" / project_id / "secretary_profile.yaml",
        ):
            self._safe_unlink(legacy)

    def migrate_legacy_memory(self) -> None:
        self._migrate_one(None)
        projects_dir = self.opc_home / "projects"
        if projects_dir.is_dir():
            for entry in sorted(projects_dir.iterdir()):
                if entry.is_dir():
                    self._migrate_one(entry.name)
        self._safe_unlink(self.history_path(None))
        profiles_dir = self.opc_home / "profiles"
        if profiles_dir.exists():
            shutil.rmtree(profiles_dir, ignore_errors=True)

    # Compatibility surface for old preference/secretary managers. These
    # managers are now disabled by default; returning empty structures keeps
    # existing callers harmless without reintroducing hidden YAML memory.
    def load_scope_data(self, project_id: str | None = None) -> dict[str, Any]:
        _ = project_id
        return {}

    def save_scope_data(self, data: dict[str, Any], project_id: str | None = None) -> None:
        _ = (data, project_id)

    def load_profile(self, project_id: str | None = None) -> dict[str, Any]:
        _ = project_id
        return {}

    def save_profile(self, profile: dict[str, Any], project_id: str | None = None) -> None:
        _ = (profile, project_id)

    def load_secretary(self, project_id: str | None = None) -> dict[str, Any]:
        _ = project_id
        return {key: list(value) for key, value in _EMPTY_SECRETARY.items()}

    def save_secretary(self, secretary: dict[str, Any], project_id: str | None = None) -> None:
        _ = (secretary, project_id)

    def _migrate_one(self, project_id: str | None) -> None:
        canonical = self.memory_path(project_id)
        legacy = self.legacy_memory_path(project_id)
        legacy_visible = self._read_visible_file(legacy)
        if legacy_visible:
            existing = self._strip_structured_block(
                canonical.read_text(encoding="utf-8") if canonical.exists() else ""
            )
            merged = self._merge_markdown(existing, legacy_visible)
            if merged != existing.strip():
                self._write_file(project_id, merged)
        self._safe_unlink(legacy)
        self._safe_unlink(self.history_path(project_id))
        if project_id:
            self._safe_unlink(self.opc_home / "projects" / project_id / "project_profile.yaml")
            self._safe_unlink(self.opc_home / "projects" / project_id / "secretary_profile.yaml")
        if canonical.exists():
            visible = self._strip_structured_block(canonical.read_text(encoding="utf-8")).strip()
            if visible != canonical.read_text(encoding="utf-8").strip():
                self._write_file(project_id, visible)

    def _write_file(self, project_id: str | None, visible: str) -> None:
        path = self.memory_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = self._strip_structured_block(str(visible or "")).strip()
        path.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")

    @staticmethod
    def _read_visible_file(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return MarkdownMemoryStore._strip_structured_block(path.read_text(encoding="utf-8")).strip()
        except Exception as exc:
            logger.warning(f"Failed to read legacy memory file {path}: {exc}")
            return ""

    @staticmethod
    def _strip_structured_block(raw: str) -> str:
        if not str(raw or "").strip():
            return ""
        pattern = re.compile(
            rf"{re.escape(_STRUCTURED_BEGIN)}\s*```yaml\s*.*?\s*```\s*{re.escape(_STRUCTURED_END)}",
            re.DOTALL,
        )
        return pattern.sub("", raw).strip()

    @staticmethod
    def _merge_markdown(existing: str, incoming: str) -> str:
        current = str(existing or "").strip()
        addition = str(incoming or "").strip()
        if not addition:
            return current
        if not current:
            return addition
        if addition in current:
            return current
        return f"{current}\n\n{addition}".strip()

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except Exception as exc:
            logger.debug(f"Failed to delete legacy memory file {path}: {exc}")
