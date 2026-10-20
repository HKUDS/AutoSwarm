---
name: skill-evolution
description: Evolve new skills from project experience. Use when a repeating pattern, useful workflow, or hard-won knowledge should be captured as a reusable skill for the current project.
---

# Skill Evolution

Distill project experience into reusable skills that live under the current project.

## Two evolution paths

1. **Automatic**: The system promotes playbook skills when an employee's pattern reaches 2 project reflections with recurring behaviors, checklists, or preferences. These are saved as `<employee>-<role>-<domain>-playbook` and require no manual action.
2. **Agent-driven** (this skill): You proactively identify valuable patterns and create skills manually. Use this when domain knowledge, workflows, or tool sequences should be preserved before the auto-promotion threshold is reached, or when the knowledge is not tied to a specific employee pattern.

## When to evolve a skill (agent-driven)

- A task required non-obvious steps that will likely recur
- You discovered a workflow that worked well and should be preserved
- Domain-specific knowledge was gathered that future tasks will need
- A sequence of tool calls forms a reliable pattern worth codifying

## Process

1. **Identify the pattern**: What knowledge or workflow is worth preserving?
2. **Read the skill-creator skill**: `file_read` the `skill-creator/SKILL.md` for format and naming guidelines
3. **Name the skill**: Lowercase letters, digits, and hyphens only, under 64 characters (e.g., `api-validation-workflow`)
4. **Create the skill directory**: Under `.opc/projects/<project_id>/skills/<skill-name>/`
5. **Write SKILL.md** with proper frontmatter (`name`, `description`) and concise instructions
6. **Add scripts/references/assets** if the skill benefits from bundled resources

## Skill location

Evolved skills belong to the project that produced them:

```
.opc/projects/<project_id>/skills/<skill-name>/SKILL.md
```

## Guidelines

- Keep it concise: only include what the agent doesn't already know
- Use concrete examples over abstract explanations
- Include the minimal set of steps needed to reproduce the workflow
- Add `scripts/` for deterministic operations that get rewritten repeatedly
- Add `references/` for domain docs the agent should consult
- Follow the same naming conventions as auto-promoted skills (hyphen-case)

## Cross-project reuse

Other projects can reference this project's skills via `file_read` when the secretary recommends them. Do not duplicate skills across projects; read from the source project instead.
