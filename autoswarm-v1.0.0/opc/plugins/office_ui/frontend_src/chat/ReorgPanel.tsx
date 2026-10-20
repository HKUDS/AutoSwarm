import React, { useCallback } from 'react'
import type { ChatMessageMeta } from '../types/chat'

interface ReorgPanelProps {
  meta: ChatMessageMeta
  onReply: (text: string) => void
  responded: boolean
}

const SCOPE_LABELS: Record<string, string> = {
  task_adjustment: 'Task Adjustment',
  runtime_replan: 'Runtime Replan',
  org_mutation: 'Org Mutation',
}

const RISK_COLORS: Record<string, string> = {
  low: 'var(--green)',
  medium: 'var(--yellow)',
  high: 'var(--red)',
}

export const ReorgPanel = React.memo(function ReorgPanel({
  meta, onReply, responded,
}: ReorgPanelProps) {
  const isResponded = responded

  const handleApprove = useCallback(() => {
    if (isResponded) return
    onReply('approve')
  }, [isResponded, onReply])

  const handleDeny = useCallback(() => {
    if (isResponded) return
    onReply('deny')
  }, [isResponded, onReply])

  const roleChanges = meta.role_changes ?? []
  const projectionChanges = meta.work_item_projection_changes ?? []
  const scope = meta.scope || 'org_mutation'
  const risk = meta.risk_level || 'medium'
  const impact = meta.impact_summary || {}

  return (
    <div className="ckpt-panel ckpt-reorg">
      <div className="ckpt-header">
        <div className="ckpt-icon ckpt-icon-reorg">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="1" y="1" width="5" height="5" rx="1" />
            <rect x="10" y="1" width="5" height="5" rx="1" />
            <rect x="5.5" y="10" width="5" height="5" rx="1" />
            <path d="M3.5 6v2.5a1 1 0 001 1h7a1 1 0 001-1V6" />
            <path d="M8 9.5V10" />
          </svg>
        </div>
        <div className="ckpt-title">{meta.title || 'Company Reorg'}</div>
        {isResponded && <span className="ckpt-badge ckpt-badge-responded">Responded</span>}
      </div>

      <div className="ckpt-reorg-badges">
        <span className="ckpt-badge ckpt-badge-scope">{SCOPE_LABELS[scope] || scope}</span>
        <span className="ckpt-badge" style={{ color: RISK_COLORS[risk] || 'var(--text-secondary)', borderColor: RISK_COLORS[risk] || 'var(--border)' }}>
          Risk: {risk.charAt(0).toUpperCase() + risk.slice(1)}
        </span>
      </div>

      {meta.summary && <div className="ckpt-summary">{meta.summary}</div>}
      {meta.rationale && <div className="ckpt-rationale">{meta.rationale}</div>}

      {roleChanges.length > 0 && (
        <div className="ckpt-changes-section">
          <div className="ckpt-changes-title">Role Changes</div>
          {roleChanges.map((rc, i) => (
            <div key={i} className="ckpt-change-row">
              <span className={`ckpt-change-action ckpt-action-${rc.action}`}>{rc.action}</span>
              <span className="ckpt-change-id">{rc.role_id}</span>
              {rc.replacement_role_id && (
                <>
                  <span className="ckpt-change-arrow">&rarr;</span>
                  <span className="ckpt-change-id">{rc.replacement_role_id}</span>
                </>
              )}
              {rc.reason && <span className="ckpt-change-reason">{rc.reason}</span>}
            </div>
          ))}
        </div>
      )}

      {projectionChanges.length > 0 && (
        <div className="ckpt-changes-section">
          <div className="ckpt-changes-title">Work Item Projection Changes</div>
          {projectionChanges.map((change, i) => (
            <div key={i} className="ckpt-change-row">
              <span className={`ckpt-change-action ckpt-action-${change.action}`}>{change.action}</span>
              <span className="ckpt-change-id">{change.work_item_projection_id}</span>
              {change.replacement_work_item_projection_id && (
                <>
                  <span className="ckpt-change-arrow">&rarr;</span>
                  <span className="ckpt-change-id">{change.replacement_work_item_projection_id}</span>
                </>
              )}
              {change.reason && <span className="ckpt-change-reason">{change.reason}</span>}
            </div>
          ))}
        </div>
      )}

      {Object.keys(impact).length > 0 && (
        <div className="ckpt-impact">
          {impact.affected_tasks != null && <span>Tasks affected: {impact.affected_tasks}</span>}
          {impact.affected_roles != null && <span>Roles affected: {impact.affected_roles}</span>}
          {impact.migration_count != null && <span>Migrations: {impact.migration_count}</span>}
        </div>
      )}

      {!isResponded && (
        <div className="ckpt-actions">
          <button className="ckpt-btn ckpt-btn-approve" onClick={handleApprove}>Approve Reorg</button>
          <button className="ckpt-btn ckpt-btn-deny" onClick={handleDeny}>Deny</button>
        </div>
      )}
    </div>
  )
})
