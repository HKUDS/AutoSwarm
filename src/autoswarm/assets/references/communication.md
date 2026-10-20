# Communicate during delegated work

Use the launcher from SKILL.md. The parent sets contacts and rules while delegating:

```text
run --agent codex --name implementation --allow-peer review --communication-rules "Share interface changes with review; own the implementation." --input implementation.txt --background
run --agent cursor --name review --allow-peer implementation --communication-rules "Ask implementation about interfaces; own the review." --input review.txt --background
```

Names are scoped to the assigning parent and may refer to peers started afterward. Retain the returned `call_id`, stable `agent_id` and `controller_id`. Hosts without a native session identity pass `--controller <controller-id>` before each main-agent command, including subsequent runs. Child commands inherit their own identity automatically. Children receive their allowed contacts and CLI instructions in their task context. These are local coordination rules, not an OS security boundary.

```text
send review --input question.txt
send implementation --interrupt --input urgent-question.txt
reply msg_ID --input answer.txt
wait --message msg_ID
receive
inspect msg_ID
deliver msg_ID
```

Targets are scoped names, stable agent IDs, returned call IDs or `parent`. The parent can contact its own children. Children can contact their parent and granted peers, or reply to a message actually addressed to them. A reply uses the received ID and returns to its sender without requiring a reverse peer grant. IDs remain valid through native resume.

Both send modes prefer live native input. Ordinary messages wait for the next native turn boundary where live input is unavailable. Urgent messages use native interruption there, wait for cancellation to complete, then continue the same native conversation with the message and unfinished assignment. Answer promptly using `reply`, then continue the assigned work unless the parent changes it. Do not finish the assignment merely because you answered a question. AutoSwarm does not kill the child process to deliver mail or grant its pending permissions.

For urgent questions choose `--interrupt`; ordinary send never forces interruption. A reply should contain the useful answer, references to artifacts when relevant, and any actual blocker. Avoid broadcasting shared background or repeating the parent's integration work.

`wait --message` returns correlated replies and also incoming questions, issues and approvals. Handle these and wait again when the desired reply has not arrived. `wait <call-ids>` likewise yields intermediate events, including approvals from newly resumed children, and does not imply completion of every task. `wait` without IDs waits for incoming events; `receive` polls them. A usable original Codex/OpenCode host binding also delivers messages through that host; failed notifications retain mail for tool collection. Installing the Skill does not create a universal App wake-up interface.

Delivery states describe facts: `queued` is retained locally, `submitted` is accepted by a native input mechanism, `delivered` is read through a tool, and `needs_attention` retains an error requiring the parent's decision. Native submission does not prove that a model has answered; `reply_to` correlates an actual answer. Message IDs help identify repeats after an uncertain native acknowledgement.

An idle, successfully completed conversation is resumed to handle new mail without repeating its old assignment; earlier results remain intact. Cancelled, failed or lost conversations are not restarted automatically. `communication_issue` gives the assigning parent the conversation/message ID and diagnostic. Inspect the call and message, fix the cause, then choose native resume, `deliver <message-id>`, or revised delegation. Do not blindly retry possibly executed operations. Permissions stay with the original native call; withdraw stale requests on a turn boundary and handle any newly requested approval normally.
