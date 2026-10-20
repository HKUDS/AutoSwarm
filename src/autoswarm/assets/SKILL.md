---
name: autoswarm
description: Use local Codex, Claude Code, or OpenCode together and collect their results. Guide CLI setup when a requested agent is unavailable.
---

Use the installed launcher argv below through the host's command tool. `--help` lists commands.

On first use, run `agents`. Recheck when a requested agent is unavailable. An App alone does not guarantee a callable CLI: first check its configured command, PATH, and any CLI exposed by the App. If none is usable, ask whether the user wants to install that agent's official CLI. Install only with their agreement, following current official instructions for their OS; rerun `agents` and continue the original task. Reuse existing configuration and authentication; a login or permission error is not evidence that installation is missing. If declined, explain which delegation is unavailable. Do not install all providers or substitute another agent without authorization.

Delegate with `run --agent <provider> --input <UTF-8-file> --cwd <workspace>`. Use `--background` for parallel work and retain each `call_id`. Collect with `wait <call-id> ...` or `receive`; `resume <call-id> --input <file>` continues that native conversation. `cancel` stops a child; interrupting a wait does not.

Keep the exact original parent binding. Use `wait` if native callbacks are unavailable. `inspect` shows saved results and delivery; `deliver <call-id>` retries delivery without rerunning work. Collect required results before finishing, and assess their outcome and artifacts.

`approval_required` is intermediate: the child is paused. Inspect `native_request`, workspace, scopes and `delegate_to_parent`. With delegation enabled, decide within the user's task and host authorization; otherwise ask the user here and await their answer. Reply using `approve` or `deny <call-id> <request-id>`, then collect the final result. Do not wait for completion before answering a pending approval.

Decisions default to `--by parent`; use `--by user` only to relay an actual explicit user answer. `--scope session` requires native support and authorization. Never rerun an operation to bypass approval, impersonate the user, or broaden permissions/delegation to avoid confirmation. `config --delegate-approvals on|off` changes the default; `run`/`resume --delegate-approvals on|off` overrides one child. Change these settings only as authorized. Native restrictions remain effective.
