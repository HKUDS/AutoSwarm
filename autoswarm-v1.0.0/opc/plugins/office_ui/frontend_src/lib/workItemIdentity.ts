import type { EmployeeAssignment } from '../types/kanban'

type WorkItemIdentity = {
  workItemRoleId?: string
  workItemRoleName?: string
  employeeAssignment?: EmployeeAssignment
}

export function humanizeWorkItemRoleId(value?: string): string {
  const normalized = (value ?? '').trim()
  if (!normalized) return ''
  return normalized
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

export function getWorkItemRoleLabel(identity: WorkItemIdentity): string {
  const explicit = (identity.workItemRoleName ?? '').trim()
  if (explicit) return explicit
  return humanizeWorkItemRoleId(identity.workItemRoleId)
}

export function getWorkItemEmployeeLabel(identity: WorkItemIdentity): string {
  return (identity.employeeAssignment?.name ?? '').trim()
}

export function getWorkItemAssignmentLabel(identity: WorkItemIdentity): string {
  const role = getWorkItemRoleLabel(identity)
  const employee = getWorkItemEmployeeLabel(identity)
  if (role && employee) return `${role} · ${employee}`
  return role || employee
}
