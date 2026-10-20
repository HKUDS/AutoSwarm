import assert from 'node:assert/strict'
import { mapBackendSession } from './collabSync'

/* ──────────────────────────────────────────────────────────────────────
 * roleWorkItems deserialization tests
 *
 * Locks the snake_case → camelCase conversion at the collabSync boundary
 * so a backend rename or a missing field surfaces here, not as a silent
 * UI regression in WorkItemProgressCard. The schema this matches is
 * produced by ``snapshot_builder._build_role_work_items_for_session`` —
 * keep these two in sync.
 * ────────────────────────────────────────────────────────────────────── */

const session = mapBackendSession({
  task_id: 'task-1',
  channel_id: 'session:task-1',
  status: 'running',
  exec_mode: 'company',
  is_company_runtime: true,
  role_work_items: {
    engineer: {
      role_key: 'engineer',
      role_id: 'engineer',
      role_name: 'Engineer',
      runtime_status: 'tool_active',
      aggregated_status: 'active',
      work_items: [
        {
          work_item_id: 'wi-1',
          work_item_projection_id: 'proj-engineer-1',
          phase: 'running',
          kanban_column: 'in-progress',
          title: 'Implement summary',
          kind: 'execute',
          is_review_target: false,
          executor_role_id: 'engineer',
          executor_role_name: 'Engineer',
          reviewer_role_id: 'cto',
          created_at: 100.0,
          updated_at: 200.0,
          execution_turn_id: 'runtime-task-1',
          activity_sections: [
            {
              kind: 'execute',
              title: 'Execute',
              role_name: 'Engineer',
              runtime_task_id: 'runtime-task-1',
              entries: [
                { timestamp: 151.0, type: 'thinking', summary: 'plan' },
              ],
            },
            {
              kind: 'report',
              title: 'Report',
              role_name: 'Engineer',
              runtime_task_id: 'runtime-report-1',
              entries: [
                { timestamp: 175.0, type: 'tool_call', summary: 'write_report' },
              ],
            },
          ],
          progress_log: [
            { timestamp: 150.0, type: 'tool_call', summary: 'edit_file' },
          ],
        },
      ],
    },
    cto: {
      role_key: 'cto',
      role_id: 'cto',
      role_name: 'CTO',
      runtime_status: 'idle',
      aggregated_status: 'waiting',
      work_items: [
        {
          work_item_id: 'wi-2',
          work_item_projection_id: 'proj-engineer-1',
          phase: 'awaiting_manager_review',
          kanban_column: 'in-review',
          title: 'Implement summary',
          kind: 'execute',
          is_review_target: true,
          executor_role_id: 'engineer',
          reviewer_role_id: 'cto',
          created_at: 50.0,
          updated_at: 250.0,
          execution_turn_id: 'runtime-task-1',
          progress_log: [],
        },
      ],
    },
  },
  executor_role_work_items: {
    engineer: {
      role_key: 'engineer',
      role_id: 'engineer',
      role_name: 'Engineer',
      runtime_status: 'idle',
      aggregated_status: 'waiting',
      work_items: [
        {
          work_item_id: 'wi-2',
          work_item_projection_id: 'proj-engineer-1',
          phase: 'awaiting_manager_review',
          kanban_column: 'in-review',
          title: 'Implement summary',
          kind: 'execute',
          is_review_target: true,
          executor_role_id: 'engineer',
          executor_role_name: 'Engineer',
          reviewer_role_id: 'cto',
          created_at: 50.0,
          updated_at: 250.0,
          execution_turn_id: 'runtime-task-1',
          progress_log: [],
        },
      ],
    },
  },
})

const roleWorkItems = session.roleWorkItems
assert.ok(roleWorkItems, 'roleWorkItems should be parsed')
assert.deepEqual(Object.keys(roleWorkItems!).sort(), ['cto', 'engineer'])

const engineer = roleWorkItems!.engineer
assert.equal(engineer.aggregatedStatus, 'active')
assert.equal(engineer.runtimeStatus, 'tool_active')
assert.equal(engineer.workItems.length, 1)
assert.equal(engineer.workItems[0].phase, 'running')
assert.equal(engineer.workItems[0].kanbanColumn, 'in-progress')
assert.equal(engineer.workItems[0].executionTurnId, 'runtime-task-1')
assert.equal(engineer.workItems[0].progressLog.length, 1)
assert.equal(engineer.workItems[0].progressLog[0].summary, 'edit_file')
assert.equal(engineer.workItems[0].activitySections?.length, 2)
assert.equal(engineer.workItems[0].activitySections?.[0].roleName, 'Engineer')
assert.equal(engineer.workItems[0].activitySections?.[1].runtimeTaskId, 'runtime-report-1')
assert.equal(engineer.workItems[0].activitySections?.[1].entries[0].summary, 'write_report')

const cto = roleWorkItems!.cto
assert.equal(cto.aggregatedStatus, 'waiting')
assert.equal(cto.runtimeStatus, 'idle')
assert.equal(cto.workItems.length, 1)
assert.equal(cto.workItems[0].isReviewTarget, true)
assert.equal(cto.workItems[0].kanbanColumn, 'in-review')

const executorRoleWorkItems = session.executorRoleWorkItems
assert.ok(executorRoleWorkItems, 'executorRoleWorkItems should be parsed')
assert.deepEqual(Object.keys(executorRoleWorkItems!).sort(), ['engineer'])
assert.equal(executorRoleWorkItems!.engineer.workItems.length, 1)
assert.equal(executorRoleWorkItems!.engineer.workItems[0].isReviewTarget, true)
assert.equal(executorRoleWorkItems!.engineer.workItems[0].reviewerRoleId, 'cto')
assert.equal(executorRoleWorkItems!.engineer.aggregatedStatus, 'waiting')

// Sanity: an unknown phase falls back to ``queued`` (safest "not started"
// label) rather than throwing — a backend with a temporarily stale enum
// shouldn't crash the panel.
const sessionWithBadPhase = mapBackendSession({
  task_id: 'task-2',
  channel_id: 'session:task-2',
  status: 'running',
  role_work_items: {
    engineer: {
      role_key: 'engineer',
      role_id: 'engineer',
      role_name: 'Engineer',
      runtime_status: 'idle',
      aggregated_status: 'pending',
      work_items: [
        {
          work_item_id: 'wi-bad',
          work_item_projection_id: 'proj-x',
          phase: 'totally_made_up',
          kanban_column: 'todo',
          title: 'Mystery item',
          created_at: 10.0,
          updated_at: 10.0,
          progress_log: [],
        },
      ],
    },
  },
})
const fallbackRow = sessionWithBadPhase.roleWorkItems!.engineer.workItems[0]
assert.equal(fallbackRow.phase, 'queued')

// Empty / missing payload normalizes to ``undefined`` so the consumer can
// branch on ``.roleWorkItems != null`` without falsy-checking against {}.
const sessionWithoutPayload = mapBackendSession({
  task_id: 'task-3',
  channel_id: 'session:task-3',
  status: 'running',
})
assert.equal(sessionWithoutPayload.roleWorkItems, undefined)
assert.equal(sessionWithoutPayload.executorRoleWorkItems, undefined)

const sessionWithEmptyPayload = mapBackendSession({
  task_id: 'task-4',
  channel_id: 'session:task-4',
  status: 'running',
  role_work_items: {},
  executor_role_work_items: {},
})
assert.equal(sessionWithEmptyPayload.roleWorkItems, undefined)
assert.equal(sessionWithEmptyPayload.executorRoleWorkItems, undefined)

console.log('roleWorkItems deserialization checks passed')
