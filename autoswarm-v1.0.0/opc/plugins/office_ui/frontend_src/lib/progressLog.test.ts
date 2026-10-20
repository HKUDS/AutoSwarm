import assert from 'node:assert/strict'
import type { ProgressEntry } from '../types/kanban'
import { mapBackendSession } from './collabSync'
import { progressEntryKey } from './progressEntryKey'
import { appendProgressEntry, normalizeProgressLog } from './progressLog'

let log = appendProgressEntry([], {
  timestamp: 1,
  type: 'thinking',
  summary: 'Thinking',
  detail: '我先',
  turnId: 'rt-1:1',
  itemId: 'rt-1:1:thinking',
  seq: 1,
})

for (const [seq, detail] of [
  [2, '先联网'],
  [3, '联网抓'],
  [4, '抓取'],
] as const) {
  log = appendProgressEntry(log, {
    timestamp: seq,
    type: 'thinking',
    summary: 'Thinking',
    detail,
    turnId: 'rt-1:1',
    itemId: 'rt-1:1:thinking',
    seq,
  })
}

assert.equal(log.length, 1)
assert.equal(log[0]?.summary, '我先联网抓取')
assert.equal(log[0]?.detail, '我先联网抓取')

// Token-sized streaming fragments keep their whitespace when merged, and
// entries without detail never splice their summary label into the text.
let spacedLog = appendProgressEntry([], {
  timestamp: 100,
  type: 'thinking',
  summary: 'The user',
  detail: 'The user',
  turnId: 'rt-2:1',
  itemId: 'rt-2:1:thinking',
  seq: 1,
})
spacedLog = appendProgressEntry(spacedLog, {
  timestamp: 101,
  type: 'thinking',
  summary: 'wants to',
  detail: ' wants to',
  turnId: 'rt-2:1',
  itemId: 'rt-2:1:thinking',
  seq: 2,
})
spacedLog = appendProgressEntry(spacedLog, {
  timestamp: 102,
  type: 'thinking',
  summary: 'Thinking',
  turnId: 'rt-2:1',
  itemId: 'rt-2:1:thinking',
  seq: 3,
})
assert.equal(spacedLog.length, 1)
assert.equal(spacedLog[0]?.detail, 'The user wants to')
assert.equal(spacedLog[0]?.summary, 'The user wants to')

const unchanged = appendProgressEntry(log, {
  timestamp: 5,
  type: 'thinking',
  summary: 'Thinking',
  detail: '重复',
  turnId: 'rt-1:1',
  itemId: 'rt-1:1:thinking',
  seq: 4,
})

assert.equal(unchanged[0]?.detail, '我先联网抓取')

let toolLog = appendProgressEntry([], {
  timestamp: 10,
  type: 'tool_call',
  summary: 'web_search',
  detail: '{"query":"weather"}',
  turnId: 'rt-1:2',
  toolCallId: 'call-1',
})

toolLog = appendProgressEntry(toolLog, {
  timestamp: 11,
  type: 'tool_call',
  summary: 'web_search',
  detail: 'completed',
  turnId: 'rt-1:2',
  toolCallId: 'call-1',
})

assert.equal(toolLog.length, 1)
assert.equal(toolLog[0]?.detail, '{"query":"weather"}\ncompleted')

let permissionLog = appendProgressEntry([], {
  timestamp: 20,
  type: 'autonomy',
  summary: 'shell_exec: ask',
  turnId: 'rt-1:3',
  permissionGroupKey: 'tool:shell_exec/python:domain:example.com',
})

permissionLog = appendProgressEntry(permissionLog, {
  timestamp: 21,
  type: 'autonomy',
  summary: 'shell_exec: allow',
  turnId: 'rt-1:3',
  permissionGroupKey: 'tool:shell_exec/python:domain:example.com',
})

assert.equal(permissionLog.length, 1)
assert.equal(permissionLog[0]?.summary, 'shell_exec: allow')

// Native company assistant replies stream like thinking: same-stream deltas
// accumulate into one entry instead of replacing each other, and separate
// iterations (distinct item_id) stay separate entries.
let assistantLog = appendProgressEntry([], {
  timestamp: 30,
  type: 'assistant',
  summary: '文件已成功写入',
  detail: '文件已成功写入',
  turnId: 'rt-1:4',
  itemId: 'rt-1:4:iter:2:assistant',
  seq: 1,
})
assistantLog = appendProgressEntry(assistantLog, {
  timestamp: 31,
  type: 'assistant',
  summary: '(278 行)。',
  detail: '(278 行)。',
  turnId: 'rt-1:4',
  itemId: 'rt-1:4:iter:2:assistant',
  seq: 2,
})
assistantLog = appendProgressEntry(assistantLog, {
  timestamp: 32,
  type: 'assistant',
  summary: '采集完成报告',
  detail: '采集完成报告',
  turnId: 'rt-1:4',
  itemId: 'rt-1:4:iter:3:assistant',
  seq: 1,
})
assert.equal(assistantLog.length, 2)
assert.equal(assistantLog[0]?.detail, '文件已成功写入(278 行)。')
assert.equal(assistantLog[0]?.summary, '文件已成功写入(278 行)。')
assert.equal(assistantLog[1]?.detail, '采集完成报告')

// A live client receives these as individual deltas. A reconnect receives the
// same rows as a full snake_case snapshot. Both paths must produce identical
// row identities: otherwise React remounts progress rows and browser anchoring
// sees a false remove/insert pair during every full sync.
const snapshotSeconds = 1_700_000_000
const snapshotRows = [
  {
    timestamp: snapshotSeconds,
    type: 'status_change',
    summary: 'Running',
    detail: 'phase=running',
  },
  {
    timestamp: snapshotSeconds + 0.1,
    type: 'status_change',
    summary: 'Running',
    detail: 'phase=running',
  },
  {
    timestamp: snapshotSeconds + 1,
    type: 'thinking',
    summary: 'Thinking',
    detail: 'Need ',
  },
  {
    timestamp: snapshotSeconds + 1.1,
    type: 'thinking',
    summary: 'Thinking',
    detail: 'context',
  },
  {
    timestamp: snapshotSeconds + 2,
    type: 'assistant',
    summary: 'Answer',
    detail: 'Answer ',
  },
  {
    timestamp: snapshotSeconds + 2.1,
    type: 'assistant',
    summary: 'ready',
    detail: 'ready',
  },
  {
    timestamp: snapshotSeconds + 3,
    type: 'tool_call',
    summary: 'file_read',
    detail: '{"path":',
  },
  {
    timestamp: snapshotSeconds + 3.1,
    type: 'tool_call',
    summary: 'file_read',
    detail: '"README.md"}',
  },
] as const

const liveDeltas: ProgressEntry[] = snapshotRows.map(entry => ({
  timestamp: entry.timestamp * 1000,
  type: entry.type,
  summary: entry.summary,
  detail: entry.detail,
}))
const liveSnapshot = liveDeltas.reduce<ProgressEntry[]>(
  (entries, entry) => appendProgressEntry(entries, entry),
  [],
)
const normalizedSnapshot = normalizeProgressLog(liveDeltas)
const mappedSnapshot = mapBackendSession({
  task_id: 'progress-snapshot',
  channel_id: 'session:progress-snapshot',
  progress_log: snapshotRows,
}).progressLog

assert.equal(liveSnapshot.length, 4)
assert.deepEqual(
  liveSnapshot.map(entry => entry.type),
  ['status_change', 'thinking', 'assistant', 'tool_call'],
)
assert.deepEqual(
  normalizedSnapshot.map(entry => ({ key: progressEntryKey(entry), timestamp: entry.timestamp })),
  liveSnapshot.map(entry => ({ key: progressEntryKey(entry), timestamp: entry.timestamp })),
)
assert.deepEqual(
  mappedSnapshot.map(entry => ({ key: progressEntryKey(entry), timestamp: entry.timestamp })),
  liveSnapshot.map(entry => ({ key: progressEntryKey(entry), timestamp: entry.timestamp })),
)
assert.deepEqual(
  liveSnapshot.map(entry => entry.timestamp),
  [
    snapshotSeconds * 1000,
    (snapshotSeconds + 1) * 1000,
    (snapshotSeconds + 2) * 1000,
    (snapshotSeconds + 3) * 1000,
  ],
)
assert.deepEqual(
  liveSnapshot.map(progressEntryKey),
  [
    'status_change::1700000000000:Running:phase=running',
    'thinking:stream:1700000001000',
    'assistant:stream:1700000002000',
    'tool:turnless:file_read:1700000003000',
  ],
)
assert.ok(liveSnapshot.every(entry => (
  !entry.itemId
  && !entry.streamId
  && !entry.toolCallId
  && !entry.permissionGroupKey
)))

// Persisted snake_case identifiers must survive the full-sync bridge. These
// identifiers take precedence over mutable summaries and timestamps when the
// UI derives a row key.
const mappedStableIds = mapBackendSession({
  task_id: 'progress-stable-ids',
  channel_id: 'session:progress-stable-ids',
  progress_log: [
    {
      timestamp: snapshotSeconds + 10,
      type: 'tool_call',
      summary: 'shell_exec',
      tool_call_id: 'call-42',
    },
    {
      timestamp: snapshotSeconds + 11,
      type: 'autonomy',
      summary: 'shell_exec: ask',
      permission_group_key: 'tool:shell_exec/python:domain:example.com',
    },
  ],
}).progressLog

assert.equal(mappedStableIds[0]?.toolCallId, 'call-42')
assert.equal(mappedStableIds[1]?.permissionGroupKey, 'tool:shell_exec/python:domain:example.com')
assert.equal(progressEntryKey(mappedStableIds[0]!), 'tool_call::call-42')
assert.equal(
  progressEntryKey(mappedStableIds[1]!),
  'autonomy::tool:shell_exec/python:domain:example.com',
)
