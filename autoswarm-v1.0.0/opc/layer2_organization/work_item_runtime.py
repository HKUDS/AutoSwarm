"""Helpers for identifying company work-item runtime metadata."""

from __future__ import annotations

from typing import Any, Mapping


WORK_ITEM_RUNTIME_KEY = "work_item_runtime"
WORK_ITEM_RUNTIME_VERSION_KEY = "work_item_runtime_version"


def is_work_item_runtime_metadata(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether metadata belongs to the company work-item runtime."""
    if not metadata:
        return False
    return bool(metadata.get(WORK_ITEM_RUNTIME_KEY, False))


def work_item_runtime_version(
    metadata: Mapping[str, Any] | None,
    *,
    default: int = 1,
) -> int:
    """Read the canonical work-item runtime version."""
    if not metadata:
        return int(default)
    raw = metadata.get(WORK_ITEM_RUNTIME_VERSION_KEY, default)
    try:
        version = int(raw)
    except (TypeError, ValueError):
        version = int(default)
    return version if version > 0 else int(default)


def mark_work_item_runtime(
    metadata: Mapping[str, Any] | None = None,
    *,
    version: int = 1,
) -> dict[str, Any]:
    """Return metadata marked for the company work-item runtime."""
    result = dict(metadata or {})
    result[WORK_ITEM_RUNTIME_KEY] = True
    result[WORK_ITEM_RUNTIME_VERSION_KEY] = work_item_runtime_version(result, default=version)
    return result


def migrate_work_item_runtime_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    default_version: int = 1,
) -> tuple[dict[str, Any], bool]:
    """Normalize canonical work-item runtime metadata."""
    before = dict(metadata or {})
    result = dict(before)
    if result.get(WORK_ITEM_RUNTIME_KEY, False):
        result[WORK_ITEM_RUNTIME_VERSION_KEY] = work_item_runtime_version(
            result,
            default=default_version,
        )
    return result, result != before
