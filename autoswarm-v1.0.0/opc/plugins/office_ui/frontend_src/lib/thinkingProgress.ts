import type { ChatMessage } from '../types/chat'
import type { ProgressEntry } from '../types/kanban'
import { resolveCanonicalTurnId } from './turnIdentity'

/** Backfill thinking by iteration, matching the IDs used by live deltas. */
export function synthesizeThinkingEntries(messages: ChatMessage[], progress: ProgressEntry[]): ProgressEntry[] {
  const live = progress.filter(entry => entry.type === 'thinking')
  const streams = new Set(live.map(entry => entry.streamId || entry.itemId).filter(Boolean))
  const turns = new Set(live.map(entry => entry.turnId).filter(Boolean))
  const entries = new Map<string, ProgressEntry>()
  for (const message of messages) {
    const thinking = String(message.metadata?.runtime_thinking ?? '').trim()
    if (!thinking) continue
    const turnId = resolveCanonicalTurnId(message.metadata)
    const persistedStream = String(message.metadata?.runtime_thinking_stream_id ?? '').trim()
    // Old rows contain cumulative thinking and have no per-iteration marker.
    // Keep their latest snapshot, and let live turn progress take precedence.
    if (!persistedStream && turnId && turns.has(turnId)) continue
    const streamId = persistedStream || (turnId ? `${turnId}:thinking` : `thinking:${message.id}`)
    if (streams.has(streamId)) continue
    entries.set(streamId, {
      type: 'thinking', summary: 'Thinking', detail: thinking,
      timestamp: Math.max(0, message.timestamp - 1),
      turnId: turnId || undefined, itemId: streamId, streamId,
      executionMode: String(message.metadata?.execution_mode ?? '').trim() || undefined,
    })
  }
  return [...entries.values()]
}
