"""Runtime artifact rendering helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .deltas import changed_artifacts
from .types import RuntimeArtifact


RUNTIME_ARTIFACT_HEADER = "## Runtime Artifact:"
RUNTIME_ARTIFACT_DELTA_HEADER = "## Runtime Artifact Delta:"


def artifact_content_hash(content: str, metadata: dict[str, Any] | None = None) -> str:
    raw = json.dumps(
        {
            "content": str(content or ""),
            "metadata": dict(metadata or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_runtime_artifact_record(
    artifact_type: str,
    title: str,
    content: str,
    *,
    scope: str = "runtime",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_hash = artifact_content_hash(content, metadata)
    return RuntimeArtifact(
        artifact_type=artifact_type,
        title=title,
        content=str(content or "").strip(),
        scope=scope,
        metadata={"content_hash": content_hash, **dict(metadata or {})},
    ).to_record(content_hash=content_hash)


def build_runtime_artifact_manifest(artifacts: Iterable[RuntimeArtifact]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    manifest: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for artifact in artifacts:
        content_hash = artifact_content_hash(artifact.content, artifact.metadata)
        record = artifact.to_record(content_hash=content_hash)
        manifest.append(record)
        hashes[artifact.artifact_type] = content_hash
    return manifest, hashes


def render_runtime_artifact_messages(
    artifacts: Iterable[RuntimeArtifact],
    *,
    previous_hashes: dict[str, str] | None = None,
    emit_delta_messages: bool = True,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for artifact, is_delta in changed_artifacts(artifacts, previous_hashes):
        header = RUNTIME_ARTIFACT_DELTA_HEADER if emit_delta_messages and is_delta else RUNTIME_ARTIFACT_HEADER
        messages.append({
            "role": "system",
            "content": f"{header} {artifact.title}\n{artifact.content}".strip(),
        })
    return messages


def is_runtime_artifact_message(message: dict[str, Any]) -> bool:
    if str(message.get("role", "") or "") != "system":
        return False
    content = str(message.get("content", "") or "")
    return content.startswith(RUNTIME_ARTIFACT_HEADER) or content.startswith(RUNTIME_ARTIFACT_DELTA_HEADER)


def strip_runtime_artifact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if not is_runtime_artifact_message(message)]
