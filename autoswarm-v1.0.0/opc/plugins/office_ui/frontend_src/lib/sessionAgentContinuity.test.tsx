import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { mapBackendSession } from './collabSync'
import { sessionReducer } from '../stores/SessionStore'
import { composerAgentForSession } from './sessionIdentity'
import { MessageComposer } from '../chat/MessageComposer'
import type { Session, TaskPreferredAgent } from '../types/kanban'

const created = () => mapBackendSession({
  project_id: 'project02', task_id: 'root', session_id: 'chat', channel_id: 'session:root',
  mode: 'primary', exec_mode: 'company', company_profile: 'corporate',
  company_team_strategy: 'adaptive', preferred_agent: 'codex', recruitment_agent: 'codex',
  selected_execution_agent: 'native', status: 'idle',
})

test('a partial session detail refresh cannot erase the selected Codex agent', () => {
  const detail = mapBackendSession({
    project_id: 'project02', task_id: 'root', session_id: 'chat', channel_id: 'session:root',
    mode: 'primary', exec_mode: 'company', company_profile: 'corporate', status: 'idle',
    message_count: 1,
  })
  const [session] = sessionReducer([created()], { type: 'UPDATE', taskId: 'root', partial: detail })
  assert.equal(session.preferredAgent, 'codex')
  assert.equal(session.recruitmentAgent, 'codex')
  assert.equal(session.companyTeamStrategy, 'adaptive')
  const markup = renderToStaticMarkup(<MessageComposer execMode="company" lockedMode
    taskPreferredAgent={composerAgentForSession(session, 'native')} onTaskAgentChange={() => undefined}
    onSend={() => undefined} />)
  assert.match(markup, /Agent locked to Codex/)
  assert.doesNotMatch(markup, /Agent locked to AutoSwarm Native/)
})

test('a primary chat keeps its chosen agent regardless of conflicting role telemetry', () => {
  for (const selectedExecutionAgent of ['native', 'claude_code', 'opencode'] as const) {
    assert.equal(composerAgentForSession({ ...created(), selectedExecutionAgent }, 'native'), 'codex')
  }
  assert.equal(composerAgentForSession({ ...created(), preferredAgent: 'native', selectedExecutionAgent: 'codex' }, 'codex'), 'native')
  assert.equal(composerAgentForSession({ ...created(), mode: 'child', parentSessionId: 'parent', selectedExecutionAgent: 'native' }, 'codex'), 'native')
})

test('all agent choices survive repeated detail and snapshot updates in Task and Company modes', () => {
  const agents: TaskPreferredAgent[] = ['native', 'codex', 'claude_code', 'cursor', 'opencode', 'jiuwen', 'jiuwenswarm']
  for (const preferredAgent of agents) {
    for (const execMode of ['task', 'company', 'org']) {
      let sessions: Session[] = [{ ...created(), execMode, preferredAgent, selectedExecutionAgent: preferredAgent }]
      for (let round = 0; round < 3; round++) {
        const incoming = mapBackendSession({
          project_id: 'project02', task_id: 'root', mode: 'primary', exec_mode: execMode,
          company_profile: execMode === 'org' ? 'custom' : 'corporate', org_id: execMode === 'org' ? 'team' : '',
          status: 'idle', preferred_agent: preferredAgent, selected_execution_agent: preferredAgent,
        })
        sessions = sessionReducer(sessions, { type: 'SET', sessions: [incoming] })
        sessions = sessionReducer(sessions, { type: 'UPDATE', taskId: 'root', partial: { preferredAgent: undefined, selectedExecutionAgent: undefined } })
        assert.equal(composerAgentForSession(sessions[0], 'native'), preferredAgent)
      }
    }
  }
})

test('an explicit Native selection in a configurable chat is accepted', () => {
  for (const type of ['SET', 'ADD', 'UPDATE'] as const) {
    const native = { ...created(), preferredAgent: 'native' as const, selectedExecutionAgent: 'native' as const }
    const action = type === 'SET' ? { type, sessions: [native] }
      : type === 'ADD' ? { type, session: native }
        : { type, taskId: 'root', partial: native }
    assert.equal(sessionReducer([created()], action)[0].preferredAgent, 'native')
  }
})
