"""Capability manager for local skill discovery (ClawHub replaces remote search)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from opc.core.config import CapabilityConfig, RoleConfig
from opc.layer5_memory.skill_library import SkillLibrary, Skill


@dataclass
class SkillCandidate:
    name: str
    description: str = ""
    source: str = "local"
    content: str = ""
    score: float = 0.0
    domains: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LocalSkillProvider:
    """Searches the existing local skill library by keyword matching."""

    def __init__(self, skill_library: SkillLibrary) -> None:
        self.skill_library = skill_library

    def search(self, query: str, limit: int = 5) -> list[SkillCandidate]:
        query_terms = [term for term in re.split(r"\W+", query.lower()) if term]
        candidates: list[tuple[float, Skill]] = []
        for skill in self.skill_library.list_skills():
            haystack = " ".join(
                [skill.name.lower(), skill.description.lower(), skill.content[:1200].lower()]
            )
            score = 0.0
            for term in query_terms:
                if term in haystack:
                    score += 1.0
            if skill.always:
                score += 0.25
            if score > 0:
                candidates.append((score, skill))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [
            SkillCandidate(
                name=skill.name,
                description=skill.description,
                source="local",
                content=skill.content,
                score=score,
                metadata={"source_path": skill.source_path, "level": skill.level},
            )
            for score, skill in candidates[:limit]
        ]


class CapabilityManager:
    """Unified capability discovery across local skills and tools.

    Remote skill search is handled by the ClawHub skill (always loaded) which
    instructs the agent to use ``npx clawhub`` via ``shell_exec``.
    """

    def __init__(
        self,
        config: CapabilityConfig,
        skill_library: SkillLibrary,
        tool_registry: Any | None = None,
        adapter_registry: Any | None = None,
    ) -> None:
        self.config = config
        self.skill_library = skill_library
        self.tool_registry = tool_registry
        self.adapter_registry = adapter_registry
        self.local_skills = LocalSkillProvider(skill_library)
    async def search_skills(self, query: str, domains: list[str] | None = None, limit: int = 5) -> list[SkillCandidate]:
        return self.local_skills.search(query, limit=limit)

    def list_attachable_tools(self) -> list[dict[str, Any]]:
        if not self.tool_registry:
            return []
        return [
            {"name": tool.name, "description": tool.description, "category": tool.category}
            for tool in self.tool_registry.list_tools()
        ]

    def list_external_agents(self) -> list[dict[str, Any]]:
        if not self.adapter_registry:
            return []
        return self.adapter_registry.describe_available()

    def build_catalog_summary(self) -> str:
        parts: list[str] = []

        local_skills = self.skill_library.list_skills()
        if local_skills:
            skill_lines = [f"- {skill.name}: {skill.description or 'No description'}" for skill in local_skills[:10]]
            parts.append("## Local Skills\n" + "\n".join(skill_lines))

        tools = self.list_attachable_tools()
        if tools:
            tool_lines = [f"- {tool['name']} ({tool['category']}): {tool['description']}" for tool in tools[:12]]
            parts.append("## Available Tools\n" + "\n".join(tool_lines))

        agents = self.list_external_agents()
        if agents:
            agent_lines = []
            for agent in agents[:8]:
                agent_lines.append(
                    f"- {agent.get('agent')}: model={agent.get('model')} run_mode={agent.get('run_mode')} session_mode={agent.get('session_mode')}"
                )
            parts.append("## External Agents\n" + "\n".join(agent_lines))

        return "\n\n".join(parts)

    async def build_recovery_context(self, query: str, domains: list[str] | None = None) -> tuple[str, list[SkillCandidate]]:
        candidates = await self.search_skills(query, domains=domains, limit=self.config.max_remote_skill_results)
        if not candidates:
            return "", []
        lines: list[str] = []
        for candidate in candidates:
            body = candidate.content.strip()
            if not body:
                body = candidate.description
            lines.append(
                f"### Skill Candidate: {candidate.name} [{candidate.source}]\n"
                f"{body.strip()}"
            )
        return "## Capability Recovery\n" + "\n\n".join(lines), candidates
