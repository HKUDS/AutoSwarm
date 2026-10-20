import { DragDropContext, type DropResult } from '@hello-pangea/dnd'
import type { AgentInfo } from '../types/visual'
import type { KanbanColumn as KanbanColumnType, KanbanTask } from '../types/kanban'
import type { BoardStoreState } from './BoardStore'
import { KanbanColumn } from './KanbanColumn'

interface KanbanBoardViewProps {
  columns: KanbanColumnType[]
  tasksByColumn: Record<string, KanbanTask[]>
  agents: AgentInfo[]
  officeMap?: Record<string, string>
  store: BoardStoreState
  companyMode?: boolean
  selectedTaskId?: string | null
  onCardClick: (task: KanbanTask) => void
  onStartTask?: (taskId: string) => void
  onQuickCreate?: (title: string) => void
  onMoveTask?: (taskId: string, columnId: string) => void
}

export function KanbanBoardView({
  columns, tasksByColumn, agents, officeMap, store, companyMode, selectedTaskId, onCardClick, onStartTask, onQuickCreate, onMoveTask,
}: KanbanBoardViewProps) {

  const handleDragEnd = (result: DropResult) => {
    if (companyMode) return
    if (!result.destination) return

    const srcColId = result.source.droppableId
    const destColId = result.destination.droppableId

    if (srcColId !== destColId) {
      // All column transitions are automatic (driven by backend status).
      // No manual drag between columns.
      return
    }

    // Same-column reorder — compute new sort orders atomically
    const destIndex = result.destination.index
    const taskId = result.draggableId
    const ordered = [...(tasksByColumn[destColId] ?? [])].filter(t => t.id !== taskId)
    const draggedTask = (tasksByColumn[destColId] ?? []).find(t => t.id === taskId)
    if (draggedTask) ordered.splice(destIndex, 0, draggedTask)
    ordered.forEach((t, i) => {
      store.moveTask(t.id, destColId, i)
    })
  }

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className="kanban-board">
        {columns.map(col => (
          <KanbanColumn
            key={col.id}
            column={col}
            tasks={tasksByColumn[col.id] ?? []}
            agents={agents}
            officeMap={officeMap}
            companyMode={companyMode}
            selectedTaskId={selectedTaskId}
            onCardClick={onCardClick}
            onStartTask={!companyMode && col.name === 'Todo' ? onStartTask : undefined}
            onQuickCreate={!companyMode && col.name === 'Todo' ? onQuickCreate : undefined}
          />
        ))}
      </div>
    </DragDropContext>
  )
}
