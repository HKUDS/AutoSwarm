# Review feedback integrity

Company Native review output could lose `summary`, `blocking_issues`, and
`followups` when a reviewer returned the flat JSON format recommended in the
prompt. The Company extractor took only the `review_verdict` string. The
external adapter had a separate, previously corrected implementation. A
nonempty `reject` summary then bypassed the raw-result fallback, sending the
worker into rework without its reviewer's instructions.

Historical replay locates the original extractor in `1500b79d` (2026-04-02).
`8eec916b` corrected external parsing and added runtime salvage; `711d94b0`
removed the salvage while changing verdict policy. The current main history
starts at `d7893197` with the Native defect already present. `b15e5392` did not
change these parsing functions.

## Resulting behavior

- Native Company and external adapters share `opc/core/review_verdict.py`.
  Flat, nested, normalized, and legacy alias formats retain all recognized
  review fields. Conflicting text cannot override an explicit artifact decision.
- A reject needs a reason in `summary` or a specific `blocking_issues` entry.
  Empty values, bare decision tokens and nonblocking followups alone do not
  satisfy that contract. This is structural validation, not an LLM evaluation
  of whether the reason is persuasive; there is no minimum summary length.
- Missing feedback produces `REVIEW_REJECT_FEEDBACK_MISSING` for the original
  reviewer role and seat. The worker remains awaiting review, with its rework
  count unchanged. The reviewer gets up to two automatic retries.
- Exhaustion fails the review and exposes the failed review card. It does not
  approve the worker or issue worker rework. The dispatcher reports the reviewer
  error instead of describing this state as a wait for human approval.
- Retry state is journaled on review cards. Reconciliation preserves the error
  and owner if creating the next card fails. It does not restart an exhausted
  retry chain. The budget is scoped to the current report/review sequence.
- The legacy review-gate path retries the reviewer task itself, with a scoped
  correction in its persisted context. It never invokes worker rework for a
  missing-reason reject.
- Reviewer errors are rendered in context and Native's durable user correction
  turn. Provider retries and resumption reuse the same correction revision.
- Label-only legacy metadata can recover details from that same review's raw
  result. This change does not rewrite an already running worker's stored
  feedback or perform an online database migration.

Existing parse-failure handling for unrelated unparseable verdicts and the
policy for valid worker rework caps are outside this change. A malformed reply
during missing-feedback correction remains a reviewer output error and cannot
fall through to the older parse-failure auto-close policy.

## Validation

`tests/test_review_verdict_integrity.py` covers shared parsing, malformed and
legacy fields, conflicting decisions, store reload into worker context, actual
Native correction construction, reviewer identity, bounded retries across
executor restart, interrupted retry-card creation, stale reviews, and legacy
gate execution. Existing review retry, context, Company controller, external
agent, isolation, Native and durable-interaction suites provide regression
coverage. Tests use isolated databases and deterministic model stubs; they do
not restart the UI server or alter a live company run.

The combined regression run passed **825 tests and 69 subtests**. An additional
read-only replay of the actual `test0001` review task
`5087464d-2db1-40f3-ba24-a7680f517ce6` preserved its 297-character summary,
four blocking issues and three followups through parsing, an isolated database
save/reload, and worker rework-context construction. Both fresh output and
legacy label-only metadata passed; external and Native parsing agreed. This
replay used the saved review output, without making new model calls.

The installed backend process must load the updated code before new review
results use this behavior. No frontend asset rebuild is required: existing
progress/error rendering and failed-card presentation are reused.
