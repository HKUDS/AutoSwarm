"""Built-in talent templates for AutoSwarm Market.

These ensure the marketplace is never empty, even before a user imports
any external talent repository.  Each template maps to roles in the
architecture blueprints exposed by ``architecture_registry.py``.
"""

from __future__ import annotations

from typing import Any

BUILTIN_TALENT_TEMPLATES: list[dict[str, Any]] = [
    # ── Management ─────────────────────────────────────────────────
    {
        "id": "ceo-strategist",
        "name": "CEO Strategist",
        "description": "Executive leader who sets product vision, makes strategic decisions, and coordinates cross-functional teams.",
        "category": "management",
        "domains": ["strategy", "leadership", "product-vision"],
        "tags": ["executive", "decision-maker", "coordinator"],
        "emoji": "\U0001F454",
        "color": "#2c3e50",
        "vibe": "Think big, decide fast, deliver value",
    },
    {
        "id": "account-manager",
        "name": "Account Manager",
        "description": "Client-facing relationship manager who scopes projects, tracks deliverables, and ensures client satisfaction.",
        "category": "management",
        "domains": ["client-relations", "project-scoping", "delivery"],
        "tags": ["client-work", "communication", "agency"],
        "emoji": "\U0001F91D",
        "color": "#e67e22",
        "vibe": "The client's voice inside the team",
    },
    {
        "id": "product-manager",
        "name": "Product Manager",
        "description": "Defines feature specs, writes user stories, prioritizes backlog, and bridges business and engineering.",
        "category": "product",
        "domains": ["product-strategy", "user-stories", "prioritization"],
        "tags": ["roadmap", "specs", "stakeholder"],
        "emoji": "\U0001F4CA",
        "color": "#3498db",
        "vibe": "Ship the right thing, not just any thing",
    },

    # ── Engineering ────────────────────────────────────────────────
    {
        "id": "cto-architect",
        "name": "CTO / Tech Architect",
        "description": "Technical leader who designs system architecture, makes technology choices, and mentors engineering teams.",
        "category": "engineering",
        "domains": ["architecture", "tech-leadership", "system-design"],
        "tags": ["technical", "architecture", "leadership"],
        "emoji": "\U0001F4BB",
        "color": "#2980b9",
        "vibe": "Build it right, scale it further",
    },
    {
        "id": "fullstack-engineer",
        "name": "Full-Stack Engineer",
        "description": "Versatile developer handling frontend, backend, databases, and deployment. Ships features end-to-end.",
        "category": "engineering",
        "domains": ["frontend", "backend", "database", "deployment"],
        "tags": ["full-stack", "implementation", "coding"],
        "emoji": "\u26A1",
        "color": "#f1c40f",
        "vibe": "Code it, ship it, fix it, repeat",
        "preferred_external_agent": "claude_code",
    },
    {
        "id": "devops-engineer",
        "name": "DevOps / Platform Engineer",
        "description": "Infrastructure specialist managing CI/CD pipelines, cloud architecture, Kubernetes, and deployment automation.",
        "category": "engineering",
        "domains": ["infrastructure", "ci-cd", "kubernetes", "cloud"],
        "tags": ["devops", "automation", "infrastructure"],
        "emoji": "\u2699\uFE0F",
        "color": "#27ae60",
        "vibe": "Automate everything, trust nothing",
        "preferred_external_agent": "claude_code",
    },
    {
        "id": "security-engineer",
        "name": "Security Engineer",
        "description": "Security specialist performing audits, vulnerability scanning, compliance checks, and threat modeling.",
        "category": "engineering",
        "domains": ["security", "compliance", "vulnerability", "audit"],
        "tags": ["security", "compliance", "audit"],
        "emoji": "\U0001F6E1\uFE0F",
        "color": "#c0392b",
        "vibe": "Assume breach, verify everything",
    },
    {
        "id": "sre-lead",
        "name": "SRE Lead",
        "description": "Site reliability engineer managing SLOs, incident response, monitoring, and system resilience.",
        "category": "engineering",
        "domains": ["reliability", "monitoring", "incident-response", "slo"],
        "tags": ["sre", "oncall", "observability"],
        "emoji": "\U0001F5A5\uFE0F",
        "color": "#16a085",
        "vibe": "Keep the lights on, measure everything",
    },

    # ── Design ─────────────────────────────────────────────────────
    {
        "id": "ui-ux-designer",
        "name": "UI/UX Designer",
        "description": "Creates user interfaces, prototypes, and conducts user research. Bridges user needs and visual design.",
        "category": "design",
        "domains": ["ui-design", "ux-research", "prototyping", "figma"],
        "tags": ["design", "user-experience", "visual"],
        "emoji": "\U0001F3A8",
        "color": "#e74c3c",
        "vibe": "Design for humans, not for screens",
    },
    {
        "id": "creative-director",
        "name": "Creative Director",
        "description": "Sets creative vision, ensures brand consistency, and maintains quality standards across all creative output.",
        "category": "design",
        "domains": ["creative-direction", "brand", "quality"],
        "tags": ["creative", "vision", "brand"],
        "emoji": "\u2728",
        "color": "#8e44ad",
        "vibe": "Every pixel tells a story",
    },

    # ── Testing ────────────────────────────────────────────────────
    {
        "id": "qa-engineer",
        "name": "QA Engineer",
        "description": "Tests software quality through manual and automated testing, bug tracking, and regression analysis.",
        "category": "testing",
        "domains": ["testing", "quality-assurance", "automation", "bugs"],
        "tags": ["qa", "testing", "bugs"],
        "emoji": "\U0001F41B",
        "color": "#d35400",
        "vibe": "Break it before users do",
        "preferred_external_agent": "claude_code",
    },

    # ── Writing ────────────────────────────────────────────────────
    {
        "id": "copywriter",
        "name": "Copywriter",
        "description": "Crafts compelling copy, content strategy, messaging, and maintains consistent tone of voice.",
        "category": "writing",
        "domains": ["copywriting", "content-strategy", "messaging"],
        "tags": ["writing", "content", "creative"],
        "emoji": "\u270D\uFE0F",
        "color": "#1abc9c",
        "vibe": "Words that move people to action",
    },
    {
        "id": "editor-in-chief",
        "name": "Editor-in-Chief",
        "description": "Manages editorial strategy, content calendar, quality standards, and publishing cadence.",
        "category": "writing",
        "domains": ["editorial", "content-calendar", "publishing"],
        "tags": ["editorial", "publishing", "quality"],
        "emoji": "\U0001F4F0",
        "color": "#34495e",
        "vibe": "Every word earns its place",
    },

    # ── Research ───────────────────────────────────────────────────
    {
        "id": "research-scientist",
        "name": "Research Scientist",
        "description": "Designs experiments, analyzes data, writes papers, and pushes the boundaries of knowledge.",
        "category": "research",
        "domains": ["experiment-design", "data-analysis", "paper-writing"],
        "tags": ["research", "academic", "analysis"],
        "emoji": "\U0001F52C",
        "color": "#9b59b6",
        "vibe": "Question everything, prove it twice",
    },
    {
        "id": "data-engineer",
        "name": "Data Engineer",
        "description": "Builds data pipelines, manages infrastructure, and ensures data quality and reproducibility.",
        "category": "data",
        "domains": ["data-pipelines", "etl", "databases", "reproducibility"],
        "tags": ["data", "pipelines", "infrastructure"],
        "emoji": "\U0001F5C4\uFE0F",
        "color": "#2c3e50",
        "vibe": "Good data in, good decisions out",
        "preferred_external_agent": "claude_code",
    },
    {
        "id": "principal-investigator",
        "name": "Principal Investigator",
        "description": "Leads research direction, formulates hypotheses, oversees publications, and mentors researchers.",
        "category": "research",
        "domains": ["research-direction", "hypothesis", "publication"],
        "tags": ["pi", "academic", "leadership"],
        "emoji": "\U0001F393",
        "color": "#7f8c8d",
        "vibe": "See what others miss, ask what others won't",
    },

    # ── Marketing ──────────────────────────────────────────────────
    {
        "id": "seo-specialist",
        "name": "SEO Specialist",
        "description": "Performs keyword research, optimizes content for search engines, and tracks analytics metrics.",
        "category": "marketing",
        "domains": ["seo", "keyword-research", "analytics"],
        "tags": ["seo", "marketing", "analytics"],
        "emoji": "\U0001F50D",
        "color": "#e67e22",
        "vibe": "Be found before being searched",
    },
    {
        "id": "social-media-manager",
        "name": "Social Media Manager",
        "description": "Manages social distribution, community engagement, cross-promotion, and audience growth.",
        "category": "marketing",
        "domains": ["social-media", "engagement", "distribution"],
        "tags": ["social", "marketing", "community"],
        "emoji": "\U0001F4E2",
        "color": "#3498db",
        "vibe": "Turn followers into advocates",
    },
]


def get_all_talent_presets() -> list[dict[str, Any]]:
    """Return all built-in talent templates."""
    return BUILTIN_TALENT_TEMPLATES


def get_talent_preset(template_id: str) -> dict[str, Any] | None:
    """Return a single built-in talent template by ID."""
    for t in BUILTIN_TALENT_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def get_talent_categories() -> list[str]:
    """Return unique categories across all built-in talent templates."""
    return sorted({t["category"] for t in BUILTIN_TALENT_TEMPLATES})
