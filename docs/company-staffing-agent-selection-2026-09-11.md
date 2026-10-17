# Company staffing agent selection: test0001 investigation

## Cause and introduction

The locking behavior was introduced by **a5191f1885508cbc61a9ae0fe35093bac3fca453**,
2026-08-26, `feat: integrate Jiuwen agents and opaque teams`.
That commit added all three parts of the behavior:

1. Manual staffing compiled durable organization `external_team_bindings`,
   omitted covered descendants, and marked each Team boundary `staffing_locked`.
2. StaffingSelectionPanel and RecruitmentPanel disabled the executor selector for
   these persisted boundaries as well as descendants covered by an active Team.
3. Approval retained configured Team bindings even when a boundary's submitted
   execution agent was Native or another single-role executor.

The implementation conflated an organization default with an immutable choice
for the next company run. Merely enabling the HTML select would produce a false
UI: an all-Native reply still compiled the three Jiuwen Teams.

The trigger in this workspace is the local corporate organization file:
`.opc/config/company_orgs/org_corporate_config.yaml`. It contains three enabled
subtree bindings for `cto`, `cmo`, and `coo`, with empty metadata. The committed
file at both the introducing commit and current HEAD has an empty binding list.
The local file's modification time was 2026-09-03 20:03:58 +0800. Git does not
identify which local operation wrote those entries. The recent Native runtime
hardening did not edit that file or introduce this locking path.

The actual pending `test0001` checkpoint is
`47381e9f-2a33-4e1d-8c26-167746c00475`, created 2026-09-11 17:28:58.
It contains only CEO, CTO, CMO, and COO; the latter three are locked. Its router
decision has `preferred_agent: null`, while session metadata contains Native.
The preflight choice is therefore resolved from organization defaults. This
repair restores explicit per-role choice regardless of the initial default.

## Changes

- Manual staffing exposes every organizational role. A configured Team boundary
  supplies an initial agent choice; the boundary itself remains editable.
- Explicit session defaults and approved per-role choices can replace configured
  Teams for this run. Team compilation copies retained bindings, preserving their
  execution limits and other settings without changing shared configuration.
- The resolved Team list is passed into seat enrichment and runtime-plan
  compilation. Individual employee assignments and Native identities are restored
  when a Team is deselected.
- Auto Recruit receives the effective covered-role set for this run. Covered
  roles are excluded from recruitment decisions, but remain represented in the
  confirmation card with no hire proposal. Their individual executor choices are
  retained so the user can still switch the boundary or a descendant later.
- Recruitment feedback uses the current role choices as well.
- Old pending manual-staffing cards are expanded on read using the same helper
  for initial snapshot, live WebSocket metadata, and approval consumption.
  Checkpoint IDs, interaction state, and stored payloads are not rewritten.
  Historical resolved cards retain their original representation.
- The frontend retains the old lock fallback for an older running backend, so
  rebuilding static assets alone cannot falsely advertise an executable choice.

Selecting a Team still gives it ownership of its covered subtree. Those
descendants cannot independently execute until the owning boundary is switched
away from Team. Approved cards remain immutable.

## Validation

Evidence directory: `/data2/bjdwhzzh/tmp/staffing-agent-choice-20260911/`.

- Related backend suite: **291 passed, 2 subtests passed**. Includes Jiuwen
  integration, recruiter, role updates, parallel isolation, Native stop/resume
  identity, durable interactions, runtime configuration and actor company mode.
- Dedicated regression file: **13 passed**, including corporate/custom
  organizations, Native/Codex/Jiuwen single-role choices, mixed Teams, disabled
  defaults, nested boundaries, old checkpoint projection and consumption, Auto
  Recruit and recruitment confirmation. Eleven are included in the 291 above;
  two additional cases were added and passed afterward.
- Session/UI compatibility suite: **245 passed, 8 subtests passed**, covering
  session integration, organization snapshots, company kanban, work-item logs
  and message sanitization. Across the backend runs, 538 distinct tests passed.
- Frontend contracts: **13 test files passed**; TypeScript check passed.
- Playwright: actual dropdown interactions passed for manual staffing and
  recruitment confirmation, including Team → Native → Team → Native,
  preserving descendant choices, submitted metadata and disabled historical cards.
- The same browser test consumed a read-only projection of the actual test0001
  checkpoint: **4 → 11 roles**, three editable Team boundaries, all eleven
  individual executor selectors and staffing panels restored after deselection.
- Production frontend build passed. The existing bundle-size warning remains.
- `git diff --check` passed.

These are deterministic staffing, topology and UI tests. No paid model run or
full benchmark was started, and the user's pending staffing choice was not
approved by the test harness. The corporate configuration, test0001 database,
and Talen source were not modified.

## Loading the fix

The Python backend must be restarted to load the changed staffing compiler and
legacy-card projection, then the browser refreshed. The existing test0001
checkpoint can be used after restart; recreating the project is unnecessary.
The observed Office UI process was launched with `opc ui --port 8799` from this
repository. That command remains compatible; new launches can use
`autoswarm ui --port 8799`. This work does not restart that user-owned process.

## Cross-project and cross-session verification

The production changes have no project-name or checkpoint-ID special case.
Four additional regressions passed for both corporate and custom organizations,
using real project-default files and separate engine instances sharing the same
organization configuration:

- Saving Native choices in project A does not change project B's defaults or
  lock B's selectors. Compiling B's Team plan leaves A's compiled plan unchanged.
- A new session can inherit a previously selected Team as an editable default,
  switch to Native, persist that choice, and retain it after an engine reload.
  It can subsequently choose Team again; an explicit Native session preference
  also overrides a saved Team default.

The dedicated test file now has **17 passing cases**. Its latest log is
`project-session-isolation-tests.log` in the evidence directory above. These four
new cases bring the distinct backend regression count to 542; no additional
production changes were needed for project/session isolation.
