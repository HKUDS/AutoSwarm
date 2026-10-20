import assert from 'node:assert/strict'
import { mapBackendSession, mapBackendTask } from './collabSync'
import { getExecutionTurnId, getLinkedRuntimeTaskId, getWorkItemCardId } from './workItemRuntimeIds'

const workItemCard = mapBackendTask({
  task_id: 'wi-1',
  work_item_id: 'wi-1',
  title: 'Prepare launch plan',
  kanban_column: 'in-progress',
  runtime_task_id: 'runtime-task-1',
  execution_turn_id: 'runtime-task-1',
})

assert.equal(getWorkItemCardId(workItemCard), 'wi-1')
assert.equal(getLinkedRuntimeTaskId(workItemCard), 'runtime-task-1')
assert.equal(workItemCard.runtimeTaskId, 'runtime-task-1')
assert.equal(workItemCard.executionTurnId, 'runtime-task-1')

const canonicalCard = mapBackendTask({
  work_item_id: 'wi-2',
  title: 'Review launch plan',
  kanban_column: 'in-review',
  runtime_task_id: 'runtime-task-2',
  execution_turn_id: 'runtime-task-2',
})

assert.equal(getWorkItemCardId(canonicalCard), 'wi-2')
assert.equal(getLinkedRuntimeTaskId(canonicalCard), 'runtime-task-2')

const plainTaskCard = mapBackendTask({
  task_id: 'plain-task-1',
  title: 'Plain task',
  kanban_column: 'todo',
  runtime_task_id: 'plain-task-1',
})

assert.equal(plainTaskCard.runtimeTaskId, 'plain-task-1')
assert.equal(getLinkedRuntimeTaskId(plainTaskCard), '')

const runtimeSession = mapBackendSession({
  task_id: 'legacy-task-id',
  runtime_task_id: 'runtime-task-3',
  execution_turn_id: 'turn-3',
  channel_id: 'session:runtime-task-3',
  title: 'CTO Execution Turn',
})

assert.equal(runtimeSession.taskId, 'legacy-task-id')
assert.equal(runtimeSession.runtimeTaskId, 'runtime-task-3')
assert.equal(runtimeSession.executionTurnId, 'turn-3')
assert.equal(getExecutionTurnId(runtimeSession), 'turn-3')

const taskSessionWithRuntimeParentMetadata = mapBackendSession({
  project_id: 'default',
  task_id: 'task-mode-session',
  session_id: 'task-session-id',
  parent_session_id: 'task-session-id',
  channel_id: 'session:task-mode-session',
  title: 'Task mode session detail',
  exec_mode: 'task',
})

assert.equal(taskSessionWithRuntimeParentMetadata.execMode, 'task')
assert.equal(taskSessionWithRuntimeParentMetadata.mode, 'primary')
assert.equal(taskSessionWithRuntimeParentMetadata.parentSessionId, undefined)

console.log('workItemRuntimeIds alias checks passed')
