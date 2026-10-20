---
name: skill-creator
description: Create or update skills. Use when designing, structuring, or packaging skills with scripts, references, and assets.
---

# Skill Creator

Guidance for creating effective skills.

## About Skills

Skills are modular packages that extend agent capabilities with specialized knowledge, workflows, and tools. Each skill is a directory containing a `SKILL.md` file and optional bundled resources.

### What Skills Provide

1. Specialized workflows - Multi-step procedures for specific domains
2. Tool integrations - Instructions for working with specific file formats or APIs
3. Domain expertise - Project-specific knowledge, schemas, business logic
4. Bundled resources - Scripts, references, and assets for complex and repetitive tasks

### Skill Locations in AutoSwarm

AutoSwarm uses a two-level skill system:

| Level | Path | Scope |
|-------|------|-------|
| System | `.opc/skills/<skill-name>/` | Shared across all projects |
| Project | `.opc/projects/<project_id>/skills/<skill-name>/` | Specific to one project |

Project skills with the same name override system skills. When creating new skills from project experience, always use the **project** level.

### How Skills Are Created

Skills enter the system through two paths:

1. **Agent-driven** (this skill): You identify a pattern, read this guide, and create the skill manually using `init_skill.py` or `file_write`.
2. **Auto-promoted**: The system automatically distills playbook skills from repeated project reflections (threshold: 2 reflections with recurring patterns). These are saved as `<employee>-<role>-<domain>-playbook` under the project skills directory.

Both paths produce the same `<skill-name>/SKILL.md` format. This guide covers the agent-driven path; auto-promoted skills follow the same naming and format conventions.

## Core Principles

### Concise is Key

The context window is shared. Only add context the agent doesn't already have. Prefer concise examples over verbose explanations.

### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/          - Executable code
    ├── references/       - Documentation loaded as needed
    └── assets/           - Files used in output
```

### Progressive Disclosure

1. **Metadata (name + description)** - Always in context (~100 words)
2. **SKILL.md body** - When agent reads the skill (<5k words)
3. **Bundled resources** - As needed (scripts can be executed without reading into context)

## Naming

All skills — whether agent-created or auto-promoted — must follow these conventions:

- Lowercase letters, digits, and hyphens only (e.g., `backend-api-playbook`)
- Under 64 characters
- Directory name must match the `name` field in frontmatter

## Skill Creation Process

### Step 1: Understand the skill with concrete examples

Clarify use cases: what triggers this skill, what does it produce?

### Step 2: Plan reusable contents

Identify what scripts, references, and assets would help.

### Step 3: Initialize the skill

For project-specific skills, create under `.opc/projects/<project_id>/skills/`:

```bash
python3 {baseDir}/scripts/init_skill.py <skill-name> --path .opc/projects/<project_id>/skills
```

Options: `--resources scripts,references,assets` and `--examples`.

### Step 4: Edit the skill

Write SKILL.md with:
- **Frontmatter**: `name` (hyphen-case, matches directory) and `description` (what it does + when to use it — this is the primary trigger for skill discovery)
- **Body**: Instructions, examples, references to bundled resources

### Step 5: Package (optional)

```bash
python3 {baseDir}/scripts/package_skill.py <path/to/skill-folder>
```

Validates the skill then creates a distributable `.skill` zip file.

### Step 6: Iterate

Test, notice struggles, improve SKILL.md and resources.
