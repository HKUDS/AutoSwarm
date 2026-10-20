import type { KanbanTask, Session, WorkItemProgressEntry } from '../types/kanban'

type WorkItemCardLike = Pick<KanbanTask, 'id' | 'workItemId'>

type ExecutionTurnLike =
  | Pick<Session, 'taskId' | 'runtimeTaskId' | 'executionTurnId'>
  | Pick<WorkItemProgressEntry, 'runtimeTaskId' | 'executionTurnId'>

type LinkedRuntimeLike = Pick<KanbanTask, 'runtimeTaskId' | 'executionTurnId'>
  & Pick<KanbanTask, 'workItemId'>

function clean(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function getWorkItemCardId(card: WorkItemCardLike | null | undefined): string {
  return clean(card?.workItemId) || clean(card?.id)
}

export function getExecutionTurnId(turn: ExecutionTurnLike | null | undefined): string {
  if (!turn) return ''
  const raw = turn as Partial<Session & WorkItemProgressEntry>
  return (
    clean(raw.executionTurnId)
    || clean(raw.runtimeTaskId)
    || clean(raw.taskId)
  )
}

export function getLinkedRuntimeTaskId(card: LinkedRuntimeLike | null | undefined): string {
  return clean(card?.workItemId)
    ? clean(card?.executionTurnId) || clean(card?.runtimeTaskId)
    : ''
}
