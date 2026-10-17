"""YAML-backed allowlist for persisted tool and command approvals."""

from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml


def _empty_scope() -> dict[str, dict[str, list[str]]]:
    return {
        "tool": {},
        "external_agent": {},
        "work_item_projection_title": {},
    }


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "global": _empty_scope(),
        "projects": {},
        "sessions": {},
    }


class ApprovalAllowlistManager:
    """Persists reusable approval rules in a user-editable YAML file."""

    def __init__(self, opc_home: str | Path) -> None:
        self.opc_home = Path(opc_home)
        self.path = self.opc_home / "config" / "approval_allowlist.yaml"
        self._cache: dict[str, Any] | None = None
        self._cache_mtime_ns: int = -1

    def ensure_file(self) -> None:
        if not self.path.exists():
            self.save(_empty_payload())

    def load(self) -> dict[str, Any]:
        # The permission predictor consults the allowlist on every tool call;
        # cache by mtime so repeated loads do not re-read and re-parse the
        # YAML. External edits to the file are picked up via the mtime change.
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._cache = None
            self._cache_mtime_ns = -1
            return _empty_payload()
        if self._cache is not None and mtime_ns == self._cache_mtime_ns:
            return deepcopy(self._cache)
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            return _empty_payload()
        normalized = self._normalize_payload(raw)
        self._cache = deepcopy(normalized)
        self._cache_mtime_ns = mtime_ns
        return normalized

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self._normalize_payload(payload)
        self.path.write_text(
            yaml.safe_dump(
                normalized,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        try:
            self._cache = deepcopy(normalized)
            self._cache_mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._cache = None
            self._cache_mtime_ns = -1

    def list_patterns(
        self,
        action_kind: str,
        action_name: str,
        project_id: str | None = None,
    ) -> list[str]:
        payload = self.load()
        patterns: list[str] = []
        if project_id:
            patterns.extend(self._scope_patterns(payload["projects"].get(project_id, {}), action_kind, action_name))
        patterns.extend(self._scope_patterns(payload["global"], action_kind, action_name))
        return list(dict.fromkeys(patterns))

    def add_patterns(
        self,
        action_kind: str,
        action_name: str,
        patterns: list[str],
        project_id: str | None = None,
    ) -> list[str]:
        normalized_patterns = [
            self._normalize_pattern(pattern)
            for pattern in patterns
            if self._normalize_pattern(pattern)
        ]
        if not normalized_patterns:
            return []

        payload = self.load()
        scope = payload["global"]
        if project_id:
            scope = payload["projects"].setdefault(project_id, _empty_scope())
            scope = self._normalize_scope(scope)
            payload["projects"][project_id] = scope

        action_bucket = scope.setdefault(action_kind, {})
        existing = [
            self._normalize_pattern(pattern)
            for pattern in action_bucket.get(action_name, [])
            if self._normalize_pattern(pattern)
        ]
        added: list[str] = []
        for pattern in normalized_patterns:
            if pattern in existing:
                continue
            existing.append(pattern)
            added.append(pattern)
        action_bucket[action_name] = existing
        if added:
            self.save(payload)
        return added

    # "Allow for this session" grants used to live only in ApprovalEngine
    # memory, so a `autoswarm ui` restart or re-entering the session re-prompted for
    # commands the user had already approved. They are now persisted here,
    # keyed by the session scope id, capped to the most recent entries.
    _MAX_SESSION_SCOPES = 200

    def session_scope(self, session_id: str) -> dict[str, dict[str, list[str]]]:
        key = str(session_id or "").strip()
        if not key:
            return _empty_scope()
        payload = self.load()
        return self._normalize_scope(payload["sessions"].get(key, {}))

    def add_session_patterns(
        self,
        session_id: str,
        action_kind: str,
        action_name: str,
        patterns: list[str],
    ) -> list[str]:
        key = str(session_id or "").strip()
        normalized_patterns = [
            self._normalize_pattern(pattern)
            for pattern in patterns
            if self._normalize_pattern(pattern)
        ]
        if not key or not normalized_patterns:
            return []

        payload = self.load()
        sessions = payload["sessions"]
        # Re-inserting moves an active session to the newest position so the
        # recency cap below always evicts the longest-idle session first.
        scope = self._normalize_scope(sessions.pop(key, {}))
        sessions[key] = scope

        action_bucket = scope.setdefault(action_kind, {})
        existing = self._normalize_pattern_list(action_bucket.get(action_name, []))
        added: list[str] = []
        for pattern in normalized_patterns:
            if pattern in existing:
                continue
            existing.append(pattern)
            added.append(pattern)
        action_bucket[action_name] = existing

        while len(sessions) > self._MAX_SESSION_SCOPES:
            sessions.pop(next(iter(sessions)))
        if added:
            self.save(payload)
        return added

    def reset(self, project_id: str | None = None) -> None:
        payload = self.load()
        if project_id:
            payload["projects"].pop(project_id, None)
        else:
            payload["global"] = _empty_scope()
        self.save(payload)

    def is_allowed(
        self,
        action_kind: str,
        action_name: str,
        candidates: list[str],
        project_id: str | None = None,
    ) -> tuple[bool, list[str], str | None]:
        normalized_candidates = [
            self._normalize_candidate(candidate)
            for candidate in candidates
            if self._normalize_candidate(candidate)
        ]
        if not normalized_candidates:
            return False, [], None

        payload = self.load()
        scopes: list[tuple[str | None, dict[str, Any]]] = []
        if project_id:
            scopes.append((project_id, payload["projects"].get(project_id, {})))
        scopes.append((None, payload["global"]))

        for scope_id, scope in scopes:
            patterns = self._scope_patterns(scope, action_kind, action_name)
            matched: list[str] = []
            all_matched = True
            for candidate in normalized_candidates:
                candidate_patterns = [
                    pattern
                    for pattern in patterns
                    if self._matches(pattern, candidate)
                ]
                if not candidate_patterns:
                    all_matched = False
                    break
                matched.extend(candidate_patterns)
            if all_matched:
                return True, list(dict.fromkeys(matched)), scope_id
        return False, [], None

    def summarize(self, project_id: str | None = None, limit: int = 20) -> list[str]:
        payload = self.load()
        lines: list[str] = []
        if project_id:
            lines.extend(self._summarize_scope(payload["projects"].get(project_id, {}), scope_label=f"project:{project_id}"))
        lines.extend(self._summarize_scope(payload["global"], scope_label="global"))
        return lines[:limit]

    @staticmethod
    def _normalize_payload(payload: Any) -> dict[str, Any]:
        data = deepcopy(payload) if isinstance(payload, dict) else {}
        normalized = _empty_payload()
        normalized["version"] = int(data.get("version", 1) or 1)
        normalized["global"] = ApprovalAllowlistManager._normalize_scope(data.get("global", {}))

        projects = data.get("projects", {})
        if isinstance(projects, dict):
            for project_id, scope in projects.items():
                key = str(project_id).strip()
                if not key:
                    continue
                normalized["projects"][key] = ApprovalAllowlistManager._normalize_scope(scope)

        sessions = data.get("sessions", {})
        if isinstance(sessions, dict):
            for session_id, scope in sessions.items():
                key = str(session_id).strip()
                if not key:
                    continue
                normalized["sessions"][key] = ApprovalAllowlistManager._normalize_scope(scope)
        return normalized

    @staticmethod
    def _normalize_scope(scope: Any) -> dict[str, dict[str, list[str]]]:
        normalized = _empty_scope()
        if not isinstance(scope, dict):
            return normalized
        for action_kind, entries in scope.items():
            kind = str(action_kind).strip()
            if not kind:
                continue
            bucket: dict[str, list[str]] = {}
            if isinstance(entries, dict):
                for action_name, patterns in entries.items():
                    name = str(action_name).strip()
                    if not name:
                        continue
                    bucket[name] = ApprovalAllowlistManager._normalize_pattern_list(patterns)
            normalized[kind] = bucket
        return normalized

    @staticmethod
    def _normalize_pattern_list(patterns: Any) -> list[str]:
        if isinstance(patterns, str):
            pattern_list = [patterns]
        elif isinstance(patterns, list):
            pattern_list = patterns
        else:
            pattern_list = []
        result: list[str] = []
        for pattern in pattern_list:
            normalized = ApprovalAllowlistManager._normalize_pattern(pattern)
            if normalized:
                result.append(normalized)
        return list(dict.fromkeys(result))

    @staticmethod
    def _normalize_pattern(pattern: Any) -> str:
        return " ".join(str(pattern).strip().split())

    @staticmethod
    def _normalize_candidate(candidate: Any) -> str:
        return " ".join(str(candidate).strip().split()).casefold()

    @staticmethod
    def _scope_patterns(scope: Any, action_kind: str, action_name: str) -> list[str]:
        if not isinstance(scope, dict):
            return []
        entries = scope.get(action_kind, {})
        if not isinstance(entries, dict):
            return []
        return ApprovalAllowlistManager._normalize_pattern_list(entries.get(action_name, []))

    @staticmethod
    def _matches(pattern: str, candidate: str) -> bool:
        normalized_pattern = ApprovalAllowlistManager._normalize_candidate(pattern)
        if not normalized_pattern or normalized_pattern == "*":
            return True
        if any(token in normalized_pattern for token in "*?[]"):
            return fnmatchcase(candidate, normalized_pattern)
        return candidate == normalized_pattern or candidate.startswith(normalized_pattern + " ")

    @staticmethod
    def _summarize_scope(scope: Any, *, scope_label: str) -> list[str]:
        if not isinstance(scope, dict):
            return []
        lines: list[str] = []
        for action_kind in sorted(scope.keys()):
            entries = scope.get(action_kind, {})
            if not isinstance(entries, dict):
                continue
            for action_name in sorted(entries.keys()):
                patterns = ApprovalAllowlistManager._normalize_pattern_list(entries.get(action_name, []))
                if not patterns:
                    continue
                joined = ", ".join(patterns[:4])
                if len(patterns) > 4:
                    joined += ", ..."
                lines.append(f"- [{scope_label}] {action_kind}:{action_name} -> {joined}")
        return lines
