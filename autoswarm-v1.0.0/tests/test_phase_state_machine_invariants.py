"""Invariants the Phase state machine must always satisfy.

These tests are the *meta-fix* for the class of bugs where:
  (A) some non-terminal phase had no path out (dead-end → stuck card)
  (B) terminal phases were reachable from too narrow a set, so failure /
      cancel paths could not interrupt arbitrary in-flight work
  (C) reusing a deterministic ID across multiple "attempts" (e.g. the
      review work item per worker) created stuck states once a previous
      attempt landed in a terminal phase

If anyone adds a new Phase value or rewrites ALLOWED_TRANSITIONS, these
tests will fail loudly when an invariant is broken — preventing the same
bug class from ever recurring.
"""

from __future__ import annotations

from collections import deque

import pytest

from opc.core.models import Phase
from opc.layer2_organization.phase import (
    ALLOWED_TRANSITIONS,
    DONE_PHASES,
    IN_PROGRESS_PHASES,
    IN_REVIEW_PHASES,
    InvalidPhaseTransition,
    RUNNABLE_PHASES,
    TERMINAL_PHASES,
    TODO_PHASES,
    coerce_phase,
    is_resumable_after_claim_release,
    is_runnable,
    is_runtime_auxiliary_work_item,
    is_stale_claim_releasable,
    is_terminal,
    kanban_column,
    validate_transition,
)


def _reachable(source: Phase) -> set[Phase]:
    """All phases reachable from source via ALLOWED_TRANSITIONS (BFS)."""
    seen: set[Phase] = {source}
    queue: deque[Phase] = deque([source])
    while queue:
        current = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS.get(current, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


# ── A: no dead ends ────────────────────────────────────────────────────────


def test_no_dead_end_phases() -> None:
    """Every non-terminal phase must reach at least one DONE phase.

    A "dead-end" is a non-terminal phase whose reachable set excludes all
    of {APPROVED, FAILED, CANCELLED}. A card that lands there is stuck
    forever — exactly the symptom that motivated this whole refactor.
    """
    for phase in Phase:
        if phase in TERMINAL_PHASES:
            continue
        reachable = _reachable(phase)
        terminal_reach = reachable & DONE_PHASES
        assert terminal_reach, (
            f"{phase.value} cannot reach any terminal phase — it is a "
            f"dead end. Reachable: {sorted(p.value for p in reachable)}"
        )


# ── B: universal failure / cancel transitions ─────────────────────────────


def test_universal_failure_transition() -> None:
    """Every non-terminal phase must allow a direct → FAILED transition.

    If a process raises an exception or the agent runtime decides to abort,
    the runtime must always be able to record the failure regardless of
    the current sub-state. Otherwise the abort path silently no-ops and
    the card hangs.
    """
    missing = [
        p.value
        for p in Phase
        if p not in TERMINAL_PHASES and Phase.FAILED not in ALLOWED_TRANSITIONS.get(p, frozenset())
    ]
    assert not missing, (
        f"phases without a → FAILED transition: {missing}. Every "
        f"non-terminal phase must allow abort-on-error."
    )


def test_universal_cancel_transition() -> None:
    """Every non-terminal phase must allow a direct → CANCELLED transition.

    Users (or the system) can cancel a card at any moment. If some
    sub-state can't reach CANCELLED, cancel requests will silently fail.
    """
    missing = [
        p.value
        for p in Phase
        if p not in TERMINAL_PHASES and Phase.CANCELLED not in ALLOWED_TRANSITIONS.get(p, frozenset())
    ]
    assert not missing, (
        f"phases without a → CANCELLED transition: {missing}. Every "
        f"non-terminal phase must allow user/system cancellation."
    )


# ── B': self-completion path for review-style work ─────────────────────────


def test_running_can_self_complete() -> None:
    """RUNNING must be able to → APPROVED directly (self-completion).

    Some work items (review cards, automated checks) finish without going
    through external review. Without RUNNING → APPROVED, _finalize_review_*
    silently fails to close review cards and they stay RUNNING forever.
    """
    assert Phase.APPROVED in ALLOWED_TRANSITIONS[Phase.RUNNING], (
        "RUNNING → APPROVED must be allowed so review work items (which "
        "do not themselves need to be reviewed) can naturally terminate."
    )


# ── C: terminal phases are immutable ───────────────────────────────────────


def test_terminal_phases_immutable() -> None:
    """APPROVED / FAILED / CANCELLED must have NO outgoing transitions."""
    for phase in TERMINAL_PHASES:
        outgoing = ALLOWED_TRANSITIONS.get(phase, frozenset())
        assert not outgoing, (
            f"terminal phase {phase.value} must have no outgoing "
            f"transitions, but allows: {sorted(p.value for p in outgoing)}"
        )


# ── D: validate_transition behavior contracts ─────────────────────────────


def test_validate_transition_accepts_creation() -> None:
    """previous=None (initial creation) accepts any target phase."""
    for target in Phase:
        validate_transition(None, target)


def test_validate_transition_idempotent_on_same_phase() -> None:
    """Idempotent same-phase writes are always allowed (no-op)."""
    for phase in Phase:
        validate_transition(phase, phase)


def test_validate_transition_close_idempotent_on_terminal() -> None:
    """Re-closing a terminal phase to the same terminal must be a no-op
    (callers should not need to defensively check phase before closing)."""
    for phase in TERMINAL_PHASES:
        validate_transition(phase, phase)


def test_validate_transition_rejects_terminal_out() -> None:
    """Any transition out of a terminal phase to a different phase is rejected."""
    for src in TERMINAL_PHASES:
        for tgt in Phase:
            if tgt == src:
                continue
            with pytest.raises(InvalidPhaseTransition):
                validate_transition(src, tgt)


# ── E: every Phase is exhaustively classified into a kanban column ────────


def test_every_phase_has_kanban_column() -> None:
    expected_columns = {"todo", "in_progress", "in_review", "done"}
    for phase in Phase:
        column = kanban_column(phase)
        assert column in expected_columns, f"{phase.value} mapped to unknown column {column!r}"


def test_phase_set_partitions_match_kanban_columns() -> None:
    """The TODO/IN_PROGRESS/IN_REVIEW/DONE Phase sets must form a complete
    disjoint partition that matches the kanban_column projection."""
    union = TODO_PHASES | IN_PROGRESS_PHASES | IN_REVIEW_PHASES | DONE_PHASES
    assert union == set(Phase), (
        f"phases not classified by any column: {set(Phase) - union}; "
        f"phases classified by multiple columns: "
        f"{[p.value for p in Phase if sum(p in s for s in (TODO_PHASES, IN_PROGRESS_PHASES, IN_REVIEW_PHASES, DONE_PHASES)) > 1]}"
    )
    for phase in TODO_PHASES:
        assert kanban_column(phase) == "todo"
    for phase in IN_PROGRESS_PHASES:
        assert kanban_column(phase) == "in_progress"
    for phase in IN_REVIEW_PHASES:
        assert kanban_column(phase) == "in_review"
    for phase in DONE_PHASES:
        assert kanban_column(phase) == "done"


# ── F: helper coverage ─────────────────────────────────────────────────────


def test_coerce_phase_round_trip() -> None:
    for phase in Phase:
        assert coerce_phase(phase) is phase
        assert coerce_phase(phase.value) is phase


def test_coerce_phase_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        coerce_phase("not_a_real_phase")


def test_runnable_set_disjoint_from_terminal() -> None:
    assert RUNNABLE_PHASES.isdisjoint(TERMINAL_PHASES), (
        "RUNNABLE_PHASES and TERMINAL_PHASES must not overlap — terminal "
        "cards must never be re-claimed by the dispatcher."
    )


def test_is_runnable_and_is_terminal_are_disjoint() -> None:
    for phase in Phase:
        if is_terminal(phase):
            assert not is_runnable(phase)


# ── G: review work-item ID per-attempt uniqueness ──────────────────────────


def test_review_work_item_id_per_attempt_is_unique() -> None:
    """Each call to the per-attempt review-id helper for the same worker
    must return a different identifier — preventing the bug where one
    cancelled review attempt locks out all future attempts.

    This test exercises the helper itself; the company_mode call sites
    increment the attempt counter via worker metadata.
    """
    from opc.layer2_organization.company_mode import (
        review_work_item_id_for_attempt,
    )

    worker_id = "wi-test-12345"
    ids = {review_work_item_id_for_attempt(worker_id, n) for n in range(1, 6)}
    assert len(ids) == 5, f"expected 5 distinct ids, got {ids}"
    # Sanity: the IDs are derived from the worker so historical lookup works.
    for rid in ids:
        assert rid.startswith(f"review::{worker_id}::"), rid


# ── H: in-flight phase recoverability after stale claim release ───────────


def test_inflight_claims_are_releasable_but_only_execution_is_resumable() -> None:
    """Claim cleanup and worker re-execution are separate invariants.

    All in-flight claims belong to the dead process and must be released on
    restart. Active execution may then resume; passive review/human parents
    must wait for their auxiliary or external actor instead of running twice.
    """
    inflight = IN_PROGRESS_PHASES | IN_REVIEW_PHASES
    not_releasable = [p.value for p in inflight if not is_stale_claim_releasable(p)]
    assert not not_releasable, (
        f"in-flight phases whose dead claims cannot be released: {not_releasable}"
    )
    not_resumable = [
        p.value for p in IN_PROGRESS_PHASES
        if not is_resumable_after_claim_release(p)
    ]
    assert not not_resumable, (
        f"active execution phases that cannot resume: {not_resumable}"
    )
    incorrectly_resumable = [
        p.value for p in IN_REVIEW_PHASES
        if is_resumable_after_claim_release(p)
    ]
    assert not incorrectly_resumable, (
        f"passive review phases incorrectly resume worker execution: "
        f"{incorrectly_resumable}"
    )


@pytest.mark.parametrize(
    "value",
    [
        {"attention_work_item": True},
        {"report_execution_work_item": True},
        {"review_execution_work_item": True},
        {"work_kind": "report", "report_target_work_item_id": "parent"},
        {"work_kind": "review", "review_target_work_item_id": "parent"},
        {"metadata": {"attention_work_item": True}},
    ],
)
def test_runtime_auxiliary_identity_is_canonical(value: dict[str, object]) -> None:
    assert is_runtime_auxiliary_work_item(value)


def test_business_work_item_is_not_runtime_auxiliary() -> None:
    assert not is_runtime_auxiliary_work_item({"work_kind": "execute"})
