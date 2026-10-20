import type { ProgressEntry, WorkItemProgressEntry } from '../types/kanban'

const STREAM_MERGE_WINDOW_MS = 4000

function clampEntries<T>(entries: T[], maxEntries: number): T[] {
  return entries.length > maxEntries ? entries.slice(-maxEntries) : entries
}

function mergeText(left: string, right: string, kind: 'thinking' | 'tool_call'): string {
  if (!left) return right
  if (!right) return left
  if (left === right) return left
  if (right.startsWith(left)) return right
  if (left.startsWith(right)) return left
  if (left.endsWith(right)) return left
  if (right.endsWith(left)) return right
  const maxOverlap = Math.min(left.length, right.length)
  for (let overlap = maxOverlap; overlap > 0; overlap -= 1) {
    if (left.slice(-overlap) === right.slice(0, overlap)) {
      return `${left}${right.slice(overlap)}`
    }
  }
  if (kind === 'tool_call' && /[}\]"]$/.test(left) && !/^\s/.test(right)) {
    return `${left}\n${right}`
  }
  return `${left}${right}`
}

function summarizeThinking(detail: string, fallback: string): string {
  const text = detail.trim().replace(/\s+/g, ' ')
  if (!text) return fallback || 'Thinking'
  return text.length > 120 ? `${text.slice(0, 120).trimEnd()}...` : text
}

function normalizeProgressEntry(entry: ProgressEntry): ProgressEntry {
  return {
    timestamp: Number.isFinite(entry.timestamp) ? entry.timestamp : Date.now(),
    type: entry.type,
    summary: typeof entry.summary === 'string' ? entry.summary : '',
    detail: typeof entry.detail === 'string' && entry.detail ? entry.detail : undefined,
    turnId: typeof entry.turnId === 'string' && entry.turnId ? entry.turnId : undefined,
    itemId: typeof entry.itemId === 'string' && entry.itemId ? entry.itemId : undefined,
    streamId: typeof entry.streamId === 'string' && entry.streamId ? entry.streamId : undefined,
    toolCallId: typeof entry.toolCallId === 'string' && entry.toolCallId ? entry.toolCallId : undefined,
    permissionGroupKey: typeof entry.permissionGroupKey === 'string' && entry.permissionGroupKey ? entry.permissionGroupKey : undefined,
    seq: typeof entry.seq === 'number' && Number.isFinite(entry.seq) ? entry.seq : undefined,
    executionMode: typeof entry.executionMode === 'string' && entry.executionMode ? entry.executionMode : undefined,
  }
}

function streamKey(entry: ProgressEntry): string {
  const itemKey = entry.itemId || entry.streamId
  if (!itemKey) {
    if (entry.toolCallId && (entry.type === 'tool_call' || entry.type === 'autonomy')) {
      return `${entry.type}:${entry.turnId ?? ''}:${entry.toolCallId}`
    }
    if (entry.permissionGroupKey && entry.type === 'autonomy') {
      return `${entry.type}:${entry.turnId ?? ''}:${entry.permissionGroupKey}`
    }
    return ''
  }
  return `${entry.type}:${entry.turnId ?? ''}:${itemKey}`
}

function canMergeProgress(left: ProgressEntry, right: ProgressEntry): boolean {
  const leftKey = streamKey(left)
  const rightKey = streamKey(right)
  if (leftKey && rightKey) return leftKey === rightKey
  if (right.timestamp - left.timestamp > STREAM_MERGE_WINDOW_MS) return false
  if (left.type !== right.type) return false
  if (left.type === 'thinking' || left.type === 'assistant') return true
  if (left.type === 'tool_call') return left.summary === right.summary
  return false
}

function isDuplicateProgress(left: ProgressEntry, right: ProgressEntry): boolean {
  return (
    right.timestamp - left.timestamp <= STREAM_MERGE_WINDOW_MS
    && left.type === right.type
    && left.summary === right.summary
    && (left.detail ?? '') === (right.detail ?? '')
  )
}

function mergeProgress(left: ProgressEntry, right: ProgressEntry): ProgressEntry {
  if (left.type === 'thinking' || left.type === 'assistant') {
    // Merge detail text only: summary is a label/preview ("Thinking",
    // truncated excerpt), so falling back to it would splice label text
    // into the middle of the merged stream. Assistant reply streams merge
    // the same way as thinking streams.
    const detail = mergeText(left.detail ?? '', right.detail ?? '', 'thinking')
    return {
      // A stream occupies the timeline slot where it began. Deltas update the
      // row in place instead of repeatedly re-sorting it around tool events.
      timestamp: left.timestamp,
      type: left.type,
      summary: summarizeThinking(detail, right.summary || left.summary),
      detail: detail || undefined,
      turnId: right.turnId ?? left.turnId,
      itemId: right.itemId ?? left.itemId,
      streamId: right.streamId ?? left.streamId,
      toolCallId: right.toolCallId ?? left.toolCallId,
      permissionGroupKey: right.permissionGroupKey ?? left.permissionGroupKey,
      seq: right.seq ?? left.seq,
      executionMode: right.executionMode ?? left.executionMode,
    }
  }

  if (left.type === 'tool_call') {
    const mergedDetail = mergeText(left.detail ?? '', right.detail ?? '', 'tool_call')
    return {
      timestamp: left.timestamp,
      type: 'tool_call',
      summary: right.summary || left.summary,
      detail: mergedDetail || undefined,
      turnId: right.turnId ?? left.turnId,
      itemId: right.itemId ?? left.itemId,
      streamId: right.streamId ?? left.streamId,
      toolCallId: right.toolCallId ?? left.toolCallId,
      permissionGroupKey: right.permissionGroupKey ?? left.permissionGroupKey,
      seq: right.seq ?? left.seq,
      executionMode: right.executionMode ?? left.executionMode,
    }
  }

  return right
}

export function appendProgressEntry(
  log: ProgressEntry[],
  entry: ProgressEntry,
  maxEntries = 100,
): ProgressEntry[] {
  const normalized = normalizeProgressEntry(entry)
  const normalizedKey = streamKey(normalized)
  const targetIndex = normalizedKey
    ? [...log].reverse().findIndex(existing => streamKey(existing) === normalizedKey)
    : -1
  const actualIndex = targetIndex >= 0 ? log.length - 1 - targetIndex : log.length - 1
  const last = log[actualIndex]
  if (!last) return [normalized]
  // The seq guard only applies within the SAME stream: when the key was not
  // found, `last` is an unrelated entry and a fresh stream legitimately
  // restarts at seq 1 (per-iteration thinking/assistant streams).
  if (
    normalizedKey
    && targetIndex >= 0
    && typeof last.seq === 'number'
    && typeof normalized.seq === 'number'
    && normalized.seq <= last.seq
  ) {
    return log
  }
  if (isDuplicateProgress(last, normalized)) {
    return clampEntries([
      ...log.slice(0, actualIndex),
      last,
      ...log.slice(actualIndex + 1),
    ], maxEntries)
  }
  if (canMergeProgress(last, normalized)) {
    return clampEntries([
      ...log.slice(0, actualIndex),
      mergeProgress(last, normalized),
      ...log.slice(actualIndex + 1),
    ], maxEntries)
  }
  return clampEntries([...log, normalized], maxEntries)
}

export function normalizeProgressLog(log: ProgressEntry[], maxEntries = 100): ProgressEntry[] {
  return (Array.isArray(log) ? log : []).reduce<ProgressEntry[]>(
    (acc, entry) => appendProgressEntry(acc, entry, maxEntries),
    [],
  )
}

function normalizeWorkItemEntry(entry: WorkItemProgressEntry): WorkItemProgressEntry {
  return {
    timestamp: Number.isFinite(entry.timestamp) ? entry.timestamp : Date.now(),
    type: entry.type,
    workItemProjectionId: typeof entry.workItemProjectionId === 'string' && entry.workItemProjectionId ? entry.workItemProjectionId : undefined,
    workItemTurnType: typeof entry.workItemTurnType === 'string' && entry.workItemTurnType ? entry.workItemTurnType : undefined,
    workItemProjectionTitle: typeof entry.workItemProjectionTitle === 'string' && entry.workItemProjectionTitle ? entry.workItemProjectionTitle : undefined,
    runtimeTaskId: typeof entry.runtimeTaskId === 'string' && entry.runtimeTaskId ? entry.runtimeTaskId : undefined,
    executionTurnId: typeof entry.executionTurnId === 'string' && entry.executionTurnId ? entry.executionTurnId : undefined,
    roleName: typeof entry.roleName === 'string' && entry.roleName ? entry.roleName : undefined,
    detail: typeof entry.detail === 'string' && entry.detail ? entry.detail : undefined,
  }
}

function sameWorkItemScope(left: WorkItemProgressEntry, right: WorkItemProgressEntry): boolean {
  return (
    left.type === right.type
    && (left.workItemProjectionId ?? '') === (right.workItemProjectionId ?? '')
    && (left.workItemTurnType ?? '') === (right.workItemTurnType ?? '')
    && (left.workItemProjectionTitle ?? '') === (right.workItemProjectionTitle ?? '')
    && (left.executionTurnId ?? left.runtimeTaskId ?? '') === (right.executionTurnId ?? right.runtimeTaskId ?? '')
    && (left.roleName ?? '') === (right.roleName ?? '')
  )
}

function canMergeWorkItem(left: WorkItemProgressEntry, right: WorkItemProgressEntry): boolean {
  return (
    right.timestamp - left.timestamp <= STREAM_MERGE_WINDOW_MS
    && sameWorkItemScope(left, right)
    && (left.type === 'thinking' || left.type === 'tool_call')
  )
}

function isDuplicateWorkItem(left: WorkItemProgressEntry, right: WorkItemProgressEntry): boolean {
  return sameWorkItemScope(left, right) && (left.detail ?? '') === (right.detail ?? '') && right.timestamp - left.timestamp <= STREAM_MERGE_WINDOW_MS
}

function mergeWorkItem(left: WorkItemProgressEntry, right: WorkItemProgressEntry): WorkItemProgressEntry {
  const kind = right.type === 'tool_call' ? 'tool_call' : 'thinking'
  return {
    ...left,
    ...right,
    timestamp: right.timestamp,
    detail: mergeText(left.detail ?? '', right.detail ?? '', kind) || undefined,
  }
}

export function appendWorkItemProgressEntry(
  log: WorkItemProgressEntry[],
  entry: WorkItemProgressEntry,
  maxEntries = 100,
): WorkItemProgressEntry[] {
  const normalized = normalizeWorkItemEntry(entry)
  const last = log[log.length - 1]
  if (!last) return [normalized]
  if (isDuplicateWorkItem(last, normalized)) {
    return clampEntries([...log.slice(0, -1), { ...last, timestamp: normalized.timestamp }], maxEntries)
  }
  if (canMergeWorkItem(last, normalized)) {
    return clampEntries([...log.slice(0, -1), mergeWorkItem(last, normalized)], maxEntries)
  }
  return clampEntries([...log, normalized], maxEntries)
}

export function normalizeWorkItemLog(log: WorkItemProgressEntry[], maxEntries = 100): WorkItemProgressEntry[] {
  return (Array.isArray(log) ? log : []).reduce<WorkItemProgressEntry[]>(
    (acc, entry) => appendWorkItemProgressEntry(acc, entry, maxEntries),
    [],
  )
}
