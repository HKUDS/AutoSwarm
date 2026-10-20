import React, { useMemo, useState } from 'react'
import type { ProgressEntry, ProgressEntryType } from '../types/kanban'
import { progressEntryKey } from '../lib/progressEntryKey'
import { IconBrain, IconTool, IconChevron, IconSparkle, IconShield, IconArrowRight, IconGate, IconZap, IconWorkItem, IconGatePass, IconGateReject, IconClock, IconHandoff } from './SvgIcons'

interface AgentProgressBlockProps {
  entries: ProgressEntry[]
  agentStatus?: string
  currentTool?: string
  toolElapsedMs?: number
  lastToolSummary?: string
  sessionStatus?: string
  expandedByDefault?: boolean
}

const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled'])
export const INLINE_PROGRESS_ENTRY_TYPES = new Set<ProgressEntryType>(['thinking', 'tool_call', 'autonomy', 'needs_input', 'verification'])

const ENTRY_CONFIG: Record<ProgressEntryType, { icon: React.ReactNode; color: string; label: string }> = {
  thinking:      { icon: <IconBrain />,       color: 'var(--accent)',          label: 'Thinking' },
  assistant:     { icon: <IconSparkle />,     color: 'var(--accent)',          label: 'Reply' },
  tool_call:     { icon: <IconTool />,        color: 'var(--green)',           label: 'Tool' },
  autonomy:      { icon: <IconShield />,      color: 'var(--yellow)',          label: 'Autonomy' },
  handoff:       { icon: <IconArrowRight />,  color: 'var(--accent)',          label: 'Handoff' },
  gate_result:   { icon: <IconGate />,        color: 'var(--green)',           label: 'Gate' },
  status_change:   { icon: <IconZap />,         color: 'var(--text-secondary)',  label: 'Status' },
  work_item_started:   { icon: <IconWorkItem />,       color: 'var(--accent)',          label: 'Work item' },
  gate_approved:   { icon: <IconGatePass />,    color: 'var(--green)',           label: 'Gate Passed' },
  gate_rejected:   { icon: <IconGateReject />,  color: 'var(--red)',             label: 'Rejected' },
  awaiting_manager_review: { icon: <IconClock />, color: 'var(--yellow)',        label: 'Awaiting Manager Review' },
  awaiting_human:  { icon: <IconClock />,       color: 'var(--yellow)',          label: 'Awaiting Human Review' },
  awaiting_review: { icon: <IconClock />,       color: 'var(--yellow)',          label: 'Awaiting Review' },
  awaiting_peer:   { icon: <IconClock />,       color: 'var(--yellow)',          label: 'Awaiting Peer' },
  work_item_failed:    { icon: <IconZap />,         color: 'var(--red)',             label: 'Failed' },
  deadlock:        { icon: <IconHandoff />,     color: 'var(--red)',             label: 'Deadlock' },
  needs_input:     { icon: <IconClock />,       color: 'var(--yellow)',          label: 'Needs Input' },
  verification:    { icon: <IconShield />,      color: 'var(--accent)',          label: 'Verification' },
}

const COLLAPSED_COUNT = 5

function elapsed(ts: number): string {
  const sec = Math.floor((Date.now() - ts) / 1000)
  if (sec < 5) return 'just now'
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  return `${Math.floor(min / 60)}h ago`
}

function normalizeNestedJson(value: unknown): unknown {
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}'))
      || (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        return normalizeNestedJson(JSON.parse(trimmed))
      } catch {
        return value
      }
    }
    return value
  }

  if (Array.isArray(value)) {
    return value.map(normalizeNestedJson)
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, nestedValue]) => [key, normalizeNestedJson(nestedValue)]),
    )
  }

  return value
}

function formatToolDetail(detail: string): string {
  const trimmed = detail.trim()
  if (!trimmed) return detail
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return detail

  try {
    return JSON.stringify(normalizeNestedJson(JSON.parse(trimmed)), null, 2)
  } catch {
    return detail
  }
}

export function AgentProgressBlock({ entries, agentStatus, currentTool, toolElapsedMs, lastToolSummary, sessionStatus, expandedByDefault }: AgentProgressBlockProps) {
  const [expanded, setExpanded] = useState(!!expandedByDefault)

  const isTerminal = !!sessionStatus && TERMINAL_STATUSES.has(sessionStatus)
  const isThinking = !isTerminal && agentStatus === 'reflecting'
  const isToolActive = !isTerminal && agentStatus === 'tool_active'
  const isWorking = isThinking || isToolActive

  const filteredEntries = useMemo(() => {
    return entries
  }, [entries])

  const visibleEntries = useMemo(() => {
    if (expanded || filteredEntries.length <= COLLAPSED_COUNT) return filteredEntries
    return filteredEntries.slice(-COLLAPSED_COUNT)
  }, [filteredEntries, expanded])

  const hiddenCount = filteredEntries.length - visibleEntries.length

  if (filteredEntries.length === 0 && !isWorking && !isTerminal) return null

  return (
    <div className="ptl-block">
      {/* ── Live status indicator ──────────────────────── */}
      {isWorking && (
        <div className={`ptl-live ${isToolActive ? 'ptl-live-tool' : 'ptl-live-think'}`}>
          <span className="ptl-live-icon">
            {isToolActive ? <IconTool /> : <IconSparkle />}
          </span>
          <span className="ptl-live-text">
            {isToolActive ? 'Running' : 'Thinking'}
          </span>
          {isToolActive && currentTool && (
            <code className="ptl-live-tool-name">{currentTool}</code>
          )}
          {isToolActive && typeof toolElapsedMs === 'number' && toolElapsedMs > 0 && (
            <span className="ptl-live-elapsed">
              {toolElapsedMs < 1000 ? `${toolElapsedMs}ms` : `${(toolElapsedMs / 1000).toFixed(1)}s`}
            </span>
          )}
          <span className="ptl-live-shimmer" />
        </div>
      )}
      {/* ── Last tool result summary ──────────────────── */}
      {lastToolSummary && !isToolActive && (
        <div className="ptl-last-tool-summary">
          <span className="ptl-last-tool-label">Last tool result:</span>
          <span className="ptl-last-tool-text">{lastToolSummary}</span>
        </div>
      )}

      {/* ── Collapse toggle (above timeline) ──────────── */}
      {hiddenCount > 0 && (
        <button className="ptl-expand" onClick={() => setExpanded(true)}>
          <IconChevron />
          <span>{hiddenCount} earlier step{hiddenCount > 1 ? 's' : ''}</span>
        </button>
      )}

      {/* ── Timeline entries ───────────────────────────── */}
      {visibleEntries.length > 0 && (
        <div className="ptl-timeline">
          {visibleEntries.map((entry, i) => {
            const isLast = i === visibleEntries.length - 1
            const cfg = ENTRY_CONFIG[entry.type] || ENTRY_CONFIG.status_change

            return (
              <div key={progressEntryKey(entry)} className={`ptl-entry${isLast ? ' ptl-entry-last' : ''}`}>
                <div className="ptl-connector">
                  <div className="ptl-dot" style={{ color: cfg.color }}>
                    {cfg.icon}
                  </div>
                  {!isLast && <div className="ptl-line" />}
                </div>
                <div className="ptl-content">
                  <AgentProgressEntryCard entry={entry} />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Terminal state completion indicator ──────────── */}
      {isTerminal && !isWorking && (
        <div className={`ptl-completion ptl-completion-${sessionStatus}`}>
          <span className="ptl-completion-icon">
            {sessionStatus === 'done' ? '\u2713' : sessionStatus === 'failed' ? '\u2717' : '\u2014'}
          </span>
          <span className="ptl-completion-text">
            {sessionStatus === 'done' ? 'Completed' : sessionStatus === 'failed' ? 'Failed' : 'Cancelled'}
          </span>
        </div>
      )}

      {/* ── Expand/collapse toggle (below timeline) ────── */}
      {expanded && filteredEntries.length > COLLAPSED_COUNT && (
        <button className="ptl-expand" onClick={() => setExpanded(false)}>
          <IconChevron down />
          <span>Show less</span>
        </button>
      )}
      {!expanded && hiddenCount > 0 && (
        <button className="ptl-expand" onClick={() => setExpanded(true)}>
          <IconChevron />
          <span>Show more ({hiddenCount} earlier step{hiddenCount > 1 ? 's' : ''})</span>
        </button>
      )}
    </div>
  )
}

export const AgentProgressEntryCard = React.memo(function AgentProgressEntryCard({ entry }: { entry: ProgressEntry }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = ENTRY_CONFIG[entry.type] || ENTRY_CONFIG.status_change
  const hasToolDetail = entry.type === 'tool_call' && !!entry.detail

  if (entry.type === 'tool_call') {
    return (
      <div className={`ptl-tool-card${expanded ? ' expanded' : ''}`}>
        <button
          className={`ptl-row ptl-tool-toggle${hasToolDetail ? ' clickable' : ''}`}
          onClick={() => {
            if (!hasToolDetail) return
            setExpanded(prev => !prev)
          }}
          type="button"
        >
          <span className="ptl-label" style={{ color: cfg.color }}>{cfg.label}</span>
          <code className="ptl-tool-badge">{entry.summary}</code>
          <span className="ptl-time">{elapsed(entry.timestamp)}</span>
          {hasToolDetail && (
            <span className="ptl-tool-chevron">
              <IconChevron down={expanded} />
            </span>
          )}
        </button>
        {expanded && entry.detail && (
          <pre className="ptl-tool-card-detail">{formatToolDetail(entry.detail)}</pre>
        )}
      </div>
    )
  }

  if (entry.type === 'autonomy') {
    const hasDetail = !!entry.detail
    return (
      <div className={`ptl-tool-card ptl-autonomy-card${expanded ? ' expanded' : ''}`}>
        <button
          className={`ptl-row ptl-tool-toggle${hasDetail ? ' clickable' : ''}`}
          onClick={() => { if (hasDetail) setExpanded(prev => !prev) }}
          type="button"
        >
          <span className="ptl-label" style={{ color: cfg.color }}>{cfg.label}</span>
          <code className="ptl-tool-badge">{entry.summary}</code>
          <span className="ptl-time">{elapsed(entry.timestamp)}</span>
          {hasDetail && (
            <span className="ptl-tool-chevron">
              <IconChevron down={expanded} />
            </span>
          )}
        </button>
        {expanded && entry.detail && (
          <div className="ptl-tool-card-detail ptl-autonomy-detail">{entry.detail}</div>
        )}
      </div>
    )
  }

  if (entry.type === 'thinking' || entry.type === 'assistant') {
    return (
      <div className="ptl-tool-card">
        <button
          className="ptl-row ptl-tool-toggle clickable"
          onClick={() => setExpanded(prev => !prev)}
          type="button"
        >
          <span className="ptl-label" style={{ color: cfg.color }}>{cfg.label}</span>
          <span className="ptl-summary">{entry.summary}</span>
          <span className="ptl-time">{elapsed(entry.timestamp)}</span>
          <span className="ptl-tool-chevron">
            <IconChevron down={expanded} />
          </span>
        </button>
        {expanded && (
          <div className="ptl-tool-card-detail ptl-thinking-detail">{entry.detail || entry.summary}</div>
        )}
      </div>
    )
  }

  if (entry.type === 'verification') {
    const hasDetail = !!entry.detail
    return (
      <div className={`ptl-tool-card ptl-verification-card${expanded ? ' expanded' : ''}`}>
        <button
          className={`ptl-row ptl-tool-toggle${hasDetail ? ' clickable' : ''}`}
          onClick={() => { if (hasDetail) setExpanded(prev => !prev) }}
          type="button"
        >
          <span className="ptl-label" style={{ color: cfg.color }}>{cfg.label}</span>
          <span className="ptl-summary">{entry.summary || 'Verification'}</span>
          <span className="ptl-time">{elapsed(entry.timestamp)}</span>
          {hasDetail && (
            <span className="ptl-tool-chevron">
              <IconChevron down={expanded} />
            </span>
          )}
        </button>
        {expanded && entry.detail && (
          <div className="ptl-tool-card-detail ptl-verification-detail">{entry.detail}</div>
        )}
      </div>
    )
  }

  return (
    <>
      <div className="ptl-row">
        <span className="ptl-label" style={{ color: cfg.color }}>{cfg.label}</span>
        <span className="ptl-summary">{entry.summary}</span>
        <span className="ptl-time">{elapsed(entry.timestamp)}</span>
      </div>
      {entry.detail && (
        <div className="ptl-detail">{entry.detail}</div>
      )}
    </>
  )
})
