import type { ChatMessage } from '../types/chat'

/** Channel id that buckets a session's chat messages. */
export function sessionChannelId(taskId: string): string {
  return `session:${taskId}`
}

const RECRUITMENT_CHECKPOINT_TYPES = new Set([
  'company_recruitment_confirmation',
  'company_staffing_selection',
])

/**
 * Map `role_id -> recruited person/template display names` for a single chat
 * session, derived from that session's latest recruitment checkpoint message.
 *
 * This is display-only plumbing: org_info / `data.employees` is global (the
 * project's active run / accumulated org config), so it cannot represent the
 * recruitment of an arbitrary *selected* session. The recruitment a user
 * confirmed in a session is persisted as a chat checkpoint message, which is
 * the only per-session source the frontend already has.
 *
 * Returns `null` when the session has no recruitment checkpoint loaded, so the
 * caller can fall back to the global employee list (previous behaviour).
 */
export function extractSessionRecruitmentByRole(
  messages: ChatMessage[],
): Record<string, string[]> | null {
  let latest: ChatMessage | null = null
  for (const m of messages) {
    const ct = m.metadata?.checkpoint_type
    if (!ct || !RECRUITMENT_CHECKPOINT_TYPES.has(ct)) continue
    if (!latest || (m.timestamp ?? 0) >= (latest.timestamp ?? 0)) latest = m
  }
  if (!latest?.metadata) return null

  const meta = latest.metadata
  const map: Record<string, string[]> = {}
  const push = (roleId: unknown, name: unknown) => {
    const r = String(roleId ?? '').trim()
    const n = String(name ?? '').trim()
    if (!r || !n) return
    const arr = map[r] ?? (map[r] = [])
    if (!arr.includes(n)) arr.push(n)
  }

  // Preferred: recruitment_rationales is already a flat per-role display label
  // (this is exactly the "(role_id, role, recruited name, reason)" list the
  // user sees in the recruitment confirmation panel).
  for (const r of meta.recruitment_rationales ?? []) {
    if (r?.selection_label) push(r.role_id, r.selection_label)
  }
  // Fallback: derive a name from the structured proposals when rationales are
  // absent (older payloads / staffing-only checkpoints).
  if (Object.keys(map).length === 0) {
    for (const p of meta.proposals ?? []) {
      const name =
        p?.existing_employee?.employee_name ||
        p?.candidate?.proposed_name ||
        p?.candidate?.template_name ||
        ''
      push(p?.role_id, name)
    }
  }

  return Object.keys(map).length ? map : null
}
