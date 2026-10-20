---
name: opc-collab
description: Collaborate with other agents in AutoSwarm company mode via the `opc-collab` CLI — send messages, request user input, delegate work, read the manager board, respond in meetings, propose task adjustments.
---

# AutoSwarm company-mode collaboration

You are running inside an AutoSwarm "company mode" run as one of several agents
collaborating on a larger task. This skill gives you a local command, `opc-collab`,
that you invoke through the shell to talk to teammates, delegate child work,
and read your manager board.

The CLI is on your `PATH`. Every invocation reads its identity (who you are,
which project/session, which task) from environment variables that AutoSwarm
already set for you before launching this process — you do NOT pass those.

## When to use this skill

- You need to message another role (DM, broadcast, or blocking question).
- Your own current work item is blocked on a user decision or missing input.
- You are a manager and need to delegate child work items, or inspect your
  kanban board.
- You are in a meeting and need to respond or finalize a decision.
- You want to propose a runtime task adjustment (replan).

Do not use this CLI for general shell work, file edits, or code changes —
those go through your native tools (`Bash`, `Edit`, `Read`, etc.).

## How to call it

General form:

```bash
opc-collab <tool> --args-json-file args.json
```

All arguments go in the JSON object. The CLI prints a JSON result on stdout
and exits 0 on success, 1 on error (with the error message on stderr and a
`{"error": "..."}` body on stdout).

Prefer `--args-json-file` or `--args-stdin` over inline JSON. They are
unambiguous for nested objects and arrays, and they work consistently on
Linux, macOS, Windows PowerShell, and Windows CMD. If `OPC_COLLAB_CLI` is set,
use that executable path; otherwise use `opc-collab` from `PATH`.
In AutoSwarm-spawned Windows runs, do not use `--args-json` or pipe JSON into
`--args-stdin`; command-line and PowerShell pipeline text can corrupt non-ASCII
before it reaches the CLI. Write the JSON object to a UTF-8 file and call
`opc-collab <tool> --args-json-file <file>` instead.
PowerShell-safe UTF-8 file write:
`$enc = New-Object System.Text.UTF8Encoding $false; [System.IO.File]::WriteAllText($path, $json, $enc)`.

## Available tools

Not every tool is available every turn. The runtime narrows the surface based
on your role and phase; if you call a tool that is not allowed, the CLI
prints an error telling you which tools ARE allowed.

### Messaging

- **`inbox`** — check or acknowledge your mailbox without implicitly marking
  messages read. Use `status` for counts, `peek` for actionable message
  bodies, and `ack` only after handling messages that do not need a reply.
  `reply_message` automatically acknowledges the original message. If a
  manager-board action already handled the matching approval/review request,
  acknowledge that inbox message with `ack`.
  ```json
  {
    "action": "status"
  }
  ```
  ```bash
  opc-collab inbox --args-json-file args.json
  ```
  ```json
  {
    "action": "ack",
    "message_ids": ["<msg_id>"]
  }
  ```
  ```bash
  opc-collab inbox --args-json-file args.json
  ```

- **`send_dm`** — async direct message.
  ```json
  {
    "to_agent": "reviewer",
    "subject": "Draft ready for review",
    "body": "The v1 draft is in ./deliverables/draft.md. Please review."
  }
  ```
  ```bash
  opc-collab send_dm --args-json-file args.json
  ```

- **`ask_peer_and_wait`** — blocking peer question; pauses this run until
  the peer replies or the timeout fires.
  ```json
  {
    "to_agent": "cto",
    "subject": "Which DB shall I target?",
    "body": "Postgres or SQLite for the prototype?",
    "timeout_seconds": 300,
    "on_timeout": "continue"
  }
  ```
  ```bash
  opc-collab ask_peer_and_wait --args-json-file args.json
  ```

- **`reply_message`** — reply to a received message.
  If the reply is rejected by policy or transport but the message has already
  been handled through the board or current task result, use `inbox` with
  `action="ack"` for that message.
  ```json
  {
    "message_id": "<msg_id from your inbox>",
    "body": "Go with Postgres — we already have it in prod."
  }
  ```
  ```bash
  opc-collab reply_message --args-json-file args.json
  ```

- **`broadcast_issue`** — raise an async issue to multiple roles.
  ```json
  {
    "to_agents": ["cto", "cmo"],
    "subject": "Blocked on pricing policy",
    "body": "Need a decision before I can land the checkout flow."
  }
  ```
  ```bash
  opc-collab broadcast_issue --args-json-file args.json
  ```

- **`request_user_input`** — pause your own current work item and ask the
  user for missing input. This tool is self-scoped: do not pass
  `target_role`, `target_task_id`, `work_item_id`, or any other target id.
  AutoSwarm reads your role, task, seat, and work item from the runtime
  environment. After it returns `requires_user_input=true`, stop the current
  turn and wait for the user's reply.
  ```json
  {
    "reason": "Blocked on a deployment choice that only the user can make.",
    "questions": [
      {
        "id": "deployment_region",
        "header": "Deployment region",
        "question": "Which deployment region should I target?",
        "options": [
          {"label": "US East", "description": "Use us-east-1"},
          {"label": "EU West", "description": "Use eu-west-1"},
          {"label": "Asia", "description": "Use ap-east-1"}
        ],
        "allow_freeform": true,
        "required": true
      }
    ],
    "context_note": "I can continue once the region is selected."
  }
  ```
  ```bash
  opc-collab request_user_input --args-json-file args.json
  ```
  Each question can have up to three selectable options. Option ids default to
  `a`, `b`, and `c`; the UI also offers Other/freeform unless
  `allow_freeform=false`. Legacy `"questions": ["..."]` remains valid and
  produces a freeform-only review card.

### Manager / delegation

### Leader Delegation Planning Overlay

Before `delegate_work`, managers should convert the upstream assignment into
self-contained child work packets. This overlay does not replace your role
prompt; it only governs delegation quality.

Use this checklist before dispatching:

- Preserve upstream intent.
- Determine local leadership scope.
- Map deliverables to owners.
- Decompose into work items.
- Distinguish hard dependencies from can-start-now prework.
- Split one role into multiple phase items when that unlocks progress; do not split mechanically.
- Add dependencies / non-overlap boundaries.
- Fill `planning_context`.
- Fill per-item `brief`, `outputs` / `deliverables`, `done_when` / `acceptance_criteria`,
  `delegation_rationale`, and `non_overlap_guard`.

- **`delegate_work`** — as a manager, create child work items for your direct
  reports. Every item needs `role_id`, `title`, and a complete `brief`
  (legacy `summary` is accepted). `scope_key` is optional; the runtime can
  generate one. `depends_on` can name a sibling role, scope key, work item
  ref, or work item id. The same `role_id` may appear in multiple items when
  each item has a distinct deliverable, phase, dependency shape, or handoff.
  ```json
  {
    "planning_context": "Leader planning summary: upstream goal, local scope, deliverable map, can-start-now work, hard dependencies, sequencing, assumptions.",
    "items": [
      {
        "role_id": "target_role",
        "title": "Startable preparation slice",
        "brief": "Context: This phase can start before upstream dependencies finish. Mission: Produce preparation artifacts that unblock later finalization. Required outputs: Leave the concrete artifact or handoff named in outputs. Location / handoff: Use the agreed output path or report channel. Quality expectations: Meet the done_when criteria below. Boundaries: Stay inside this phase. Insufficient work: Do not submit only vague notes. Completion report: List artifacts, verification, assumptions, and blockers.",
        "scope_key": "target-role-prep",
        "work_kind": "execute",
        "outputs": ["Concrete preparation artifact or handoff"],
        "done_when": ["Preparation output is useful for dependent finalization"],
        "delegation_rationale": "Why this direct report owns this slice.",
        "non_overlap_guard": "This item owns preparation; a sibling item owns finalization.",
        "coordination_notes": "Record assumptions that finalization must revisit.",
        "depends_on": []
      },
      {
        "role_id": "target_role",
        "title": "Dependency-bound finalization slice",
        "brief": "Context: This sibling item finalizes the same role's output after dependencies land. Mission: Integrate dependency evidence into the final production deliverable. Required outputs: Leave the final artifact named in outputs. Location / handoff: Use the agreed output path or report channel. Quality expectations: Verify against prep and dependency inputs. Boundaries: Do not redo prep unless quality requires it. Insufficient work: Do not finish before dependencies are incorporated. Completion report: List final artifacts, verification, assumptions, and blockers.",
        "scope_key": "target-role-finalize",
        "work_kind": "execute",
        "outputs": ["Final dependency-aware artifact"],
        "done_when": ["Final output incorporates dependencies and is ready to integrate"],
        "delegation_rationale": "Same role owns final quality for this slice.",
        "non_overlap_guard": "This item owns finalization; sibling prep owns early scaffolding.",
        "coordination_notes": "Use precise scope_key dependencies when a role owns multiple items.",
        "depends_on": ["target-role-prep"]
      }
    ]
  }
  ```
  ```bash
  opc-collab delegate_work --args-json-file args.json
  ```
  `brief` is the child work item's full assignment packet, not a short
  summary. Do not use `description`. Recommended `brief` shape: `Context`,
  `Mission`, `Required outputs`,
  `Location / handoff`, `Quality expectations`, `Boundaries`,
  `Insufficient work`, `Completion report`.
  When the same role gets multiple sibling items, set stable `scope_key`
  values and reference those keys in `depends_on`; broad role references may
  become ambiguous.

- **`manager_board_read`** — READ-ONLY view of your direct reports' child
  items. For your current manager board, omit `parent_work_item_id`; the
  runtime uses `$OPC_WORK_ITEM_ID` automatically. Pass an explicit id only
  when it is a FULL WorkItem id returned by a prior tool call (no truncation).
  Never pass `$OPC_TASK_ID` or `$OPC_RUNTIME_TASK_ID`; those are runtime Task
  ids, not WorkItem ids.
  After you approve, reject, or otherwise handle a child through the board
  review flow, acknowledge any matching approval/review inbox message unless
  `reply_message` already did it.
  ```json
  {
    "include_children": true
  }
  ```
  ```bash
  opc-collab manager_board_read --args-json-file args.json
  ```
  Do NOT use legacy aliases like `parent_id`, `include_outputs`, `scope`,
  `reason`, `note` — the tool rejects them.

- **`modify_work_item`** — revise an existing child WorkItem on your current
  manager board when the owner is still correct but the title, brief,
  deliverables, acceptance criteria, dependencies, or coordination fields are
  wrong. Read the board first, then pass the exact full `work_item_id`.
  Prefer this over creating a duplicate replacement item.
  ```json
  {
    "work_item_id": "work-item-id-from-manager_board_read",
    "task_brief": "Revised concrete assignment for the same owner.",
    "deliverables": ["Updated artifact or handoff"],
    "acceptance_criteria": ["Updated acceptance condition"],
    "depends_on": ["sibling-scope-key-or-full-work-item-id"],
    "reason": "Why the existing card needed revision.",
    "reset_to_ready": true
  }
  ```
  ```bash
  opc-collab modify_work_item --args-json-file args.json
  ```

- **`delete_work_item`** — cancel or hide an obsolete/wrong child WorkItem on
  your current manager board so it no longer blocks parent synthesis. Read the
  board first, then pass the exact full `work_item_id`.
  ```json
  {
    "work_item_id": "work-item-id-from-manager_board_read",
    "reason": "Why this child card is obsolete or wrong.",
    "replacement_dependency_work_item_ids": []
  }
  ```
  ```bash
  opc-collab delete_work_item --args-json-file args.json
  ```

- **`close_human_review`** — close the owner-facing delivery review when you
  decide the user's latest directive means the delivery is accepted and no
  further internal work is needed. Do not call this for requested changes; use
  board tools or reply directly instead.
  ```json
  {
    "summary": "The user accepted the delivery; no further work is required.",
    "user_message": "Acknowledged. I am closing the human review for this delivery."
  }
  ```
  ```bash
  opc-collab close_human_review --args-json-file args.json
  ```

### Meetings

- **`start_meeting`** — open a meeting and wait for the outcome.
  ```json
  {
    "topic": "Prioritization for Q2",
    "participants": ["cto", "cmo", "cfo"],
    "agenda": ["Goals", "Trade-offs", "Decision"]
  }
  ```
  ```bash
  opc-collab start_meeting --args-json-file args.json
  ```
- **`respond_meeting`** — respond to a live meeting, optionally finalize it
  if you are the decision owner.

### Other

- **`propose_task_adjustment`** — propose a runtime replan (summary + changeset).
- **`route_work`** — coordinator tool; `send_followup` / `spawn_task` /
  `escalate`.
- **`read_inbox`** / **`read_meeting`** / **`list_colleagues`** — debug/admin
  reads that may not be available outside of debug mode.

## Argument contract rules

1. **Use the exact argument names shown above.** `brief`/`summary`,
   `outputs`/`deliverables`, and `done_when`/`acceptance_criteria` are the
   supported aliases; do not invent others.
2. **Work-item IDs are verbatim.** When a prior response hands you a
   `work_item_id`, copy the full string unmodified into the next call. Never
   truncate.
3. **`delegate_work` items use `brief` or legacy `summary`, not `description`.**
4. **`manager_board_read` takes only `parent_work_item_id` and optional
   `include_children`.** No other args are accepted. For your current manager
   board, omit `parent_work_item_id`; never use `$OPC_TASK_ID`.

## Error handling

- Exit code 0 + JSON on stdout: success, use the payload.
- Exit code 1 + `{"error": "..."}` on stdout, message on stderr: the call
  failed. Read the error and decide whether to retry with corrected args,
  escalate, or continue with what you have.
- An error containing "not available for this run" means the runtime did not
  grant this tool for your current role/phase. The error message lists the
  tools you CAN call — pick one of those instead.

## Only transport

`opc-collab` is the supported way to reach your teammates. If the CLI
returns an error, inspect the error message and retry or adapt.
