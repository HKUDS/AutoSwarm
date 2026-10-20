/**
 * Single-source-of-truth projection: KanbanPhase → kanban column id.
 *
 * This mirror MUST stay in sync with the backend projection in
 * ``opc/presentation/kanban.py:STATUS_TO_COLUMN`` and
 * ``opc/layer2_organization/phase.py:_PHASE_TO_COLUMN``. The test in
 * ``phaseHelpers.test.ts`` locks the mapping by enumerating all 14
 * phases; if the backend ever renames or reshapes the column set, that
 * test catches the drift before the UI silently mis-groups cards.
 *
 * The UI previously grouped cards by the backend-supplied ``columnId``
 * field, which is itself a projection of phase on the backend. Moving
 * the projection into the frontend removes a layer of indirection and
 * lets the UI stay internally consistent when future optimistic writes
 * only know the phase intent, not the derived column.
 */

import type { KanbanPhase } from '../types/kanban'

export const PHASE_TO_COLUMN: Record<KanbanPhase, string> = {
  // todo
  queued: 'todo',
  ready: 'todo',
  ready_for_rework: 'todo',
  waiting_dependencies: 'todo',
  // in-progress
  running: 'in-progress',
  waiting_for_peer: 'in-progress',
  waiting_for_children: 'in-progress',
  paused: 'in-progress',
  needs_attention: 'in-progress',
  // in-review
  awaiting_manager_review: 'in-review',
  awaiting_human: 'in-review',
  // done
  approved: 'done',
  failed: 'done',
  cancelled: 'done',
}

/**
 * Derive the kanban column for a task based on its phase.
 * Falls back to ``'todo'`` when phase is missing or unknown; callers
 * that already have a backend-supplied ``columnId`` should prefer that
 * value during the transition window and only use this helper when the
 * phase is trustworthy.
 */
export function deriveColumnFromPhase(phase: KanbanPhase | undefined | null): string {
  if (!phase) return 'todo'
  const mapped = PHASE_TO_COLUMN[phase]
  return mapped ?? 'todo'
}
