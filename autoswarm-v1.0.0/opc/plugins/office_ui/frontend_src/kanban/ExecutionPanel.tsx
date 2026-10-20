import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  ProgressEntry,
  RoleAggregatedStatus,
  RoleWorkItemActivitySection,
  RoleWorkItemRow,
  RoleWorkItemSummary,
} from '../types/kanban'
import type { AgentInfo } from '../types/visual'
import { AgentProgressBlock } from '../chat/AgentProgressBlock'
import { IconClose, IconTimeline, IconWorkItem } from '../chat/SvgIcons'

interface ExecutionPanelProps {
  role: RoleWorkItemSummary
  focusedWorkItemId?: string
  focusedExecutionTurnId?: string
  agents: AgentInfo[]
  onClose: () => void
}

const ROLE_STATUS_BADGE: Record<RoleAggregatedStatus, { label: string; cls: string }> = {
  active: { label: 'Working', cls: 'exec-badge-running' },
  waiting: { label: 'Waiting', cls: 'exec-badge-idle' },
  pending: { label: 'Pending', cls: 'exec-badge-pending' },
  done: { label: 'Done', cls: 'exec-badge-done' },
  failed: { label: 'Failed', cls: 'exec-badge-failed' },
}

const ROW_STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  todo: { label: 'To do', cls: 'exec-badge-pending' },
  'in-progress': { label: 'In progress', cls: 'exec-badge-running' },
  'in-review': { label: 'In review', cls: 'exec-badge-idle' },
  done: { label: 'Done', cls: 'exec-badge-done' },
  failed: { label: 'Failed', cls: 'exec-badge-failed' },
  cancelled: { label: 'Cancelled', cls: 'exec-badge-cancelled' },
}

function formatRelativeTime(ts: number): string {
  const sec = Math.floor((Date.now() - ts) / 1000)
  if (sec < 5) return 'now'
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  return `${Math.floor(hr / 24)}d ago`
}

function humanize(value?: string): string {
  const text = String(value ?? '').trim()
  if (!text) return ''
  return text.replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function rowBadge(row: RoleWorkItemRow): { label: string; cls: string } {
  if (row.phase === 'failed') return ROW_STATUS_BADGE.failed
  if (row.phase === 'cancelled') return ROW_STATUS_BADGE.cancelled
  return ROW_STATUS_BADGE[row.kanbanColumn] ?? ROW_STATUS_BADGE.todo
}

function rowSessionStatus(row: RoleWorkItemRow): string | undefined {
  if (row.phase === 'failed') return 'failed'
  if (row.phase === 'cancelled') return 'cancelled'
  if (row.kanbanColumn === 'done') return 'done'
  return undefined
}

function countActivityEntries(row: RoleWorkItemRow): number {
  const sections = row.activitySections ?? []
  if (sections.length > 0) {
    return sections.reduce((count, section) => count + (section.entries?.length ?? 0), 0)
  }
  return row.progressLog.length
}

function ActivitySections({
  sections,
  fallbackEntries,
  sessionStatus,
}: {
  sections?: RoleWorkItemActivitySection[]
  fallbackEntries?: ProgressEntry[]
  sessionStatus?: string
}) {
  const visibleSections = (sections ?? []).filter(section => (
    (section.entries?.length ?? 0) > 0 || !!section.runtimeTaskId
  ))

  if (visibleSections.length > 0) {
    return (
      <div className="exec-activity-sections">
        {visibleSections.map((section, index) => {
          const entries = section.entries ?? []
          const key = `${section.runtimeTaskId || section.kind}:${index}`
          return (
            <section key={key} className="exec-activity-section">
              <div className="exec-activity-section-head">
                <span className="exec-activity-section-title">{section.title}</span>
                {section.roleName && (
                  <span className="exec-activity-section-role">{section.roleName}</span>
                )}
                {entries.length > 0 && (
                  <span className="exec-section-count">{entries.length}</span>
                )}
              </div>
              {entries.length > 0 ? (
                <AgentProgressBlock
                  entries={entries}
                  sessionStatus={sessionStatus}
                  expandedByDefault
                />
              ) : (
                <div className="exec-section-empty">No runtime activity yet</div>
              )}
            </section>
          )
        })}
      </div>
    )
  }

  if (!fallbackEntries || fallbackEntries.length === 0) {
    return <div className="exec-section-empty">No activity recorded yet</div>
  }
  return (
    <AgentProgressBlock
      entries={fallbackEntries}
      sessionStatus={sessionStatus}
      expandedByDefault
    />
  )
}

export function ExecutionPanel({
  role,
  focusedWorkItemId,
  focusedExecutionTurnId,
  agents,
  onClose,
}: ExecutionPanelProps) {
  const rows = useMemo(() => (
    role.workItems
      .slice()
      .sort((a, b) => a.createdAt - b.createdAt)
  ), [role.workItems])

  const focused = useMemo(() => (
    rows.find(row => (
      (!!focusedWorkItemId && row.workItemId === focusedWorkItemId)
      || (!!focusedExecutionTurnId && row.executionTurnId === focusedExecutionTurnId)
    )) ?? rows[rows.length - 1] ?? null
  ), [focusedExecutionTurnId, focusedWorkItemId, rows])

  const focusedRowKey = focused?.workItemId
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => {
    const init = new Set<string>()
    if (focusedRowKey) init.add(focusedRowKey)
    return init
  })
  const autoExpandedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!focusedRowKey || autoExpandedRef.current.has(focusedRowKey)) return
    autoExpandedRef.current.add(focusedRowKey)
    setExpandedIds(prev => {
      if (prev.has(focusedRowKey)) return prev
      const next = new Set(prev)
      next.add(focusedRowKey)
      return next
    })
  }, [focusedRowKey])

  const toggleExpanded = useCallback((workItemId: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(workItemId)) next.delete(workItemId)
      else next.add(workItemId)
      return next
    })
  }, [])

  const focusedCardRef = useRef<HTMLDivElement | null>(null)
  const lastScrolledIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!focusedRowKey || lastScrolledIdRef.current === focusedRowKey) return
    lastScrolledIdRef.current = focusedRowKey
    focusedCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [focusedRowKey])

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])
  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  const roleAgent = agents.find(agent => agent.agent_id === role.roleId)
  const roleName = role.roleName || roleAgent?.name || humanize(role.roleId) || 'Role'
  const headerBadge = ROLE_STATUS_BADGE[role.aggregatedStatus] ?? ROLE_STATUS_BADGE.pending

  return (
    <>
      <div className="exec-panel-backdrop" onClick={onClose} />
      <div className="exec-panel">
        <div className="exec-panel-header">
          <div className="exec-panel-title-row">
            <IconTimeline />
            <h3 className="exec-panel-title">{roleName}</h3>
            <span className={`exec-badge ${headerBadge.cls}`}>{headerBadge.label}</span>
            <span className="exec-panel-task-count">
              {rows.length} Work Item{rows.length === 1 ? '' : 's'}
            </span>
          </div>
          <button className="exec-panel-close" onClick={onClose} title="Close (Esc)">
            <IconClose />
          </button>
        </div>

        <div className="exec-panel-identity">
          <div className="exec-panel-avatar">
            {roleName.charAt(0).toUpperCase()}
          </div>
          <div className="exec-panel-agent-info">
            <span className="exec-panel-agent-name">{roleName}</span>
            <span className="exec-panel-agent-role">{humanize(role.roleId)}</span>
            {role.roleSessionId && (
              <span className="exec-panel-employee">{role.roleSessionId}</span>
            )}
          </div>
        </div>

        <div className="exec-panel-body">
          {rows.length === 0 && (
            <div className="exec-section-empty">No work items yet</div>
          )}
          {rows.map((row, index) => {
            const isFocused = row.workItemId === focusedRowKey
            const isExpanded = expandedIds.has(row.workItemId)
            const badge = rowBadge(row)
            const activityCount = countActivityEntries(row)
            return (
              <div
                key={row.workItemId}
                ref={isFocused ? focusedCardRef : null}
                className={`exec-task-card${isFocused ? ' exec-task-card-focused' : ''}`}
              >
                <button
                  type="button"
                  className="exec-task-card-header"
                  onClick={() => toggleExpanded(row.workItemId)}
                  title={isExpanded ? 'Collapse' : 'Expand'}
                >
                  <span className="exec-task-card-index">Work item #{index + 1}</span>
                  <span className="exec-task-card-title">{row.title || row.workItemId}</span>
                  <span className={`exec-badge ${badge.cls}`}>{badge.label}</span>
                  <span className="exec-task-card-time">{formatRelativeTime(row.updatedAt)}</span>
                  <span className={`exec-task-card-chevron${isExpanded ? ' open' : ''}`} aria-hidden="true">
                    ▸
                  </span>
                </button>

                {isExpanded && (
                  <div className="exec-task-card-body">
                    <div className="exec-task-card-projection">
                      <IconWorkItem />
                      <span>{humanize(row.kind) || 'Work item'}</span>
                      {row.workItemProjectionId && <code>{row.workItemProjectionId}</code>}
                      {row.executionTurnId && <span className="exec-inline-tag">Execution Turn</span>}
                      {row.isReviewTarget && <span className="exec-inline-tag">Review target</span>}
                      {row.executorRoleName && <span>{row.executorRoleName}</span>}
                    </div>

                    <div className="exec-section">
                      <div className="exec-section-header">
                        <IconTimeline />
                        <span>Activity</span>
                        <span className="exec-section-count">{activityCount}</span>
                      </div>
                      <div className="exec-section-content exec-activity-scroll">
                        <ActivitySections
                          sections={row.activitySections}
                          fallbackEntries={row.progressLog}
                          sessionStatus={rowSessionStatus(row)}
                        />
                      </div>
                    </div>

                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
