import assert from 'node:assert/strict'
import test from 'node:test'
import { synthesizeThinkingEntries } from './thinkingProgress'
import type { ChatMessage } from '../types/chat'
import type { ProgressEntry } from '../types/kanban'

function message(iteration: number, legacy = false): ChatMessage {
  return {
    id: `m${iteration}`, timestamp: iteration * 100,
    metadata: {
      canonical_turn_id: 'turn', runtime_thinking: `thinking ${iteration}`,
      ...(legacy ? {} : { runtime_thinking_stream_id: `turn:iter:${iteration}:thinking` }),
    },
  } as ChatMessage
}

test('history preserves thinking from every iteration of the same turn', () => {
  const result = synthesizeThinkingEntries([message(1), message(2)], [])
  assert.deepEqual(result.map(entry => entry.detail), ['thinking 1', 'thinking 2'])
  assert.deepEqual(result.map(entry => entry.streamId), ['turn:iter:1:thinking', 'turn:iter:2:thinking'])
})

test('live thinking suppresses only its own iteration', () => {
  const live = [{ type: 'thinking', turnId: 'turn', streamId: 'turn:iter:1:thinking' }] as ProgressEntry[]
  const result = synthesizeThinkingEntries([message(1), message(2)], live)
  assert.equal(result.length, 1)
  assert.equal(result[0].detail, 'thinking 2')
})

test('legacy cumulative snapshots collapse to the latest row', () => {
  const result = synthesizeThinkingEntries([message(1, true), message(2, true)], [])
  assert.equal(result.length, 1)
  assert.equal(result[0].detail, 'thinking 2')
  assert.deepEqual(synthesizeThinkingEntries([message(1, true)], [
    { type: 'thinking', turnId: 'turn', streamId: 'turn:iter:1:thinking' } as ProgressEntry,
  ]), [])
})
