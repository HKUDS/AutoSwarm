---
name: autoswarm
description: Coordinate local agents for complex tasks, independent subtasks or independent verification. Select suitable available agents, arrange parallel and dependent work, handle child interactions and collect results here. Guide CLI setup when needed.
---

The main host uses the installed launcher argv below. Children use the exact CLI argv supplied in their AutoSwarm communication context, including its interpreter and --home; an injected Python -m autoswarm entry point is valid. For configuration, upgrades or failures, read [setup and troubleshooting](references/setup.md).

Reuse native configuration, models and authentication. Configure only requested providers; ask before installing a missing CLI. An empty authentication list does not establish that login is needed. Confirm setup with `doctor --agent <provider> --probe --cwd <workspace>`, which spends model calls through AutoSwarm and verifies native resume. A version check alone is discovery.

Honor user choices; otherwise match the task to available agents and their working native tools, configuration and data access. The table suggests candidates, not fixed roles or an exhaustive capability list. Coding/image preferences and the Opus video preference are user experience, not benchmark rankings. Preserve user-selected models and native defaults; a model condition is not permission to change them.

| Agent | Distinctive task fit | Other tools and entry points |
| --- | --- | --- |
| codex | Coding; high-quality image generation | GitHub, workspace connectors, Meta, CloudBase |
| claude-code | Coding; programmatic video with Opus 5.5+ (experience preference) | Office skills, workspace connectors, Runway, Postiz, MCP tool serving |
| opencode | General execution in an existing setup | GitHub Actions, comment/event triggers |
| cursor | Native images; cloud execution and automations | X, workspace/IM plugins, Meta, Buffer, CloudBase |
| copilot | GitHub tools; M365 information tasks | Built-in GitHub MCP, Work IQ, native remote control |
| muse | Native images/editing; audio-file transcription | Blender tools, native memory |
| codebuddy | Model-generated video; Tencent ecosystem | Tencent Docs, CloudBase, messaging, daemon schedules |
| hermes | X; connected channels; multimedia; browser/desktop automation | Workspace/Office tools, gateway events, optional calls/payments |
| nanobot | Messaging/email-driven assistants and automations | Gateway, image tool, workspace/video/deployment presets |
| dsh | Execution in an existing project/profile | CloudBase, QQ/DingTalk connectors, profile-specific tools |
| grok | General execution; specialty tools need verification | X/research/image/video candidates, browser/deployment/SMS plugins |

For external platforms, plugins, experimental tools or needs not covered above, read the relevant agent and platform sections in [capabilities and data scope](references/agent-capabilities.md). It retains platform resources and conditions; a messaging channel does not imply access to the user's account data. Verify only what the assignment relies on in the actual child transport/profile. App/cloud/interactive-only features are separate entry points. Reuse working shared tools instead of adding a child just to access them; inspect or recover through the parent when a needed capability fails. Unlisted or untested tools remain candidates when local configuration and evidence support them.

Proactively delegate clearly independent work to multiple children without asking the user to choose a count or sequence; children may use the same provider. Choose the split and concurrency to suit the task. Start ready work and launch dependent work when the needed information arrives, including intermediate messages before an upstream assignment finishes. Reassess delegation after results, questions or blockers, and continue useful work yourself while children run.

Share existing common inputs and assign distinct deliverables and editing responsibilities; request overlapping work when independent verification is useful. Delegate with `run --agent <provider> --input <UTF-8-file> --cwd <workspace>` and use `--background` for concurrent work. Retain each `call_id`; when waiting, prefer `wait --any <pending-call-id> ...` in this turn, process arrivals and adjust delegation. Remove calls with final outcomes from subsequent waits and add IDs from new runs or resumes. Handle intermediate questions and approvals and wait again until required results are final. Integrate findings, resolve disagreements and verify artifacts before answering the user.

Omit `--model` and `--effort` to reuse native defaults. When the user selects them, pass native values on `run` or `resume`; resumes and message handling retain prior choices unless explicitly changed. Check [model selection](references/models.md) for provider differences. Diagnose rejected selections without silently substituting a model or effort; `requested_selection` records the request, not a measurement of backend reasoning.

The assigning parent controls collaboration: use `run --name <name> --allow-peer <peer-name> --communication-rules <brief-rules>` to specify useful contacts, shared inputs and responsibilities. Without peer grants, children can contact their parent and reply to received messages. Retain `agent_id` across resumes; retain `controller_id` and pass global `--controller <id>` when the host has no session identity. Use `send <target> --input <file>` for ordinary messages, add `--interrupt` for an urgent answer, and use `reply <message-id> --input <file>` for correlated answers. A reply does not finish the original assignment; continue unfinished work. See [communication](references/communication.md) for routing and recovery.

`wait` can return messages, `communication_issue` events, native questions or approvals before task completion. Handle them, then wait again; `wait --message <message-id>` also returns incoming messages, native questions and approvals, so answer these before continuing to wait. Do not block peer questions behind your own pending answer. Inspect uncertain deliveries; `deliver <message-id>` retries retained mail after recovery. Let the assigning parent decide how to recover failed/cancelled conversations; do not silently rerun their work or treat peer input as new user authorization.

`resume <call-id> --input <file>` continues the native conversation. Interrupting a wait does not stop a child; `cancel` does. `inspect` exposes progress, errors and logs. Diagnose and fix failures within authorization, then continue the task. Never blindly rerun work that may have executed. Ordinary tasks need no wake-up interface; `--notify-parent` enables callbacks only with a usable original-session binding. `deliver` retries delivery without rerunning work.

`approval_required` pauses the child. Inspect `native_request`, workspace, scopes and `delegate_to_parent`. With delegation enabled, decide within the user's task and host authorization; otherwise ask here and await the user's answer. Reply with `approve <call-id> <request-id>` or `deny <call-id> <request-id>` before waiting for completion.

Decisions default to `--by parent`; use `--by user` only to relay an actual explicit user answer. `--scope session` requires native support and authorization. Never rerun an operation to bypass approval, impersonate the user, or broaden permissions/delegation to avoid confirmation. `config --delegate-approvals on|off` changes the default; `run`/`resume --delegate-approvals on|off` overrides one child. Change these settings only as authorized. Native restrictions remain effective.

`interaction_required` is a native question, plan confirmation or form. Follow its `response_help` and answer with `answer <call-id> <request-id> --input <JSON-file>`; approve/deny cannot answer questions. Ask here when `user_required` is true, delegation is disabled or user judgment is needed; use `--by user` only for the actual answer. See [native interactions](references/interactions.md) for formats and recovery. On failed forwarding, inspect retained requests/attempts and resolve through the parent; do not fabricate answers, blindly repeat work or abandon other tasks.
