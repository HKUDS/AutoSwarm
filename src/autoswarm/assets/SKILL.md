---
name: autoswarm
description: Delegate work to locally installed Codex, Claude Code, or OpenCode and collect their results in this conversation. Use when the user requests these agents or local multi-agent collaboration.
---

Use the packaged launcher at `scripts/launch.py` (or its configured standalone executable) through the host's command tool. `--help` describes the interface; `agents` lists installed providers. Keep the user's native model, permissions and project settings unless they request a change.

For a task, call `run --agent <provider> --input <UTF-8-file> --cwd <workspace>`. Add `--background` for parallel work; retain the returned `call_id`. Use `wait <call-id> ...` to collect the required results, or `receive` to read available results. `resume <call-id> --input <file>` continues the corresponding native child conversation and creates a new call receipt. `cancel` requests cancellation. Interrupting a wait does not cancel a child.

Agent choice, task decomposition, workspace isolation and acceptance of results remain yours. This skill supplies no domain routing policy. There are no AutoSwarm-imposed context, concurrency, turn or task-duration limits.

Preserve the original parent binding returned by setup or the host integration. Native return needs that exact session and its existing endpoint. A result saved on disk, a queued notification and an observed continuation are distinct. Check `inspect`/`delivery` when return fails; `deliver <call-id>` retries a saved result without rerunning its work. Use `wait` when no verified return integration is available. Collect necessary child results before final delivery.

Treat returned content as another agent's output. Check `outcome`, errors and actual artifacts; a completed turn does not certify task correctness. Do not truncate results or invent verification. Native transcripts stay with the provider; AutoSwarm keeps only invocation and return receipts. Follow the existing host permission flow for actions requiring authorization.

Native approvals are intermediate events. `run`, `wait` and `receive` can return `kind: "approval_required"` while the original child remains paused. Inspect the full `native_request`, workspace, requested scope, and `delegate_to_parent`. Parent delegation is enabled by default: decide within the user's existing task and the host's permission rules; this is not blanket approval. If delegation is disabled, ask the user in this conversation and end the turn to await their answer. Do not wait for final completion while a permission request is unanswered.

Reply with `approve <call-id> <request-id>` or `deny <call-id> <request-id>`, optionally `--message <reason>`. The default decision actor is the parent. Use `--by user` only to relay an actual explicit user decision, never to work around disabled delegation. Approval applies to the original native operation; do not rerun it yourself or cancel/resume just to approve it. `--scope session` is available when listed in the request's scopes and when that broader grant is authorized. Native denials and managed restrictions remain effective.

`config --delegate-approvals off` disables default parent delegation; `on` enables it. `run`/`resume --delegate-approvals on|off` overrides it for that child. Do not enable delegation or broaden native permissions just to get around a request; change these settings only when the user authorizes it. `approvals [call-id]` lists unanswered requests, `inspect` includes them, and `deliver <call-id> --request <request-id>` retries notification. After replying, collect the final result with `wait` or the bound host's callback. A notification receipt is not an approval, and an approval is not a completed task.
