"""Typed prompt harness objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeArtifact:
    artifact_type: str
    title: str
    content: str
    scope: str = "runtime"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self, *, content_hash: str) -> dict[str, Any]:
        return {
            "type": self.artifact_type,
            "title": self.title,
            "content": self.content,
            "scope": self.scope,
            "content_hash": content_hash,
            "metadata": dict(self.metadata),
        }


@dataclass
class PromptHarnessOutput:
    system_prompt: str
    runtime_policy_messages: list[dict[str, Any]] = field(default_factory=list)
    workspace_context_messages: list[dict[str, Any]] = field(default_factory=list)
    dynamic_messages: list[dict[str, Any]] = field(default_factory=list)
    artifact_messages: list[dict[str, Any]] = field(default_factory=list)
    static_section_ids: list[str] = field(default_factory=list)
    dynamic_section_ids: list[str] = field(default_factory=list)
    artifact_manifest: list[dict[str, Any]] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
