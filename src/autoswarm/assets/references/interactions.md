# Native questions and recovery

`wait` / `receive`, including `wait --message`, can return `interaction_required` before a child finishes. Read `native_request`, `response_format`, `response_help`, `user_required` and `delegate_to_parent`. The original request stays with its child connection and native turn.

Write the answer JSON using the supplied format, then run:

```text
answer <call-id> <request-id> --input answer.json
```

Answer from the existing task and shared context when authorized. Ask in the main conversation when the answer needs user judgment or consent, `user_required` is true, or delegation is disabled; await the actual answer and relay it with `--by user`. Forms require user review. URL requests require explicit consent; accepting a URL prompt is not proof of completed authentication. Do not fetch authentication URLs, collect secrets on behalf of the user, or fabricate form answers. Ordinary tool permissions still use `approve` / `deny`; approving a question does not supply its answers.

Use exact native question IDs/text, option IDs/labels and selection modes. Do not replace the original tool input or alter the question. Codex blocking questions use an answers map keyed by question ID, while asynchronous question items use text delivered through the same active native turn; Claude and CodeBuddy use exact question text; Cursor uses option IDs; OpenCode uses arrays in question order; Muse uses questionId and selectedLabel, selectedLabels or freeText. `response_help` is authoritative for this request. Cancel/decline, plan rejection and Muse clarification follow their documented response formats.

After answering, wait again until required calls have final outcomes. Presentation receipts are not answers; native command acknowledgements are checked separately. Muse is marked engaged when its request reaches the parent, disarming its native countdown without treating it as answered. Native cancellation, settlement and turn completion invalidate outstanding questions. Never reuse an old request ID after interruption or resume; handle the newly surfaced request.

On `communication_issue` or `reply_error`, inspect the original request file, retained attempts and native logs. A rejected answer leaves an unresolved request available for an explicit correction. Uncertain delivery is not automatically repeated. Unknown interactions retain the full native payload and notify the parent; no fabricated consent is sent. Diagnose within authorization, continue independent work, and choose a corrected answer, native-session inspection, cancellation followed by resume, or revised delegation. Resume only after the original live call stops. Preserve already produced work and pending messages.

A question written as ordinary final text has no native reply channel: use `resume` to answer in that native conversation. No adapter can recover a closed native callback by writing to an old request file. Explain a genuine remaining decision to the user concisely; do not make them troubleshoot transport details.
