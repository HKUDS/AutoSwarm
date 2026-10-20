"""Helpers for company runtime output-root contracts.

The runtime may receive a user-requested absolute output path that is not
writable inside an external-agent sandbox. These helpers keep the writable
canonical output root separate from requested path aliases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


OUTPUT_CONTRACT_KEYS: tuple[str, ...] = (
    "workspace_root",
    "output_root",
    "target_output_dir",
    "requested_output_root",
    "output_root_aliases",
    "output_root_alias_map",
)


PATH_BLOCKER_TOKENS: tuple[str, ...] = (
    "operation not permitted",
    "permission denied",
    "sandbox",
    "not writable",
    "read-only",
    "readonly",
    "missing required path",
    "required root",
    "required path",
)


def normalize_path_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve(strict=False))
    except Exception:
        return raw


def is_path_relative_to(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).expanduser().resolve(strict=False).relative_to(
            Path(root).expanduser().resolve(strict=False)
        )
        return True
    except Exception:
        return False


def output_contract_metadata(source: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(source or {})
    output_root = normalize_path_text(data.get("output_root") or data.get("target_output_dir"))
    target_output_dir = normalize_path_text(data.get("target_output_dir") or output_root)
    workspace_root = normalize_path_text(data.get("workspace_root") or data.get("comms_workspace_root"))
    raw_requested_output_root = str(data.get("requested_output_root") or "").strip()
    requested_output_root = normalize_path_text(raw_requested_output_root)

    aliases: list[str] = []
    for item in list(data.get("output_root_aliases", []) or []):
        raw_alias = str(item or "").strip()
        normalized_alias = normalize_path_text(raw_alias)
        if normalized_alias:
            aliases.append(normalized_alias)
        if raw_alias and raw_alias != normalized_alias:
            aliases.append(raw_alias)
    if requested_output_root and requested_output_root not in aliases and requested_output_root != output_root:
        aliases.append(requested_output_root)
    if raw_requested_output_root and raw_requested_output_root not in aliases and raw_requested_output_root != output_root:
        aliases.append(raw_requested_output_root)

    alias_map: dict[str, str] = {}
    raw_map = data.get("output_root_alias_map", {})
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            raw_alias = str(key or "").strip()
            alias = normalize_path_text(key)
            target = normalize_path_text(value)
            if raw_alias and target:
                alias_map[raw_alias] = target
            if alias and target:
                alias_map[alias] = target
    if output_root:
        for alias in aliases:
            if alias and alias != output_root:
                alias_map.setdefault(alias, output_root)

    contract: dict[str, Any] = {}
    if workspace_root:
        contract["workspace_root"] = workspace_root
    if output_root:
        contract["output_root"] = output_root
        contract["target_output_dir"] = target_output_dir or output_root
    elif target_output_dir:
        contract["target_output_dir"] = target_output_dir
    if requested_output_root:
        contract["requested_output_root"] = requested_output_root
    if aliases:
        contract["output_root_aliases"] = list(dict.fromkeys(aliases))
    if alias_map:
        contract["output_root_alias_map"] = alias_map
    return contract


def output_alias_map(source: dict[str, Any] | None) -> dict[str, str]:
    data = dict(source or {})
    contract = output_contract_metadata(data)
    aliases = dict(contract.get("output_root_alias_map", {}) or {})
    output_root = str(contract.get("output_root") or contract.get("target_output_dir") or "").strip()
    if not output_root:
        return aliases

    raw_requested = str(data.get("requested_output_root") or "").strip()
    if raw_requested and raw_requested != output_root:
        aliases.setdefault(raw_requested, output_root)

    raw_aliases = data.get("output_root_aliases", []) or []
    if isinstance(raw_aliases, (list, tuple, set)):
        for item in raw_aliases:
            raw_alias = str(item or "").strip()
            if raw_alias and raw_alias != output_root:
                aliases.setdefault(raw_alias, output_root)

    raw_map = data.get("output_root_alias_map", {})
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            raw_alias = str(key or "").strip()
            raw_target = str(value or "").strip()
            target = normalize_path_text(raw_target) or output_root
            if raw_alias and target:
                aliases.setdefault(raw_alias, target)
    return aliases


def replace_output_aliases(value: Any, source: dict[str, Any] | None) -> Any:
    aliases = output_alias_map(source)
    if not aliases:
        return value
    if isinstance(value, str):
        updated = value
        for alias, target in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if alias and target and alias != target:
                updated = updated.replace(alias, target)
        return updated
    if isinstance(value, list):
        return [replace_output_aliases(item, source) for item in value]
    if isinstance(value, tuple):
        return tuple(replace_output_aliases(item, source) for item in value)
    if isinstance(value, dict):
        return {
            key: replace_output_aliases(item, source)
            for key, item in value.items()
        }
    return value


def text_has_path_blocker(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in PATH_BLOCKER_TOKENS)


def render_output_contract_context(
    source: dict[str, Any] | None,
    *,
    heading: str = "## Runtime Output Contract",
    include_workspace_root: bool = True,
) -> str:
    contract = output_contract_metadata(source)
    output_root = str(contract.get("output_root", "") or "").strip()
    workspace_root = str(contract.get("workspace_root", "") or "").strip()
    requested = str(contract.get("requested_output_root", "") or "").strip()
    alias_map = dict(contract.get("output_root_alias_map", {}) or {})
    visible_workspace_root = workspace_root if include_workspace_root else ""
    if not any([output_root, visible_workspace_root, requested, alias_map]):
        return ""

    lines: list[str] = [heading]
    if output_root:
        lines.append(f"Canonical writable output root: {output_root}")
    if visible_workspace_root:
        lines.append(f"Workspace root: {visible_workspace_root}")
    if requested and requested != output_root:
        lines.append(f"Requested output alias: {requested}")
    if alias_map:
        lines.append("Path aliases:")
        for alias, target in list(alias_map.items())[:6]:
            lines.append(f"- {alias} -> {target}")
        lines.append("When old instructions mention an alias path, write and verify against the canonical output root.")
    return "\n".join(lines).strip()
