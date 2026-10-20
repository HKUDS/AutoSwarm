import type { AgentAnimStatus, Session } from '../types/kanban'

export const TERMINAL_SESSION_STATUSES = new Set(['done', 'failed', 'cancelled'])
const COMPANY_EXEC_MODES = new Set(['company', 'org', 'custom'])
const BOARD_STATUS_IDLE = new Set(['idle', 'done', 'failed', 'cancelled'])

export function getSessionRuntimeStatus(
  session: Pick<Session, 'status' | 'agentStatus' | 'runtimeControlState'>,
): AgentAnimStatus {
  if (TERMINAL_SESSION_STATUSES.has(session.status)) return 'idle'
  if (session.agentStatus === 'reflecting' || session.agentStatus === 'tool_active') {
    return session.agentStatus
  }
  if (
    session.runtimeControlState
    && session.runtimeControlState !== 'running'
    && session.runtimeControlState !== 'suspending'
    && session.runtimeControlState !== 'resuming'
  ) {
    return 'idle'
  }
  if (session.status === 'running') return 'reflecting'
  return 'idle'
}

export function isSessionWorking(
  session: Pick<Session, 'status' | 'agentStatus' | 'runtimeControlState'>,
): boolean {
  return getSessionRuntimeStatus(session) !== 'idle'
}

export function isCompanyRuntimeSession(session?: Partial<Session> | null): boolean {
  if (!session) return false
  const mode = String(session.execMode ?? '').trim().toLowerCase()
  const hasExplicitTaskMode = mode === 'task'
  return COMPANY_EXEC_MODES.has(mode)
    || !!session.isCompanyRuntime
    || !!session.parentSessionId
    || !!session.orgId
    || !!session.workItemProjectionId
    || !!session.roleWorkItems
    || !!session.executorRoleWorkItems
    || (!!session.companyProfile && !hasExplicitTaskMode)
}

export function companyRuntimeControlPatchForBoardStatus(
  session: Partial<Session> | undefined | null,
  status: string,
): Partial<Pick<Session, 'runtimeControlState' | 'canStop' | 'canResume'>> {
  if (!isCompanyRuntimeSession(session)) return {}

  const normalizedStatus = String(status ?? '').trim().toLowerCase()
  if (normalizedStatus === 'running') {
    return {
      runtimeControlState: 'running',
      canStop: true,
      canResume: false,
    }
  }

  if (BOARD_STATUS_IDLE.has(normalizedStatus)) {
    const existingState = session?.runtimeControlState
    if (existingState === 'suspending' || existingState === 'suspended') {
      return {}
    }
    return {
      runtimeControlState: 'idle',
      canStop: false,
      canResume: false,
    }
  }

  return {}
}
