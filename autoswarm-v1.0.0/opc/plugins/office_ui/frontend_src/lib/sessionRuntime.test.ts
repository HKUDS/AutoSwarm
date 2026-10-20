import assert from 'node:assert/strict'
import type { Session } from '../types/kanban'
import {
  companyRuntimeControlPatchForBoardStatus,
  isCompanyRuntimeSession,
} from './sessionRuntime'

function makeSession(overrides: Partial<Session>): Partial<Session> {
  return {
    taskId: 'task-a',
    status: 'idle',
    execMode: 'task',
    ...overrides,
  }
}

assert.equal(
  isCompanyRuntimeSession(makeSession({ execMode: 'org' })),
  true,
  'org sessions are company runtime sessions',
)
assert.equal(
  isCompanyRuntimeSession(makeSession({ execMode: 'company' })),
  true,
  'company sessions are company runtime sessions',
)
assert.equal(
  isCompanyRuntimeSession(makeSession({ parentSessionId: 'root-session' })),
  true,
  'child runtime sessions are company runtime sessions',
)
assert.equal(
  isCompanyRuntimeSession(makeSession({ execMode: 'task', companyProfile: undefined })),
  false,
  'plain task sessions are not company runtime sessions',
)
assert.equal(
  isCompanyRuntimeSession(makeSession({ execMode: 'task', companyProfile: 'corporate' })),
  false,
  'explicit task sessions with legacy default companyProfile stay plain task sessions',
)

assert.deepEqual(
  companyRuntimeControlPatchForBoardStatus(makeSession({ execMode: 'org' }), 'running'),
  { runtimeControlState: 'running', canStop: true, canResume: false },
  'company running board events must show Stop immediately',
)
assert.deepEqual(
  companyRuntimeControlPatchForBoardStatus(
    makeSession({ execMode: 'org', runtimeControlState: 'running', canStop: true }),
    'done',
  ),
  { runtimeControlState: 'idle', canStop: false, canResume: false },
  'company terminal board events must clear Stop after completion',
)
assert.deepEqual(
  companyRuntimeControlPatchForBoardStatus(
    makeSession({ execMode: 'org', runtimeControlState: 'suspended', canResume: true }),
    'cancelled',
  ),
  {},
  'terminal child events must not erase an explicit suspended/continue state',
)
assert.deepEqual(
  companyRuntimeControlPatchForBoardStatus(makeSession({ execMode: 'task' }), 'running'),
  {},
  'plain task board events must not opt into company runtime control',
)

console.log('sessionRuntime.test.ts: OK (company board status drives Stop without breaking Continue)')
