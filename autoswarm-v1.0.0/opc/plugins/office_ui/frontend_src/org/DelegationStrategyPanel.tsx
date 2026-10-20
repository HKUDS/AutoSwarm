import type { OrgRole, RuntimeFrontierSummary, RuntimeSeatInfo, RuntimeTeamInfo, RuntimeWorkItemInfo, RuntimePolicy } from '../types/visual'

interface DelegationStrategyPanelProps {
  roles: OrgRole[]
  runtimeTeams: RuntimeTeamInfo[]
  runtimeSeats: RuntimeSeatInfo[]
  workItems: RuntimeWorkItemInfo[]
  frontier: RuntimeFrontierSummary
  companyProfile: string
  runtimePolicy?: RuntimePolicy
  finalDeciderRoleId?: string | null
  topLevelRoleIds?: string[]
  readOnly?: boolean
  onUpdateOrgStrategy?: (data: { final_decider_role_id?: string | null }) => void
  onUpdateRuntimePolicy?: (policy: Record<string, any>) => void
}

function adaptiveForWorkItem(item: RuntimeWorkItemInfo): Record<string, unknown> | undefined {
  if (item.adaptive && typeof item.adaptive === 'object') return item.adaptive
  const metadata = item.metadata
  if (metadata && typeof metadata === 'object' && metadata.adaptive && typeof metadata.adaptive === 'object') {
    return metadata.adaptive as Record<string, unknown>
  }
  return undefined
}

function missingSignalsForWorkItem(item: RuntimeWorkItemInfo): string[] {
  const adaptive = adaptiveForWorkItem(item)
  const signals = Array.isArray(adaptive?.signals) ? adaptive.signals : []
  return signals
    .filter(signal => signal && typeof signal === 'object')
    .filter(signal => Boolean((signal as Record<string, unknown>).required ?? true) && !Boolean((signal as Record<string, unknown>).satisfied))
    .map(signal => String((signal as Record<string, unknown>).name ?? '').trim())
    .filter(Boolean)
}

function gateOwnerForWorkItem(item: RuntimeWorkItemInfo): string {
  const adaptive = adaptiveForWorkItem(item)
  const stageProfile = adaptive?.work_item_profile
  if (stageProfile && typeof stageProfile === 'object') {
    return String((stageProfile as Record<string, unknown>).gate_owner_role_id ?? '').trim()
  }
  return ''
}

function adaptiveConfidenceLabel(item: RuntimeWorkItemInfo): string {
  const adaptive = adaptiveForWorkItem(item)
  const confidence = typeof adaptive?.confidence === 'number' ? adaptive.confidence : undefined
  return typeof confidence === 'number' ? `${Math.round(confidence * 100)}%` : ''
}

export function DelegationStrategyPanel({
  roles,
  runtimeTeams,
  runtimeSeats,
  workItems,
  frontier,
  companyProfile,
  runtimePolicy,
  finalDeciderRoleId,
  topLevelRoleIds,
  readOnly = false,
  onUpdateOrgStrategy,
  onUpdateRuntimePolicy,
}: DelegationStrategyPanelProps) {
  const topLevel = roles.filter(r => (topLevelRoleIds ?? []).includes(r.role_id))
  const selectedFinalDecider = finalDeciderRoleId || (topLevel.length === 1 ? topLevel[0]?.role_id : '')
  const hasSelectionError = topLevel.length > 1 && !selectedFinalDecider
  const roleNameMap = new Map(roles.map(r => [r.role_id, r.name]))

  return (
    <div className="wfe-container">
      <div className="wfe-header">
        <h3 className="wfe-title">Actor Runtime</h3>
        <span className="wfe-profile-badge">{companyProfile}</span>
      </div>

      <div className="myorg-collapsible" style={{ margin: '0 0 8px' }}>
        <div className="myorg-collapsible-body" style={{ display: 'block' }}>
          <div className="oc-form-row">
            <label>Final decider</label>
            <select
              value={selectedFinalDecider}
              disabled={readOnly}
              onChange={e => {
                if (readOnly) return
                onUpdateOrgStrategy?.({ final_decider_role_id: e.target.value || null })
              }}
            >
              <option value="">{topLevel.length > 1 ? 'Select top-level role' : 'Auto-select only top-level role'}</option>
              {topLevel.map(role => (
                <option key={role.role_id} value={role.role_id}>{role.name}</option>
              ))}
            </select>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Runtime wakeups, delegation, approvals, and recovery are seat-scoped.
          </div>
          {hasSelectionError && (
            <div className="org-toast org-toast--warn" style={{ margin: '0 0 8px' }}>
              Multiple top-level roles exist. Select one final decider before company execution can start.
            </div>
          )}
        </div>
      </div>

      {(runtimeTeams.length || runtimeSeats.length || workItems.length) ? (
        <div className="myorg-collapsible" style={{ margin: '0 0 8px' }}>
          <div className="myorg-collapsible-body" style={{ display: 'block' }}>
            <div className="oc-form-row">
              <label>Runtime</label>
              <div>
                {frontier.status || 'running'}
                {frontier.run_id ? ` (${frontier.run_id.slice(0, 8)})` : ''}
              </div>
            </div>
            <div className="oc-form-row">
              <label>Frontier</label>
              <div>
                {frontier.running_count ?? 0} running, {frontier.ready_count ?? 0} ready, {frontier.blocked_count ?? 0} blocked, {frontier.waiting_count ?? 0} waiting
              </div>
            </div>
            <div className="oc-form-row">
              <label>Teams</label>
              <div>{runtimeTeams.length}</div>
            </div>
            <div className="oc-form-row">
              <label>Seats</label>
              <div>{runtimeSeats.length}</div>
            </div>
            <div className="oc-form-row">
              <label>Work items</label>
              <div>{workItems.length}</div>
            </div>
            {workItems.length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {workItems.slice(0, 6).map(item => {
                  const adaptive = adaptiveForWorkItem(item)
                  const normalizedState = typeof adaptive?.normalized_state === 'string' ? adaptive.normalized_state : ''
                  const blockedReason = typeof adaptive?.blocked_reason === 'string' ? adaptive.blocked_reason : (item.blocked_reason ?? '')
                  const gateOwner = gateOwnerForWorkItem(item)
                  const missingSignals = missingSignalsForWorkItem(item)
                  const confidence = adaptiveConfidenceLabel(item)
                  const summary = [
                    blockedReason ? `waiting ${blockedReason}` : '',
                    gateOwner ? `gate ${roleNameMap.get(gateOwner) ?? gateOwner}` : '',
                    missingSignals.length ? `signals ${missingSignals.join(', ')}` : '',
                    confidence ? `confidence ${confidence}` : '',
                    normalizedState === 'invalidated' ? 'invalidated' : '',
                  ].filter(Boolean)
                  return (
                    <div key={item.work_item_id} style={{ marginBottom: 6 }}>
                      <div>
                        {roleNameMap.get(item.role_id) ?? item.role_id}: {item.title} [{item.phase}]
                        {item.kanban_column && (
                          <span style={{ opacity: 0.5 }}> · {item.kanban_column}</span>
                        )}
                        {item.batch_id && <span style={{ opacity: 0.5 }}> batch:{item.batch_id}</span>}
                        {normalizedState && normalizedState !== item.phase && (
                          <span style={{ opacity: 0.7 }}> state:{normalizedState}</span>
                        )}
                      </div>
                      {summary.length > 0 && (
                        <div style={{ marginLeft: 12, opacity: 0.85 }}>
                          {summary.join(' • ')}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      ) : null}

    </div>
  )
}
