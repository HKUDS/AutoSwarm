import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { TaskHeaderBar } from './TaskHeaderBar'
import type { Session } from '../types/kanban'
import type { AgentInfo } from '../types/visual'

const now = Date.now()

const nexusAgent: AgentInfo = {
  agent_id: 'nexus',
  name: 'NEXUS Executive Brief',
  description: 'Executive briefing agent',
  specialties: [],
  status: 'idle',
  appearance: { palette: 0, hue_shift: 0, seat_zone: 'north' },
}

function makeSession(overrides: Partial<Session>): Session {
  return {
    projectId: 'project-a',
    taskId: 'task-a',
    channelId: 'channel-a',
    execMode: 'task',
    title: 'NEXUS Executive Brief',
    status: 'running',
    columnId: 'in-progress',
    assigneeIds: ['nexus'],
    priority: null,
    tags: [],
    progressLog: [],
    createdAt: now - 60_000,
    updatedAt: now,
    messageCount: 1,
    ...overrides,
  }
}

const companyMarkup = renderToStaticMarkup(
  React.createElement(TaskHeaderBar, {
    session: makeSession({
      execMode: 'org',
      isCompanyRuntime: true,
      workItemRoleName: 'Chief Analyst',
      employeeAssignment: { name: 'NEXUS Executive Brief', employeeId: 'employee-nexus' },
      selectedExecutionAgent: 'codex',
      displayTool: 'opc-collab delegate_work',
      currentTool: undefined,
    }),
    agents: [nexusAgent],
    onTitleChange: () => undefined,
  }),
)

assert.match(companyMarkup, /Chief Analyst/, 'company header should keep the role pill')
assert.match(companyMarkup, /NEXUS Executive Brief/, 'company header should keep the employee label')
assert.match(companyMarkup, /Codex/, 'company header should keep the execution agent label')
assert.match(companyMarkup, /opc-collab delegate_work/, 'company header should show stable displayTool while still running')
assert.doesNotMatch(
  companyMarkup,
  /task-header-avatar/,
  'company header must not render a duplicate assignee initial avatar',
)

const taskMarkup = renderToStaticMarkup(
  React.createElement(TaskHeaderBar, {
    session: makeSession({ execMode: 'task' }),
    agents: [nexusAgent],
    onTitleChange: () => undefined,
  }),
)

assert.match(
  taskMarkup,
  /task-header-avatar/,
  'plain task headers should keep assignee initial avatars',
)

console.log('TaskHeaderBar.test.tsx: OK (company header hides duplicate avatar and keeps stable tool label)')
