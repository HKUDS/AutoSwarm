import { useMemo } from 'react'
import type { OrgInfoPayload } from '../types/visual'
import type { CommsStatePayload } from '../lib/wsClient'
import { getRuntimeOrgView } from '../lib/runtimeOrg'

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asRecordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item) => item && typeof item === 'object' && !Array.isArray(item)) as Record<string, unknown>[]
    : []
}

function summarizeText(value: unknown, fallback = 'Pending') {
  const text = String(value ?? '').trim()
  return text || fallback
}

function humanizeId(value: unknown, fallback = 'Team') {
  const text = String(value ?? '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function statusToken(value: unknown) {
  return summarizeText(value, 'idle').toLowerCase().replace(/[\s_]+/g, '-')
}

interface TeamCardInfo {
  key: string
  label: string
  managerLabel: string
  status: string
  seatCount: number
  pendingApprovals: number
  latestAlert?: string
  seats: Array<{
    key: string
    roleLabel: string
    seatLabel: string
    status: string
  }>
}

interface ProjectCockpitProps {
  orgInfoData?: OrgInfoPayload | null
  commsState?: CommsStatePayload | null
  onStopRun?: () => void
  embedded?: boolean
}

export function ProjectCockpit({
  orgInfoData,
  commsState,
  onStopRun,
  embedded = false,
}: ProjectCockpitProps) {
  const runtimeView = useMemo(() => getRuntimeOrgView(orgInfoData ?? null), [orgInfoData])
  const projectRun = runtimeView.projectRun
  const seatDigests = runtimeView.seatDigests
  const pendingDecisionCount = seatDigests.reduce((total, digest) => {
    const managerDigest = asRecord(digest.manager_digest)
    return total + asRecordList(managerDigest.pending_decisions).length
  }, 0)
  const actionableCount = seatDigests.reduce((count, digest) => (
    count + asRecordList(asRecord(digest.manager_digest).actionable_chat).length
  ), 0)
  const protocolCount = seatDigests.reduce((count, digest) => (
    count + asRecordList(asRecord(digest.manager_digest).protocol_backlog).length
  ), 0)
  const notificationCount = seatDigests.reduce((count, digest) => (
    count + asRecordList(asRecord(digest.manager_digest).notification_backlog).length
  ), 0)
  const unreadCount = actionableCount + protocolCount + notificationCount

  const communicationItems = [
    { label: 'Actionable', value: actionableCount },
    { label: 'Protocol', value: protocolCount },
    { label: 'Notifications', value: notificationCount },
    { label: 'Meetings', value: commsState?.meetings?.length ?? 0 },
    { label: 'Failures', value: commsState?.recent_failures?.length ?? 0 },
  ]

  const teamCards = useMemo<TeamCardInfo[]>(() => {
    const digestBySeatId = new Map<string, Record<string, unknown>>()
    for (const digest of seatDigests) {
      const seatId = String(digest.seat_id ?? '').trim()
      if (seatId) digestBySeatId.set(seatId, asRecord(digest.manager_digest))
    }

    const cards: TeamCardInfo[] = runtimeView.runtimeTeams.map((team) => {
      const teamKeys = [
        String(team.cell_id ?? '').trim(),
        String(team.team_id ?? '').trim(),
        String(team.team_instance_id ?? '').trim(),
      ].filter(Boolean)
      const seats = runtimeView.runtimeSeats.filter((seat) => (
        teamKeys.includes(String(seat.team_id ?? '').trim())
        || teamKeys.includes(String(seat.team_instance_id ?? '').trim())
      ))
      const pendingApprovals = seats.reduce((count, seat) => {
        const managerDigest = digestBySeatId.get(String(seat.seat_id ?? '').trim()) ?? {}
        return count + asRecordList(managerDigest.pending_decisions).length
      }, 0)
      const latestAlert = seats
        .map((seat) => summarizeText(seat.latest_notification?.subject ?? seat.latest_notification?.summary, ''))
        .find(Boolean)

      return {
        key: String(team.cell_id ?? team.team_id ?? team.team_instance_id ?? team.manager_role_id ?? 'team'),
        label: humanizeId(team.team_id ?? team.cell_id, 'Team'),
        managerLabel: humanizeId(team.manager_role_id, 'Unassigned manager'),
        status: summarizeText(team.status, 'idle'),
        seatCount: seats.length || team.member_role_ids.length,
        pendingApprovals,
        latestAlert,
        seats: seats.map((seat) => ({
          key: String(seat.seat_id ?? seat.role_session_id ?? seat.role_id),
          roleLabel: humanizeId(seat.role_id, 'Seat'),
          seatLabel: summarizeText(seat.seat_id ?? seat.role_session_id, 'Seat'),
          status: summarizeText(seat.resident_status ?? seat.status, 'idle'),
        })),
      }
    })

    const coveredTeamKeys = new Set(
      cards.flatMap((card) => card.seats.map((seat) => seat.key))
    )

    for (const seat of runtimeView.runtimeSeats) {
      const seatKey = String(seat.seat_id ?? seat.role_session_id ?? seat.role_id).trim()
      if (!seatKey || coveredTeamKeys.has(seatKey)) continue

      const teamKey = String(seat.team_id ?? seat.team_instance_id ?? 'unassigned').trim() || 'unassigned'
      let card = cards.find((item) => item.key === teamKey)
      if (!card) {
        card = {
          key: teamKey,
          label: humanizeId(teamKey, 'Unassigned Team'),
          managerLabel: 'Unassigned manager',
          status: summarizeText(seat.status, 'idle'),
          seatCount: 0,
          pendingApprovals: 0,
          seats: [],
        }
        cards.push(card)
      }

      const managerDigest = digestBySeatId.get(String(seat.seat_id ?? '').trim()) ?? {}
      card.pendingApprovals += asRecordList(managerDigest.pending_decisions).length
      if (!card.latestAlert) {
        card.latestAlert = summarizeText(seat.latest_notification?.subject ?? seat.latest_notification?.summary, '')
      }
      card.seats.push({
        key: seatKey,
        roleLabel: humanizeId(seat.role_id, 'Seat'),
        seatLabel: summarizeText(seat.seat_id ?? seat.role_session_id, 'Seat'),
        status: summarizeText(seat.resident_status ?? seat.status, 'idle'),
      })
      card.seatCount = card.seats.length
    }

    return cards.sort((left, right) => left.label.localeCompare(right.label))
  }, [runtimeView.runtimeTeams, runtimeView.runtimeSeats, seatDigests])

  const latestAlert = teamCards.map((team) => team.latestAlert).find(Boolean) || 'No current alerts'

  if (!projectRun?.run_id && teamCards.length === 0) {
    return null
  }

  return (
    <section className={`project-cockpit${embedded ? ' embedded' : ''}`} aria-label="Team">
      <div className="project-cockpit-overview">
        <div className="project-cockpit-title">
          <span className="project-cockpit-label">Team</span>
          <strong>{summarizeText(projectRun?.run_id, 'Runtime Team Status')}</strong>
          {onStopRun && (
            <button
              className="project-cockpit-stop-btn"
              onClick={onStopRun}
              title="Stop this run"
            >
              Stop
            </button>
          )}
        </div>
        <div className="project-cockpit-metrics">
          <span>{summarizeText(projectRun?.lifecycle_status, 'active')}</span>
          <span>Teams {teamCards.length}</span>
          <span>Seats {runtimeView.runtimeSeats.length}</span>
          <span>Approvals {pendingDecisionCount}</span>
          <span>Unread {unreadCount}</span>
          <span>Run state {summarizeText(asRecord(projectRun?.recovery_pointer).status, 'clean')}</span>
        </div>
      </div>

      <div className="project-cockpit-grid project-cockpit-grid--team">
        <section className="project-cockpit-panel">
          <header>
            <h3>Communication</h3>
            <span>{communicationItems.length}</span>
          </header>
          <div className="project-cockpit-summary">
            {communicationItems.map((item) => (
              <span key={item.label}>{item.label} {item.value}</span>
            ))}
            <div className="project-cockpit-inline-text">{latestAlert}</div>
          </div>
        </section>

        <section className="project-cockpit-panel project-cockpit-panel--wide">
          <header>
            <h3>Teams</h3>
            <span>{teamCards.length}</span>
          </header>
          <div className="project-cockpit-team-grid">
            {teamCards.map((team) => (
              <article key={team.key} className="project-cockpit-team-card">
                <div className="project-cockpit-team-head">
                  <div className="project-cockpit-team-title">
                    <strong>{team.label}</strong>
                    <span>{team.managerLabel}</span>
                  </div>
                  <span className={`project-cockpit-team-status status-${statusToken(team.status)}`}>
                    {team.status}
                  </span>
                </div>
                <div className="project-cockpit-team-meta">
                  <span>Seats {team.seatCount}</span>
                  <span>Approvals {team.pendingApprovals}</span>
                  {team.latestAlert && <span>{team.latestAlert}</span>}
                </div>
                <div className="project-cockpit-seat-list">
                  {team.seats.length > 0 ? team.seats.map((seat) => (
                    <div key={seat.key} className="project-cockpit-seat-row">
                      <span className="project-cockpit-seat-role">{seat.roleLabel}</span>
                      <span className="project-cockpit-seat-id">{seat.seatLabel}</span>
                      <span className={`project-cockpit-seat-status status-${statusToken(seat.status)}`}>
                        {seat.status}
                      </span>
                    </div>
                  )) : (
                    <div className="project-cockpit-inline-text">No seats assigned yet</div>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      </div>
    </section>
  )
}
