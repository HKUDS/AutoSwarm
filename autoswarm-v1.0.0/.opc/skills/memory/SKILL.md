---
name: memory
description: Manage durable global/project memory in `.opc/memory/global.md` and `.opc/memory/projects/<current_project_id>.md`.
always: true
---

# Memory

Use this skill only for durable Markdown memory:
- Global memory: `.opc/memory/global.md`
- Project memory: `.opc/memory/projects/<current_project_id>.md`
- Prefer the absolute `OPC_GLOBAL_MEMORY_PATH` and `OPC_PROJECT_MEMORY_PATH` shown in runtime context or environment; do not create a separate `.opc/memory` under the workplace.

## What To Save

- Global: stable cross-project user preferences, communication defaults, standing constraints.
- Project: current-project preferences, workspace overrides, repo paths, architecture constraints, coding conventions, delivery requirements.
- Never save secrets, copied transcripts, temporary progress, speculative notes, or one-off task results.

## When to Update

- Update only during interaction with the user, and only when the user states or confirms something that should matter in later sessions.

## How to Update

Read before editing. Merge, deduplicate, replace stale items, and keep entries compact. Do not mix project IDs: write project memory only to the current project's file.
