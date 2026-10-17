import type { Session, TaskPreferredAgent } from '../types/kanban'

export type CanonicalSessionExecMode = 'task' | 'company' | 'org'
export type CanonicalCompanyProfile = 'corporate' | 'custom'

export function composerAgentForSession(session: Partial<Session>, fallback: TaskPreferredAgent): TaskPreferredAgent {
  // A role's runtime telemetry cannot change the committed primary chat agent.
  // Child views show that role's own execution agent instead.
  const child = session.mode === 'child' || !!session.parentSessionId
  return (child
    ? session.selectedExecutionAgent || session.preferredAgent
    : session.preferredAgent || session.selectedExecutionAgent) || fallback
}

export function normalizeSessionExecMode(value?: string | null): CanonicalSessionExecMode {
  const normalized = String(value ?? '').trim().toLowerCase()
  if (normalized === 'company') return 'company'
  if (normalized === 'org' || normalized === 'custom') return 'org'
  return 'task'
}

export function normalizeSessionCompanyProfile(value?: string | null): CanonicalCompanyProfile {
  return String(value ?? '').trim().toLowerCase() === 'custom' ? 'custom' : 'corporate'
}

export function canonicalizeSessionExecutionIdentity<T extends Partial<Session>>(session: T): T {
  const rawMode = String(session.execMode ?? '').trim().toLowerCase()
  const rawProfile = String(session.companyProfile ?? '').trim().toLowerCase()
  const rawOrgId = String(session.orgId ?? '').trim()
  const hasExplicitMode = rawMode.length > 0

  const execMode: CanonicalSessionExecMode = hasExplicitMode
    ? normalizeSessionExecMode(rawMode)
    : (rawProfile === 'custom' || rawOrgId ? 'org' : normalizeSessionExecMode(rawMode))

  if (execMode === 'org') {
    return {
      ...session,
      execMode: 'org',
      companyProfile: 'custom',
      orgId: rawOrgId || undefined,
    }
  }

  if (execMode === 'company') {
    return {
      ...session,
      execMode: 'company',
      companyProfile: 'corporate',
      orgId: undefined,
    }
  }

  return {
    ...session,
    execMode: 'task',
    companyProfile: undefined,
    orgId: undefined,
  }
}
