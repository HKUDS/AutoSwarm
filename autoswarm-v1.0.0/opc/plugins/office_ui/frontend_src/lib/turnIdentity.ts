import type { ChatMessage, ChatMessageMeta } from '../types/chat'

const TERMINAL_ASSISTANT_KINDS = new Set([
  'runtime_v2_assistant',
  'runtime_v2_company_assistant',
  'child_task_result',
  'child_task_result_retry',
  'company_role_result',
  'company_role_result_retry',
  'child_result',
  'top_level_reply',
])

export function resolveCanonicalTurnId(
  metadata: ChatMessageMeta | Record<string, unknown> | null | undefined,
): string {
  const source = (metadata ?? {}) as Record<string, unknown>
  for (const key of [
    'canonical_turn_id',
    'conversation_turn_id',
    'turn_id',
    'execution_turn_id',
  ]) {
    const value = String(source[key] ?? '').trim()
    if (value) return value
  }
  return ''
}

export function terminalAssistantTurnId(message: ChatMessage): string {
  if (message.sender === 'user') return ''
  const metadata = (message.metadata ?? {}) as Record<string, unknown>
  const kind = String(metadata.transcript_kind ?? metadata.kind ?? '').trim()
  if (!TERMINAL_ASSISTANT_KINDS.has(kind)) return ''
  if (kind === 'runtime_v2_company_assistant') {
    // Company tool-call iterations intentionally share this transcript kind
    // and canonical turn with the final answer. Treating every iteration as
    // terminal hides the live draft during detail refresh, then grows it back
    // on the next delta. Only the actual final surface may replace the draft.
    const uiMessageId = String(metadata.ui_message_id ?? '').trim()
    const isFinal = metadata.company_final_turn === true
      || !!String(metadata.result_delivery_id ?? '').trim()
      || uiMessageId.startsWith('runtime-v2-company-assistant-final:')
      // Snapshot translation retains final-vs-intermediate as visibility even
      // when reading records created before structured delivery ids existed.
      || String(metadata.detail_visibility ?? '').trim() === 'summary'
    if (!isFinal) return ''
  }
  return resolveCanonicalTurnId(metadata)
}
