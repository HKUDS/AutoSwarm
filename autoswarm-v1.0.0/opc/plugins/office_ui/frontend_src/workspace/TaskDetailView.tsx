import { useMemo } from 'react'
import type { KanbanTask, Session } from '../types/kanban'
import type { ChatMessage } from '../types/chat'
import { AGENT_STATUS_LABEL, PRIORITY_META } from '../types/kanban'
import type { AgentInfo } from '../types/visual'
import { MarkdownBody, MessageList } from '../chat/MessageList'
import { getLinkedRuntimeTaskId } from '../lib/workItemRuntimeIds'

interface TaskDetailViewProps {
  task: KanbanTask
  linkedSession?: Session | null
  linkedSessionMessages?: ChatMessage[]
  agents: AgentInfo[]
  onBack: () => void
  onOpenLinkedSession?: (taskId: string) => void
  onOpenExecutionPanel?: (taskId: string) => void
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? '')
  }
}

function stringList(value: string[] | undefined): string[] {
  return (value ?? []).map(item => item.trim()).filter(Boolean)
}

function infoPairsFromRecord(value: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!value) return []
  return Object.entries(value)
    .filter(([, entry]) => entry !== null && entry !== undefined && entry !== '' && entry !== false)
    .map(([key, entry]) => {
      if (Array.isArray(entry)) return [key, entry.join(', ')]
      if (typeof entry === 'object') return [key, prettyJson(entry)]
      return [key, String(entry)]
    })
}

export function TaskDetailView({
  task,
  linkedSession,
  linkedSessionMessages,
  agents,
  onBack,
  onOpenLinkedSession,
  onOpenExecutionPanel,
}: TaskDetailViewProps) {
  const liveAssignees = useMemo(() => (
    task.assigneeIds
      .map(id => agents.find(agent => agent.agent_id === id))
      .filter(Boolean) as AgentInfo[]
  ), [agents, task.assigneeIds])

  const priorityMeta = task.priority ? PRIORITY_META[task.priority] : null
  const residentAssignment = (task.residentAssignment ?? {}) as Record<string, unknown>
  const memberSessionState = (task.memberSessionState ?? {}) as Record<string, unknown>
  const ownershipContract = (task.ownershipContract ?? {}) as Record<string, unknown>
  const runtimeActive = !!(task.agentStatus && task.agentStatus !== 'idle')
  const deliverables = stringList(task.deliverables)
  const acceptanceCriteria = stringList(task.acceptanceCriteria)
  const dependencyIds = stringList(task.dependencies)
  const assignmentSummary = infoPairsFromRecord({
    role_id: residentAssignment.role_id,
    employee_id: residentAssignment.employee_id,
    manager_role_id: residentAssignment.manager_role_id,
    team_id: residentAssignment.team_id,
    seat_id: residentAssignment.seat_id,
    work_item_turn_type: residentAssignment.work_item_turn_type,
    resident_status: residentAssignment.resident_status,
  })
  const memberStateSummary = infoPairsFromRecord({
    status: memberSessionState.status,
    current_turn_mode: memberSessionState.current_turn_mode,
    manager_role_id: memberSessionState.manager_role_id,
    actionable_inbox_count: memberSessionState.actionable_inbox_count,
    protocol_backlog_count: memberSessionState.protocol_backlog_count,
    notification_backlog_count: memberSessionState.notification_backlog_count,
  })
  const promptContext = String(task.employeeAssignment?.promptContext ?? '').trim()
  const deltaContext = String(task.employeeAssignment?.deltaContext ?? '').trim()
  const linkedTaskId = getLinkedRuntimeTaskId(task)
  const isWorkItem = !!(task.workItemId || task.workItemProjectionId || linkedTaskId)

  return (
    <div className="ctx-child-detail">
      <div className="ctx-child-topbar">
        <button className="ctx-back-btn" onClick={onBack}>
          {'\u2190'} Back to board
        </button>
        {linkedTaskId && (onOpenLinkedSession || onOpenExecutionPanel) && (
          <button
            className="ctx-child-stop-btn"
            onClick={() => {
              if (onOpenLinkedSession) {
                onOpenLinkedSession(linkedTaskId)
                return
              }
              onOpenExecutionPanel?.(linkedTaskId)
            }}
          >
            Open Runtime Session
          </button>
        )}
      </div>

      <div className="ctx-child-header">
        <div className="ctx-child-avatar">
          {(task.workItemRoleName ?? task.title).charAt(0).toUpperCase()}
        </div>
        <div className="ctx-child-meta">
          <span className="ctx-child-name">{task.title}</span>
          <span className="ctx-child-work-item">
            {[task.workItemRoleName, task.phase].filter(Boolean).join(' · ')}
          </span>
        </div>
      </div>

      {runtimeActive && (
        <div className={`task-detail-runtime status-${task.agentStatus}`}>
          <span className="kanban-runtime-dot" />
          <span>
            {task.agentStatus === 'tool_active' && task.currentTool
              ? task.currentTool
              : AGENT_STATUS_LABEL[task.agentStatus!] ?? task.agentStatus}
          </span>
          {liveAssignees.length > 0 && (
            <span className="task-detail-runtime-agent">
              {liveAssignees.map(agent => agent.name).join(', ')}
            </span>
          )}
        </div>
      )}

      <div className="task-detail-body">
        <div className="task-detail-section">
          <h4 className="task-detail-section-title">{isWorkItem ? 'Work Item' : 'Task'}</h4>
          <div className="ctx-task-chip-row">
            <span className="task-detail-dep-id">{task.displayId}</span>
            {priorityMeta && <span className="kanban-tag">{priorityMeta.label}</span>}
            {task.workItemRoleName && <span className="kanban-tag">{task.workItemRoleName}</span>}
            {task.managerRoleId && <span className="kanban-tag">Manager: {task.managerRoleId}</span>}
            {task.scopeKey && <span className="kanban-tag">{task.scopeKey}</span>}
          </div>
          {task.description && (
            <div className="msg-content-agent-card ctx-task-detail-card">
              <MarkdownBody content={task.description} />
            </div>
          )}
        </div>

        {task.originalMessage && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Session Goal</h4>
            <pre className="task-detail-handoff">{task.originalMessage}</pre>
          </div>
        )}

        {task.planningContext && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Planning Context</h4>
            <pre className="task-detail-handoff">{task.planningContext}</pre>
          </div>
        )}

        {deliverables.length > 0 && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Deliverables</h4>
            <ul className="task-detail-dep-list">
              {deliverables.map(item => (
                <li key={item} className="task-detail-dep-item">{item}</li>
              ))}
            </ul>
          </div>
        )}

        {acceptanceCriteria.length > 0 && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Acceptance Criteria</h4>
            <ul className="task-detail-dep-list">
              {acceptanceCriteria.map(item => (
                <li key={item} className="task-detail-dep-item">{item}</li>
              ))}
            </ul>
          </div>
        )}

        {(task.delegationRationale || task.nonOverlapGuard || task.coordinationNotes) && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Delegation Notes</h4>
            {task.delegationRationale && <pre className="task-detail-handoff">{task.delegationRationale}</pre>}
            {task.nonOverlapGuard && <pre className="task-detail-handoff">{task.nonOverlapGuard}</pre>}
            {task.coordinationNotes && <pre className="task-detail-handoff">{task.coordinationNotes}</pre>}
          </div>
        )}

        {dependencyIds.length > 0 && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Dependencies</h4>
            <ul className="task-detail-dep-list">
              {dependencyIds.map(depId => (
                <li key={depId} className="task-detail-dep-item">
                  <span className="task-detail-dep-id">{depId}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {(assignmentSummary.length > 0 || memberStateSummary.length > 0 || linkedSession) && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Role Runtime Context</h4>
            {linkedSession && (
              <div className="ctx-task-chip-row">
                <span className="kanban-tag">Runtime Session: {linkedSession.title}</span>
                <span className="kanban-tag">Status: {linkedSession.status}</span>
              </div>
            )}
            {assignmentSummary.length > 0 && (
              <div className="ctx-task-kv-grid">
                {assignmentSummary.map(([label, value]) => (
                  <div key={label} className="ctx-task-kv-item">
                    <span className="ctx-task-kv-label">{label}</span>
                    <span className="ctx-task-kv-value">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {memberStateSummary.length > 0 && (
              <div className="ctx-task-kv-grid">
                {memberStateSummary.map(([label, value]) => (
                  <div key={label} className="ctx-task-kv-item">
                    <span className="ctx-task-kv-label">{label}</span>
                    <span className="ctx-task-kv-value">{value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {task.handoffContext && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Handoff Context</h4>
            <pre className="task-detail-handoff">{task.handoffContext}</pre>
          </div>
        )}

        {promptContext && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Role Prompt Context</h4>
            <div className="msg-content-agent-card ctx-task-detail-card">
              <MarkdownBody content={promptContext} />
            </div>
          </div>
        )}

        {deltaContext && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Role Delta Context</h4>
            <pre className="task-detail-handoff">{deltaContext}</pre>
          </div>
        )}

        {Object.keys(ownershipContract).length > 0 && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Ownership Contract</h4>
            <pre className="task-detail-handoff">{prettyJson(ownershipContract)}</pre>
          </div>
        )}

        {task.progressLog && task.progressLog.length > 0 && (
          <div className="task-detail-section">
            <h4 className="task-detail-section-title">Activity</h4>
            <ul className="task-detail-progress">
              {task.progressLog.map((entry, index) => (
                <li key={`${entry.timestamp}-${index}`} className={`progress-entry type-${entry.type}`}>
                  <span className="progress-time">
                    {new Date(entry.timestamp).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </span>
                  <span className="progress-summary">{entry.summary}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Runtime session transcript — live chat of the agent processing this work item */}
        <div className="task-detail-section">
          <h4 className="task-detail-section-title">Runtime Session Activity</h4>
          {linkedSession && linkedSessionMessages && linkedSessionMessages.length > 0 ? (
            <div className="task-detail-linked-messages">
              <MessageList
                key={linkedSession.channelId}
                messages={linkedSessionMessages}
                channelName={linkedSession.title ?? 'Runtime Session'}
                detailMode="summary"
                scrollScope={linkedSession.channelId}
              />
            </div>
          ) : linkedSession ? (
            <p className="task-detail-empty-hint">Runtime session has no visible messages yet.</p>
          ) : (
            <p className="task-detail-empty-hint">No Runtime Session linked yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}
