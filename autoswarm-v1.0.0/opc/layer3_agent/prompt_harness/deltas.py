"""Artifact delta helpers."""

from __future__ import annotations

from typing import Iterable

from .types import RuntimeArtifact


def changed_artifacts(
    artifacts: Iterable[RuntimeArtifact],
    previous_hashes: dict[str, str] | None = None,
) -> list[tuple[RuntimeArtifact, bool]]:
    previous = dict(previous_hashes or {})
    changed: list[tuple[RuntimeArtifact, bool]] = []
    for artifact in artifacts:
        content_hash = str(artifact.metadata.get("content_hash", "") or "")
        is_delta = bool(previous.get(artifact.artifact_type)) and previous.get(artifact.artifact_type) != content_hash
        changed.append((artifact, is_delta))
    return changed
