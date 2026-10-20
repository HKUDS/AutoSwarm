import type { ProgressEntry } from '../types/kanban'

function compact(value: unknown): string {
  return String(value ?? '')
    .trim()
    .replace(/\s+/g, ' ')
    .slice(0, 96)
}

export function progressEntryKey(entry: ProgressEntry): string {
  const stableId = entry.itemId || entry.streamId || entry.toolCallId || entry.permissionGroupKey
  if (stableId) {
    return `${entry.type}:${compact(entry.turnId)}:${compact(stableId)}`
  }

  if (entry.type === 'thinking' || entry.type === 'assistant') {
    return `${entry.type}:${compact(entry.turnId) || compact(entry.executionMode) || 'stream'}:${
      Number.isFinite(entry.timestamp) ? entry.timestamp : ''
    }`
  }

  if (entry.type === 'tool_call') {
    return `tool:${compact(entry.turnId) || 'turnless'}:${compact(entry.summary) || 'tool'}:${
      Number.isFinite(entry.timestamp) ? entry.timestamp : ''
    }`
  }

  if (entry.turnId && typeof entry.seq === 'number') {
    return `${entry.type}:${compact(entry.turnId)}:seq:${entry.seq}`
  }

  const fallbackParts: Array<string | number> = [
    entry.type,
    compact(entry.turnId),
  ]
  if (typeof entry.seq === 'number' && Number.isFinite(entry.seq)) {
    fallbackParts.push(`seq-${entry.seq}`)
  }
  fallbackParts.push(
    Number.isFinite(entry.timestamp) ? entry.timestamp : '',
    compact(entry.summary),
    compact(entry.detail),
  )
  return fallbackParts.join(':')
}
