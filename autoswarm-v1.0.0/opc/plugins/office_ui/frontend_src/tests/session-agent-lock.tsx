import React, { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { mapBackendSession } from '../lib/collabSync'
import { useSessionStore } from '../stores/SessionStore'
import { ContextPanel } from '../workspace/ContextPanel'
import '../index.css'
import '../chat/chat.css'
import '../workspace/workspace.css'

declare global {
  interface Window {
    __agentLockFixture?: {
      select: (taskId: string) => void
      refresh: () => void
      switchProject: (project: string) => void
    }
  }
}

const evidence = await fetch('/agent-lock-evidence.json').then(response => response.json())
const rows = Array.isArray(evidence) ? evidence : [
  { task_id: 'adaptive-codex', preferred_agent: 'codex', selected_execution_agent: 'native', company_team_strategy: 'adaptive' },
  { task_id: 'fixed-codex', preferred_agent: 'codex', selected_execution_agent: '', company_team_strategy: 'fixed' },
  { task_id: 'explicit-native', preferred_agent: 'native', selected_execution_agent: 'codex', company_team_strategy: 'adaptive' },
]
const sessions = rows.map((row: Record<string, unknown>) => mapBackendSession({
  project_id: 'project02', channel_id: `session:${row.task_id}`, session_id: `chat:${row.task_id}`,
  title: `Regression ${row.task_id}`, mode: 'primary', exec_mode: 'company', company_profile: 'corporate',
  status: 'idle', message_count: 3, ...row,
}))
const other = mapBackendSession({
  project_id: 'other-project', task_id: 'other-root', channel_id: 'session:other-root',
  mode: 'primary', exec_mode: 'company', company_profile: 'corporate', status: 'idle', message_count: 3,
  preferred_agent: 'native', selected_execution_agent: 'native', title: 'Other project',
})

function Fixture() {
  const store = useSessionStore()
  useEffect(() => {
    store.initFromBackend('project02', sessions)
    store.setActiveSession(sessions[0].taskId)
  }, [])
  useEffect(() => {
    window.__agentLockFixture = {
      select: store.setActiveSession,
      refresh: () => {
        const session = store.activeSession
        if (!session) return
        // Reproduce the old server detail response and the exact App mapper.
        store.updateSession(session.taskId, mapBackendSession({
          project_id: session.projectId, task_id: session.taskId, session_id: session.sessionId,
          channel_id: session.channelId, mode: session.mode, exec_mode: session.execMode,
          company_profile: session.companyProfile, org_id: session.orgId, status: 'idle', message_count: 3,
        }))
      },
      switchProject: project => {
        const incoming = project === 'project02' ? sessions : [other]
        store.initFromBackend(project, incoming)
        store.setActiveSession(incoming[0].taskId)
      },
    }
  }, [store])
  const active = store.activeSession
  if (!active) return null
  return <main style={{ display: 'flex', width: '100vw', height: '100vh' }}>
    <ContextPanel panelState="open" width={1100} onResizeMouseDown={() => {}} isResizing={false}
      activeView={{ kind: 'session', taskId: active.taskId }} activeSession={active}
      activeTask={null} linkedTaskSession={null} linkedTaskSessionMessages={[]} childDetailSession={null}
      messages={[]} childDetailMessages={[]} allSessions={store.sessions} openSessions={store.sessions}
      multiViewSessions={[]} multiSessionMessages={{}} agents={[]} childSessions={[]}
      taskPreferredAgent="native" nativeApprovalDefault="auto" channelId={active.channelId}
      channelName={active.title} secretaryChannelId="secretary:project02" multiSessionView={false}
      panelTab="chat" onPanelTabChange={() => {}} onTitleChange={() => {}}
      onSelectSessionTab={store.setActiveSession} onCollapse={() => {}} onExpand={() => {}}
      onMaximize={() => {}} onComposerSend={() => {}} onSessionTaskAgentChange={() => {}}
      onInteractionReply={async () => ({ ok: true, accepted: true, checkpoint_id: 'fixture', checkpoint_type: 'fixture', client_request_id: 'fixture' })}
      onWorkItemClick={() => {}} onMarkRead={() => {}} />
  </main>
}

createRoot(document.getElementById('root')!).render(<Fixture />)
