import type { ChatMessage } from '../types/chat'
import { resolveCanonicalTurnId, terminalAssistantTurnId } from './turnIdentity'

const COMMITTED_RESULT_SURFACE_KINDS = new Set([
  'child_task_result',
  'child_task_result_retry',
  'company_role_result',
  'company_role_result_retry',
  'child_result',
  'top_level_reply',
])

const RUNTIME_RESULT_SURFACE_KINDS = new Set([
  'runtime_v2_assistant',
  'runtime_v2_company_assistant',
])

function metadataValue(metadata: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = String(metadata[key] ?? '').trim()
    if (value) return value
  }
  return ''
}

function resultGenerationSuffix(metadata: Record<string, unknown>, kind: string): string {
  const explicitAttempt = metadataValue(
    metadata,
    'result_attempt',
    'attempt',
    'attempt_index',
    'retry_count',
    'retryCount',
  )
  const attempt = explicitAttempt || (kind.endsWith('_retry') ? 'retry' : '')
  const revision = metadataValue(
    metadata,
    'result_revision',
    'delivery_revision',
    'revision',
  )
  return [
    attempt ? `attempt:${attempt}` : '',
    revision ? `revision:${revision}` : '',
  ].filter(Boolean).join(':')
}

function withResultGeneration(
  base: string,
  metadata: Record<string, unknown>,
  kind: string,
): string {
  const suffix = resultGenerationSuffix(metadata, kind)
  return suffix ? `${base}:${suffix}` : base
}

/** Stable protocol identity shared by mirrors of one committed result. */
export function stableResultDeliveryKey(message: ChatMessage): string {
  const metadata = (message.metadata ?? {}) as Record<string, unknown>
  const kind = String(metadata.transcript_kind ?? metadata.kind ?? '').trim()
  if (!COMMITTED_RESULT_SURFACE_KINDS.has(kind) && !RUNTIME_RESULT_SURFACE_KINDS.has(kind)) {
    return ''
  }

  const deliveryId = metadataValue(
    metadata,
    'canonical_delivery_id',
    'result_delivery_id',
    'delivery_id',
  )
  if (deliveryId) return `delivery:${deliveryId}`

  if (RUNTIME_RESULT_SURFACE_KINDS.has(kind)) {
    const turnId = resolveCanonicalTurnId(metadata)
    return turnId ? `turn:${turnId}` : ''
  }

  // A committed result belongs to a task/work-item delivery, not merely to
  // the surrounding conversation turn. Parallel roles commonly share that
  // turn, so a turn-only fallback would collapse independent deliveries.
  const explicitSourceTaskId = metadataValue(metadata, 'source_task_id', 'child_task_id')
  const sourceTaskId = explicitSourceTaskId || (
    kind === 'top_level_reply'
      ? ''
      : metadataValue(metadata, 'task_id', 'taskId')
  )
  if (sourceTaskId) {
    return withResultGeneration(`source-task:${sourceTaskId}`, metadata, kind)
  }

  const workItemId = metadataValue(metadata, 'work_item_id', 'work_item_projection_id')
  if (workItemId) {
    return withResultGeneration(`work-item:${workItemId}`, metadata, kind)
  }

  const childSessionId = metadataValue(metadata, 'child_session_id')
  return childSessionId
    ? withResultGeneration(`child-session:${childSessionId}`, metadata, kind)
    : ''
}

export function stableMessageTimelineKey(message: ChatMessage): string {
  const metadata = message.metadata ?? {}
  const checkpointId = String(metadata.checkpoint_id ?? '').trim()
  if (checkpointId) return `checkpoint:${checkpointId}`

  const uiMessageId = String(metadata.ui_message_id ?? '').trim()
  const transcriptKind = String(metadata.transcript_kind ?? metadata.kind ?? '').trim()
  const metadataRole = String((metadata as Record<string, unknown>).role ?? '').trim().toLowerCase()
  const isUserTurn = message.sender === 'user'
    || metadataRole === 'user'
    || transcriptKind === 'runtime_v2_user_turn'
    || transcriptKind === 'top_level_user_turn'
  // The optimistic and persisted user surfaces share one client identity.
  if (isUserTurn && uiMessageId) return `ui:${uiMessageId}`

  // ChatStore attaches this only when one semantic result surface replaces
  // another. It preserves the already-mounted row without entering protocol
  // or persistence data.
  const retainedTimelineId = String(metadata.ui_timeline_id ?? '').trim()
  if (retainedTimelineId) return retainedTimelineId

  // Only a streamed runtime terminal owns the live draft's React slot.
  // Committed result surfaces may expose the same conversation turn through
  // terminalAssistantTurnId, but their row identity must remain delivery/task
  // scoped so parallel role results cannot collide.
  const turnId = RUNTIME_RESULT_SURFACE_KINDS.has(transcriptKind)
    ? terminalAssistantTurnId(message)
    : ''
  if (!isUserTurn && turnId) {
    return `turn:assistant:${turnId}`
  }

  const resultDeliveryKey = stableResultDeliveryKey(message)
  if (!isUserTurn && resultDeliveryKey) return `result:${resultDeliveryKey}`

  return `message:${message.id}`
}
