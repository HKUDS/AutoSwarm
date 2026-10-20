"""Compatibility secretary policy manager.

Secretary-authored durable rules are disabled. The class remains for older
call sites, skill import plumbing, and tests that construct it directly.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shlex
from typing import Any

import yaml

from opc.layer5_memory.markdown_memory import MarkdownMemoryStore


_DEFAULT_POLICY = {
    "version": 1,
    "memory_notes": [],
    "authorization_rules": [],
    "workspace_guardrails": [],
    "skill_injection_rules": [],
}

_DEFAULT_RISKY_TOOLS = ["file_write", "file_edit", "shell_exec", "git_commit"]


class SecretaryPolicyManager:
    """No-op compatibility facade for legacy secretary policy storage."""

    def __init__(self, opc_home: Path) -> None:
        self.opc_home = opc_home
        self.memory_store = MarkdownMemoryStore(opc_home)

    def load_global(self) -> dict[str, Any]:
        return self._empty_policy()

    def load_project(self, project_id: str) -> dict[str, Any]:
        _ = project_id
        return self._empty_policy()

    def load_merged(self, project_id: str | None = None) -> dict[str, Any]:
        merged = self._empty_policy()
        global_policy = self.load_global()
        merged["memory_notes"].extend(global_policy["memory_notes"])
        merged["authorization_rules"].extend(global_policy["authorization_rules"])
        merged["workspace_guardrails"].extend(global_policy["workspace_guardrails"])
        merged["skill_injection_rules"].extend(global_policy["skill_injection_rules"])
        if project_id:
            project_policy = self.load_project(project_id)
            merged["memory_notes"].extend(project_policy["memory_notes"])
            merged["authorization_rules"].extend(project_policy["authorization_rules"])
            merged["workspace_guardrails"].extend(project_policy["workspace_guardrails"])
            merged["skill_injection_rules"].extend(project_policy["skill_injection_rules"])
        return merged

    def add_rule(self, kind: str, rule: dict[str, Any], project_id: str | None = None) -> dict[str, Any]:
        _ = (kind, rule, project_id)
        return {}

    def add_memory_note(self, note: str, project_id: str | None = None, source: str = "user") -> dict[str, Any]:
        _ = (note, project_id, source)
        return {}

    def get_injected_skills(self, project_id: str | None, domains: list[str] | None = None) -> list[str]:
        _ = (project_id, domains)
        return []

    def render_context(self, project_id: str | None = None, domains: list[str] | None = None) -> str:
        _ = (project_id, domains)
        return ""

    def render_cross_project_skills(self, current_project_id: str | None = None) -> str:
        """Build a summary of skills available across all projects for cross-project recommendations."""
        projects_dir = self.opc_home / "projects"
        if not projects_dir.exists():
            return ""
        lines: list[str] = []
        for child in sorted(projects_dir.iterdir()):
            if not child.is_dir():
                continue
            pid = child.name
            if pid == current_project_id:
                continue
            skills_dir = child / "skills"
            if not skills_dir.exists():
                continue
            skill_items: list[str] = []
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                try:
                    import re
                    text = skill_md.read_text(encoding="utf-8")
                    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
                    if fm_match:
                        fm = yaml.safe_load(fm_match.group(1)) or {}
                        name = fm.get("name", skill_dir.name)
                        desc = fm.get("description", "")
                        skill_items.append(f"  - **{name}**: {desc} [{skill_md}]")
                except Exception:
                    continue
            if skill_items:
                lines.append(f"### Project: {pid}")
                lines.extend(skill_items)
        if not lines:
            return ""
        return "## Skills from Other Projects\nThese skills can be referenced via `file_read`.\n\n" + "\n".join(lines)

    def summarize_policies(self, project_id: str | None = None) -> str:
        _ = project_id
        return "Secretary durable policies are disabled."

    def evaluate_tool_policy(
        self,
        project_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        safe_command_prefixes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        _ = (project_id, tool_name, arguments, safe_command_prefixes)
        return None

    def _evaluate_workspace_guardrails(
        self,
        rules: list[dict[str, Any]],
        tool_name: str,
        arguments: dict[str, Any],
        safe_command_prefixes: list[str],
    ) -> dict[str, Any] | None:
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            risky_tool_names = [str(item).strip() for item in rule.get("risky_tool_names", []) if str(item).strip()]
            if risky_tool_names and tool_name not in risky_tool_names:
                continue
            if tool_name == "shell_exec" and not self._command_is_risky(str(arguments.get("command", "")), safe_command_prefixes):
                continue
            allowed_roots = [self._normalize_path(value) for value in rule.get("allowed_roots", []) if str(value).strip()]
            if not allowed_roots:
                continue
            target_paths = self._extract_target_paths(tool_name, arguments)
            if not target_paths and tool_name == "shell_exec" and rule.get("require_working_directory_for_risky_shell", True):
                return {
                    "effect": rule.get("outside_allowed_action", "escalate"),
                    "reason": "Secretary guardrail requires an explicit working directory for risky shell commands.",
                    "rule_id": rule.get("id", ""),
                    "policy_type": "workspace_guardrail",
                }
            if target_paths and all(not self._path_within(path, allowed_roots) for path in target_paths):
                return {
                    "effect": rule.get("outside_allowed_action", "escalate"),
                    "reason": (
                        "Secretary guardrail blocks risky actions outside approved roots: "
                        + ", ".join(allowed_roots[:3])
                    ),
                    "rule_id": rule.get("id", ""),
                    "policy_type": "workspace_guardrail",
                }
        return None

    def _evaluate_authorization_rules(
        self,
        rules: list[dict[str, Any]],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            if str(rule.get("tool_name", "")).strip() != tool_name:
                continue
            path_prefixes = [self._normalize_path(value) for value in rule.get("path_prefixes", []) if str(value).strip()]
            target_paths = self._extract_target_paths(tool_name, arguments)
            if path_prefixes and target_paths:
                if not all(self._path_within(path, path_prefixes) for path in target_paths):
                    continue
            return {
                "effect": rule.get("action", "auto_allow"),
                "reason": str(rule.get("rationale", "")).strip() or "Matched a secretary authorization rule.",
                "rule_id": rule.get("id", ""),
                "policy_type": "authorization_rule",
            }
        return None

    def _extract_target_paths(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        if tool_name in {"file_read", "file_write", "file_edit"}:
            path = str(arguments.get("path", "")).strip()
            return [self._normalize_path(path)] if path else []
        if tool_name == "shell_exec":
            cwd = str(arguments.get("working_directory", "")).strip()
            return [self._normalize_path(cwd)] if cwd else []
        return []

    def _command_is_risky(self, command: str, safe_command_prefixes: list[str]) -> bool:
        cleaned = command.strip()
        if not cleaned:
            return False
        if self._command_has_redirection(cleaned):
            return True
        segments = self._split_shell_command_segments(cleaned)
        if len(segments) != 1:
            return True
        segment = " ".join(segments[0]).strip().casefold()
        for prefix in safe_command_prefixes:
            candidate = str(prefix or "").strip().casefold()
            if not candidate:
                continue
            if segment == candidate or segment.startswith(f"{candidate} "):
                return False
        return True

    def _split_shell_command_segments(self, command: str) -> list[list[str]]:
        text = str(command or "").replace("\r\n", "\n").replace("\n", " ; ").strip()
        if not text:
            return []
        try:
            lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            try:
                tokens = shlex.split(text)
            except ValueError:
                tokens = text.split()

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in {"&&", "||", ";", "|", "&"}:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    def _command_has_redirection(self, command: str) -> bool:
        text = str(command or "").replace("\r\n", "\n").replace("\n", " ; ").strip()
        if not text:
            return False
        try:
            lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            return any(marker in text for marker in (">", "<"))
        return any(token in {">", ">>", "<", "<<"} for token in tokens)

    def _normalize_policy(self, payload: dict[str, Any], scope: str) -> dict[str, Any]:
        data = self._empty_policy()
        data.update(payload or {})
        data["memory_notes"] = [
            self._normalize_memory_note(item, scope=scope)
            for item in list(data.get("memory_notes", []))
            if isinstance(item, dict) or str(item).strip()
        ]
        data["authorization_rules"] = [
            self._normalize_rule("authorization_rules", item, scope=scope)
            for item in list(data.get("authorization_rules", []))
            if isinstance(item, dict)
        ]
        data["workspace_guardrails"] = [
            self._normalize_rule("workspace_guardrails", item, scope=scope)
            for item in list(data.get("workspace_guardrails", []))
            if isinstance(item, dict)
        ]
        data["skill_injection_rules"] = [
            self._normalize_rule("skill_injection_rules", item, scope=scope)
            for item in list(data.get("skill_injection_rules", []))
            if isinstance(item, dict)
        ]
        return data

    def _normalize_memory_note(self, item: dict[str, Any] | str, scope: str) -> dict[str, Any]:
        if isinstance(item, str):
            return {
                "id": f"note-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                "text": item.strip(),
                "scope": scope,
                "source": "imported",
                "created_at": datetime.now().isoformat(),
            }
        note = dict(item)
        note.setdefault("id", f"note-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        note["text"] = str(note.get("text", "")).strip()
        note.setdefault("scope", scope)
        note.setdefault("source", "user")
        note.setdefault("created_at", datetime.now().isoformat())
        return note

    def _normalize_rule(self, kind: str, item: dict[str, Any], scope: str) -> dict[str, Any]:
        rule = deepcopy(item)
        rule.setdefault("id", f"{kind[:-1]}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        rule.setdefault("scope", scope)
        rule.setdefault("enabled", True)
        rule.setdefault("created_at", datetime.now().isoformat())
        if kind == "authorization_rules":
            rule["tool_name"] = str(rule.get("tool_name", "")).strip()
            rule["action"] = str(rule.get("action", "auto_allow")).strip() or "auto_allow"
            rule["path_prefixes"] = [self._normalize_path(value) for value in rule.get("path_prefixes", []) if str(value).strip()]
            rule["rationale"] = str(rule.get("rationale", "")).strip()
        elif kind == "workspace_guardrails":
            rule["allowed_roots"] = [self._normalize_path(value) for value in rule.get("allowed_roots", []) if str(value).strip()]
            tool_names = [str(item).strip() for item in rule.get("risky_tool_names", []) if str(item).strip()]
            rule["risky_tool_names"] = tool_names or list(_DEFAULT_RISKY_TOOLS)
            rule["outside_allowed_action"] = str(rule.get("outside_allowed_action", "escalate")).strip() or "escalate"
            rule["require_working_directory_for_risky_shell"] = bool(
                rule.get("require_working_directory_for_risky_shell", True)
            )
            rule["rationale"] = str(rule.get("rationale", "")).strip()
        elif kind == "skill_injection_rules":
            rule["domains"] = [str(value).strip() for value in rule.get("domains", []) if str(value).strip()]
            rule["skill_names"] = [str(value).strip() for value in rule.get("skill_names", []) if str(value).strip()]
            rule["rationale"] = str(rule.get("rationale", "")).strip()
        return rule

    def _normalize_path(self, value: str) -> str:
        try:
            return str(Path(str(value).strip()).expanduser().resolve(strict=False))
        except Exception:
            return str(value).strip()

    def _path_within(self, candidate: str, allowed_roots: list[str]) -> bool:
        normalized = self._normalize_path(candidate)
        for root in allowed_roots:
            if normalized == root:
                return True
            if normalized.startswith(root.rstrip("/") + "/"):
                return True
        return False

    def _empty_policy(self) -> dict[str, Any]:
        return deepcopy(_DEFAULT_POLICY)
