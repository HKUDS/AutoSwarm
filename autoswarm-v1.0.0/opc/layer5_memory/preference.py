"""Compatibility preference manager.

Durable user/project preferences now live in the canonical Markdown memory
files and are edited by agents through the memory skill. This manager remains
only for old call sites that still expect a preference object.
"""

from __future__ import annotations

from typing import Any

from opc.layer5_memory.markdown_memory import MarkdownMemoryStore


class PreferenceManager:
    """No-op compatibility facade for legacy structured preference storage."""

    def __init__(self, opc_home) -> None:
        self.opc_home = opc_home
        self.memory_store = MarkdownMemoryStore(opc_home)

    # --- Load ---

    def load_global(self) -> dict[str, Any]:
        return self.memory_store.load_profile()

    def load_project(self, project_id: str) -> dict[str, Any]:
        return self.memory_store.load_profile(project_id)

    def load_project_knowledge(self, project_id: str) -> dict[str, Any]:
        profile = self.load_project(project_id)
        knowledge = profile.get("project_knowledge", {})
        return knowledge if isinstance(knowledge, dict) else {}

    def load_merged(self, project_id: str | None = None) -> dict[str, Any]:
        """Load and merge preferences with project overrides on top of global."""
        merged = self.load_global()
        if project_id:
            merged = self._deep_merge(merged, self.load_project(project_id))
        return merged

    # --- Save ---

    def save_global(self, prefs: dict[str, Any]) -> None:
        self.memory_store.save_profile(prefs)

    def save_project(self, project_id: str, prefs: dict[str, Any]) -> None:
        self.memory_store.save_profile(prefs, project_id)

    # --- Update (partial merge) ---

    def update_global(self, updates: dict[str, Any]) -> None:
        _ = updates

    def update_project(self, project_id: str, updates: dict[str, Any]) -> None:
        _ = (project_id, updates)

    def update_project_knowledge(self, project_id: str, updates: dict[str, Any]) -> None:
        _ = (project_id, updates)

    def record_autonomy_feedback(
        self,
        action_name: str,
        approved: bool,
        project_id: str | None = None,
        explicit: bool = False,
        notes: str = "",
    ) -> None:
        _ = (action_name, approved, project_id, explicit, notes)

    def get_autonomy_preferences(self, project_id: str | None = None) -> dict[str, Any]:
        _ = project_id
        return {}

    def reset_autonomy_preferences(self, project_id: str | None = None) -> None:
        _ = project_id

    # --- Preference context for agent prompts ---

    def build_preference_context(self, project_id: str | None = None) -> str:
        _ = project_id
        return ""

    def summarize_autonomy_preferences(self, project_id: str | None = None) -> str:
        _ = project_id
        return ""

    def render_project_knowledge_context(self, project_id: str | None) -> str:
        _ = project_id
        return ""

    @staticmethod
    def _render_project_knowledge_lines(value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [f"- {text}"] if text else []
        if isinstance(value, list):
            return [f"- {str(item).strip()}" for item in value if str(item).strip()]
        if isinstance(value, dict):
            lines: list[str] = []
            for key, nested in value.items():
                nested_text = str(nested).strip()
                if nested_text:
                    lines.append(f"- {key}: {nested_text}")
            return lines
        return []

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = PreferenceManager._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
