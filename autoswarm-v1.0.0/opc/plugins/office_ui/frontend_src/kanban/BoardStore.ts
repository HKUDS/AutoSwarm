import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import type { KanbanBoard, KanbanColumn, KanbanTask, TaskPriority } from '../types/kanban'
import { deriveColumnFromPhase } from '../lib/phaseHelpers'

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

type BoardAction =
  | { type: 'SET'; boards: KanbanBoard[] }
  | { type: 'UPDATE_NAME'; boardId: string; name: string }

function boardReducer(state: KanbanBoard[], action: BoardAction): KanbanBoard[] {
  switch (action.type) {
    case 'SET': return action.boards
    case 'UPDATE_NAME':
      return state.map(b => b.id === action.boardId ? { ...b, name: action.name } : b)
    default: return state
  }
}

type ColumnAction =
  | { type: 'SET'; columns: KanbanColumn[] }

function columnReducer(state: KanbanColumn[], action: ColumnAction): KanbanColumn[] {
  switch (action.type) {
    case 'SET': return action.columns
    default: return state
  }
}

type TaskAction =
  | { type: 'SET'; tasks: KanbanTask[] }
  | { type: 'ADD'; task: KanbanTask }
  | { type: 'UPDATE'; id: string; partial: Partial<KanbanTask> }
  | { type: 'DELETE'; id: string }
  | { type: 'MOVE'; id: string; columnId: string; sortOrder: number }
  | { type: 'ASSIGN'; taskId: string; agentIds: string[] }
  | { type: 'REMOVE_ASSIGNEE'; agentId: string }

function taskReducer(state: KanbanTask[], action: TaskAction): KanbanTask[] {
  const now = Date.now()
  switch (action.type) {
    case 'SET': return action.tasks
    case 'ADD': return state.some(t => t.id === action.task.id) ? state : [...state, action.task]
    case 'UPDATE': return state.map(t => t.id === action.id ? { ...t, ...action.partial, updatedAt: now } : t)
    case 'DELETE': return state.filter(t => t.id !== action.id)
    case 'MOVE': return state.map(t => t.id === action.id ? { ...t, columnId: action.columnId, sortOrder: action.sortOrder, updatedAt: now } : t)
    case 'ASSIGN': return state.map(t => t.id === action.taskId ? { ...t, assigneeIds: action.agentIds, updatedAt: now } : t)
    case 'REMOVE_ASSIGNEE': return state.map(t =>
      t.assigneeIds.includes(action.agentId)
        ? { ...t, assigneeIds: t.assigneeIds.filter(a => a !== action.agentId), updatedAt: now }
        : t
    )
    default: return state
  }
}

export interface BoardStoreState {
  scopeProjectId: string
  boards: KanbanBoard[]
  columns: KanbanColumn[]
  tasks: KanbanTask[]
  activeBoardId: string | null
  activeBoard: KanbanBoard | null
  activeBoardColumns: KanbanColumn[]
  tasksByColumn: Record<string, KanbanTask[]>
  setActiveBoard: (boardId: string | null) => void

  createTask: (opts: { boardId: string; columnId: string; title: string; description?: string; priority?: TaskPriority | null; assigneeIds?: string[]; tags?: string[]; taskId?: string; displayId?: string }) => KanbanTask
  updateTask: (id: string, partial: Partial<KanbanTask>) => void
  deleteTask: (id: string) => void
  moveTask: (id: string, columnId: string, sortOrder: number) => void
  assignTask: (taskId: string, agentIds: string[]) => void

  dispatchTask: (action: TaskAction) => void
  getOpenTaskCount: () => number
  removeAssignee: (agentId: string) => void
  initFromBackend: (
    projectId: string,
    boards: KanbanBoard[],
    columns: KanbanColumn[],
    tasks: KanbanTask[],
    options?: { preserveTasksWhenIncomingEmpty?: boolean },
  ) => void
  updateBoardName: (boardId: string, name: string) => void
}

export function useBoardStore(): BoardStoreState {
  const [boards, dispatchBoard] = useReducer(boardReducer, [])
  const [columns, dispatchCol] = useReducer(columnReducer, [])
  const [tasks, dispatchTask] = useReducer(taskReducer, [])
  const [activeBoardId, setActiveBoardId] = useState<string | null>(null)
  const [scopeProjectId, setScopeProjectId] = useState<string>('default')

  // NOTE: no auto-select logic here.  Board selection is driven entirely by
  // the parent (WorkspacePage) which knows the execution mode:
  //   - Non-company mode → 1 project board, parent sets it once.
  //   - Company mode     → 1 board per session, parent syncs to activeSession.
  // Having BoardStore auto-select to boards[0] would race with the parent's
  // session-driven clear, producing a render loop (screen flicker).

  const activeBoard = useMemo(() => boards.find(b => b.id === activeBoardId) ?? null, [boards, activeBoardId])

  const activeBoardColumns = useMemo(() =>
    columns.filter(c => c.boardId === activeBoardId).sort((a, b) => a.sortOrder - b.sortOrder),
    [columns, activeBoardId]
  )

  // All tasks for active board, sorted by column.
  //
  // Column placement: prefer deriving from `phase` (the authoritative
  // single-source-of-truth field from the backend) and fall back to the
  // backend-supplied `columnId` only when phase is missing. During the
  // transition window both fields should agree — in dev mode we warn
  // loudly when they don't so the drift is caught immediately.
  const tasksByColumn = useMemo(() => {
    const boardTasks = tasks.filter(t => t.boardId === activeBoardId)
    const map: Record<string, KanbanTask[]> = {}
    for (const col of activeBoardColumns) map[col.id] = []
    for (const t of boardTasks) {
      const derived = t.phase ? deriveColumnFromPhase(t.phase) : t.columnId
      if (import.meta.env.DEV && t.phase && t.columnId && derived !== t.columnId) {
        // eslint-disable-next-line no-console
        console.warn(
          `[phase/columnId drift] task=${t.id} phase=${t.phase} derived=${derived} backendColumn=${t.columnId}`,
        )
      }
      if (map[derived]) map[derived].push(t)
    }
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => a.sortOrder - b.sortOrder)
    }
    return map
  }, [tasks, activeBoardId, activeBoardColumns])

  const createTask = useCallback((opts: {
    boardId: string; columnId: string; title: string;
    description?: string; priority?: TaskPriority | null;
    assigneeIds?: string[]; tags?: string[];
    taskId?: string; displayId?: string
  }) => {
    const board = boards.find(b => b.id === opts.boardId)
    const num = board?.nextTaskNum ?? 1
    const prefix = board?.prefix ?? 'T'
    const task: KanbanTask = {
      id: opts.taskId ?? `task-${uid()}`,
      displayId: opts.displayId ?? `${prefix}-${String(num).padStart(3, '0')}`,
      boardId: opts.boardId,
      columnId: opts.columnId,
      title: opts.title,
      description: opts.description,
      priority: opts.priority ?? null,
      assigneeIds: opts.assigneeIds ?? [],
      tags: opts.tags ?? [],
      sortOrder: num,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    dispatchTask({ type: 'ADD', task })
    return task
  }, [boards])

  const updateTask = useCallback((id: string, partial: Partial<KanbanTask>) => dispatchTask({ type: 'UPDATE', id, partial }), [])
  const deleteTask = useCallback((id: string) => dispatchTask({ type: 'DELETE', id }), [])
  const moveTask = useCallback((id: string, columnId: string, sortOrder: number) => dispatchTask({ type: 'MOVE', id, columnId, sortOrder }), [])
  const assignTask = useCallback((taskId: string, agentIds: string[]) => dispatchTask({ type: 'ASSIGN', taskId, agentIds }), [])

  const getOpenTaskCount = useCallback(() => {
    const terminalColIds = new Set(columns.filter(c => c.isTerminal).map(c => c.id))
    return tasks.filter(t => !terminalColIds.has(t.columnId)).length
  }, [tasks, columns])

  const removeAssignee = useCallback((agentId: string) => {
    dispatchTask({ type: 'REMOVE_ASSIGNEE', agentId })
  }, [])

  const initFromBackend = useCallback((
    projectId: string,
    bds: KanbanBoard[],
    cols: KanbanColumn[],
    tks: KanbanTask[],
    options?: { preserveTasksWhenIncomingEmpty?: boolean },
  ) => {
    const nextProjectId = projectId || 'default'
    const projectChanged = nextProjectId !== scopeProjectId
    const shouldPreserveTasks =
      !projectChanged
      && !!options?.preserveTasksWhenIncomingEmpty
      && tks.length === 0
    setScopeProjectId(nextProjectId)
    dispatchBoard({ type: 'SET', boards: bds })
    dispatchCol({ type: 'SET', columns: cols })
    dispatchTask({ type: 'SET', tasks: shouldPreserveTasks ? tasks : tks })
    // Only reset activeBoardId when the current selection no longer exists.
    // Otherwise preserve the parent's choice.  Never auto-default to boards[0]
    // here — the parent decides based on execution mode.
    setActiveBoardId(prev => (!projectChanged && prev && bds.some(b => b.id === prev) ? prev : null))
  }, [scopeProjectId, tasks])

  const setActiveBoard = useCallback((boardId: string | null) => {
    setActiveBoardId(boardId)
  }, [])

  const updateBoardName = useCallback((boardId: string, name: string) => {
    dispatchBoard({ type: 'UPDATE_NAME', boardId, name })
  }, [])

  return useMemo(() => ({
    scopeProjectId, boards, columns, tasks, activeBoardId, activeBoard, activeBoardColumns,
    tasksByColumn,
    setActiveBoard,
    createTask, updateTask, deleteTask, moveTask, assignTask,
    dispatchTask, getOpenTaskCount,
    removeAssignee, initFromBackend, updateBoardName,
  }), [
    scopeProjectId, boards, columns, tasks, activeBoardId, activeBoard, activeBoardColumns,
    tasksByColumn,
    setActiveBoard,
    createTask, updateTask, deleteTask, moveTask, assignTask,
    dispatchTask, getOpenTaskCount,
    removeAssignee, initFromBackend, updateBoardName,
  ])
}
