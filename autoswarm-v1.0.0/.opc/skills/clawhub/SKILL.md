---
name: clawhub
description: Search and install agent skills from ClawHub, the public skill registry. Use when needing new capabilities, searching for skills, installing skills, or updating installed skills.
always: true
homepage: https://clawhub.ai
---

# ClawHub

Public skill registry for AI agents. Search by natural language (vector search).

## When to use

- You encounter an unfamiliar domain or technology and need guidance
- The user asks to find, search, install, or update skills
- You need specialized knowledge that existing skills don't cover
- A task requires capabilities beyond your current skill set

## Search

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## Install

Install skills into the current project's skill directory:

```bash
npx --yes clawhub@latest install <slug> --workdir .opc/projects/<project_id>
```

Replace `<slug>` with the skill name from search results and `<project_id>` with the current project ID. This places the skill into `.opc/projects/<project_id>/skills/<slug>/SKILL.md`.

After install, read the installed SKILL.md with `file_read` to apply its guidance immediately.

## Update

```bash
npx --yes clawhub@latest update --all --workdir .opc/projects/<project_id>
```

## List installed

```bash
npx --yes clawhub@latest list --workdir .opc/projects/<project_id>
```

## Workflow

1. Search for relevant skills with a descriptive query
2. Review search results and pick the best match
3. Install the skill into the project directory
4. Read the installed SKILL.md with `file_read`
5. Follow the skill's instructions for the current task

## Notes

- Requires Node.js (`npx` comes with it).
- No API key needed for search and install.
- Login (`npx --yes clawhub@latest login`) is only required for publishing.
- `--workdir` must point to the project directory so skills install correctly.
