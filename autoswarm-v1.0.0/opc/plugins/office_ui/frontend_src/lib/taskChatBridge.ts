import type { ChatStoreState } from '../chat/ChatStore'
import type { KanbanTask } from '../types/kanban'

export function notifyTaskAssigned(
  chatStore: ChatStoreState,
  task: KanbanTask,
  agentNames: string[],
  officeChannelId?: string,
) {
  const names = agentNames.join(', ')
  const channelId = officeChannelId ?? `session:${task.id}`
  const targetCh = chatStore.channels.find(ch => ch.id === channelId) ?? chatStore.channels.find(ch => ch.type === 'activity')
  if (!targetCh) return
  chatStore.sendMessage({
    channelId: targetCh.id,
    sender: 'system',
    senderName: 'System',
    content: `Task **${task.displayId}** assigned to ${names}`,
    metadata: { type: 'task_assigned', taskId: task.id, boardId: task.boardId },
  })
}
