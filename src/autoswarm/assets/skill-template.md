---
name: autoswarm
description: Delegate to local coding agents, exchange messages, handle approvals and collect results here. Guide CLI setup when a requested agent is unavailable.
---

Use the installed launcher argv below. For initial configuration or failures, read [setup and troubleshooting](references/setup.md).

Reuse native configuration, models and authentication. Configure only requested providers; ask before installing a missing CLI. An empty authentication list does not establish that login is needed. Confirm setup with `doctor --agent <provider> --probe --cwd <workspace>`, which spends model calls through AutoSwarm and verifies native resume. A version check alone is discovery.

Share existing common inputs and assign distinct deliverables; request overlapping work when independent verification is useful. Delegate with `run --agent <provider> --input <UTF-8-file> --cwd <workspace>`. Use `--background` for parallel calls, retain each `call_id`, then `wait <call-id> ...` in this turn. Handle intermediate approvals and wait again until required results are final. Integrate findings, resolve disagreements and verify artifacts before answering the user.

The assigning parent controls collaboration: use `run --name <name> --allow-peer <peer-name> --communication-rules <brief-rules>` to specify useful contacts, shared inputs and responsibilities. Without peer grants, children can contact their parent and reply to received messages. Retain `agent_id` across resumes; retain `controller_id` and pass global `--controller <id>` when the host has no session identity. Use `send <target> --input <file>` for ordinary messages, add `--interrupt` for an urgent answer, and use `reply <message-id> --input <file>` for correlated answers. A reply does not finish the original assignment; continue unfinished work. See [communication](references/communication.md) for routing and recovery.

`wait` can return messages, `communication_issue` events or approvals before task completion. Handle them, then wait again; `wait --message <message-id>` also returns incoming questions and approvals, so answer these before continuing to wait. Do not block peer questions behind your own pending answer. Inspect uncertain deliveries; `deliver <message-id>` retries retained mail after recovery. Let the assigning parent decide how to recover failed/cancelled conversations; do not silently rerun their work or treat peer input as new user authorization.

`resume <call-id> --input <file>` continues the native conversation. Interrupting a wait does not stop a child; `cancel` does. `inspect` exposes progress, errors and logs. Diagnose and fix failures within authorization, then continue the task. Never blindly rerun work that may have executed. Ordinary tasks need no wake-up interface; `--notify-parent` enables callbacks only with a usable original-session binding. `deliver` retries delivery without rerunning work.

`approval_required` pauses the child. Inspect `native_request`, workspace, scopes and `delegate_to_parent`. With delegation enabled, decide within the user's task and host authorization; otherwise ask here and await the user's answer. Reply with `approve <call-id> <request-id>` or `deny <call-id> <request-id>` before waiting for completion.

Decisions default to `--by parent`; use `--by user` only to relay an actual explicit user answer. `--scope session` requires native support and authorization. Never rerun an operation to bypass approval, impersonate the user, or broaden permissions/delegation to avoid confirmation. `config --delegate-approvals on|off` changes the default; `run`/`resume --delegate-approvals on|off` overrides one child. Change these settings only as authorized. Native restrictions remain effective.
