import { useCallback, useMemo, useReducer, useRef, useState } from 'react'
import type { ChatChannel, ChatMessage } from '../types/chat'
import { stableMessageTimelineKey } from '../lib/messageTimelineIdentity'

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

const DUPLICATE_WINDOW_MS = 2000
const RESULT_SURFACE_PRIORITY: Record<string, number> = {
  child_task_result: 80,
  child_task_result_retry: 79,
  company_role_result: 75,
  company_role_result_retry: 74,
  child_result: 70,
  runtime_v2_assistant: 60,
  runtime_v2_company_assistant: 20,
  top_level_reply: 40,
  worker_notification: 10,
}

function messageMetadata(message: ChatMessage): Record<string, unknown> {
  return (message.metadata ?? {}) as Record<string, unknown>
}

function normalizeMessageFormatting(content: string): string {
  return String(content ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .map(line => line.trimEnd())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function normalizeMessageContent(content: string): string {
  const normalized = normalizeMessageFormatting(content)
  const titleStripped = stripNarrativeTitlePrefix(normalized)
  const paragraphs = titleStripped.split(/\n{2,}/).map(part => part.trim()).filter(Boolean)
  if (paragraphs.length > 1 && /^Verification:\s/i.test(paragraphs[paragraphs.length - 1])) {
    return paragraphs.slice(0, -1).join('\n\n').trim()
  }
  return titleStripped
}

function stripNarrativeTitlePrefix(content: string): string {
  let trimmed = String(content || '').trim()
  // Only an explicit, first-line "**Title**: body" wrapper is removable.
  // Searching for an arbitrary `: ` in the first N characters is destructive:
  // ordinary Markdown such as "**Work Item 1: ...**" and list fields such as
  // "- ID: ..." would be peeled one layer at a time on repeated syncs.
  for (;;) {
    const markdownTitle = trimmed.match(/^\*\*([^\r\n]{8,160}?)\*\*:(?:[ \t]+|\r?\n+)([\s\S]+)$/)
    if (!markdownTitle) return trimmed
    const body = markdownTitle[2].trim()
    if (body.length < 80 || body === trimmed) return trimmed
    trimmed = body
  }
}

function losslessIdentityContent(
  existing: ChatMessage,
  candidate: ChatMessage,
  preferred: ChatMessage,
  sharesConcreteIdentity: boolean,
): string {
  if (!sharesConcreteIdentity) return preferred.content

  const existingComparable = normalizeMessageFormatting(existing.content)
  const candidateComparable = normalizeMessageFormatting(candidate.content)
  if (!existingComparable || !candidateComparable || existingComparable === candidateComparable) {
    return preferred.content
  }

  // A cache/detail replay can contain a prefix-truncated copy of the same
  // persistent message. Preserve the lossless source regardless of arrival
  // order; unrelated edits still follow the normal preference rules.
  if (existingComparable.endsWith(candidateComparable)) return existing.content
  if (candidateComparable.endsWith(existingComparable)) return candidate.content
  return preferred.content
}

function messageIdentityKeys(message: ChatMessage): Set<string> {
  const metadata = messageMetadata(message)
  const keys = new Set<string>()
  const checkpointType = typeof metadata.checkpoint_type === 'string' ? metadata.checkpoint_type.trim() : ''
  const checkpointId = typeof metadata.checkpoint_id === 'string' ? metadata.checkpoint_id.trim() : ''
  for (const value of [
    message.id,
    typeof metadata.ui_message_id === 'string' ? metadata.ui_message_id : '',
    typeof metadata.result_delivery_id === 'string' && metadata.result_delivery_id.trim()
      ? `delivery:${metadata.result_delivery_id.trim()}`
      : '',
    checkpointType && checkpointId ? `checkpoint:${checkpointType}:${checkpointId}` : '',
  ]) {
    const normalized = String(value ?? '').trim()
    if (normalized) keys.add(normalized)
  }
  return keys
}

function scopedMessageIdentityKeys(message: ChatMessage): Set<string> {
  return new Set(
    [...messageIdentityKeys(message)].map(key => `${message.channelId}\u0000${key}`),
  )
}

function isDerivedIdentityKey(value: string): boolean {
  return value.startsWith('checkpoint:') || value.startsWith('delivery:')
}

function messageTimestamp(message: ChatMessage): number {
  return typeof message.timestamp === 'number' ? message.timestamp : 0
}

function isOptimisticMessage(message: ChatMessage): boolean {
  return String(message.id ?? '').startsWith('msg-')
}

function isPersistentMessage(message: ChatMessage): boolean {
  // sendMessage creates client-only optimistic rows with this prefix. Once the
  // backend acknowledges one, message deduplication replaces its identity with
  // the persistent ui_message_id / backend message id.
  return !isOptimisticMessage(message)
}

function latestPersistentMessageTimestamps(messages: ChatMessage[]): Record<string, number> {
  const latest: Record<string, number> = {}
  for (const message of messages) {
    if (!isPersistentMessage(message)) continue
    const timestamp = messageTimestamp(message)
    if (timestamp > (latest[message.channelId] ?? 0)) {
      latest[message.channelId] = timestamp
    }
  }
  return latest
}

function advanceReadTimestamp(
  state: Record<string, number>,
  channelId: string,
  timestamp: number,
): Record<string, number> {
  if (timestamp <= (state[channelId] ?? 0)) return state
  return { ...state, [channelId]: timestamp }
}

function unreadMessageCounts(
  messages: ChatMessage[],
  readTimestamps: Record<string, number>,
): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const message of messages) {
    if (!isPersistentMessage(message) || message.sender === 'user') continue
    const lastRead = readTimestamps[message.channelId] ?? 0
    if (messageTimestamp(message) <= lastRead) continue
    counts[message.channelId] = (counts[message.channelId] ?? 0) + 1
  }
  return counts
}

function messageRoleBucket(message: ChatMessage): 'user' | 'assistant' {
  const sender = String(message.sender ?? '').trim().toLowerCase()
  const metadata = messageMetadata(message)
  const role = typeof metadata.role === 'string' ? metadata.role.trim().toLowerCase() : ''
  if (sender === 'user' || role === 'user') return 'user'
  return 'assistant'
}

function messagePreferenceScore(message: ChatMessage): number {
  const metadata = messageMetadata(message)
  const sender = String(message.sender ?? '').trim().toLowerCase()
  let score = 0
  const resultPriority = resultSurfacePriority(message)
  if (resultPriority) score += 1000 + resultPriority
  if (metadata.source === 'engine') score += 100
  if (sender && sender !== 'system') score += 20
  if (sender && !['assistant', 'system', 'user'].includes(sender)) score += 5
  if (message.replyToId) score += 2
  score += Math.min(Object.keys(metadata).length, 10)
  return score
}

function messageHasEngineSource(message: ChatMessage): boolean {
  return String(messageMetadata(message).source ?? '').trim().toLowerCase() === 'engine'
}

function resultSurfacePriority(message: ChatMessage): number {
  const metadata = messageMetadata(message)
  const transcriptKind = String(metadata.transcript_kind ?? '').trim()
  if (transcriptKind) return RESULT_SURFACE_PRIORITY[transcriptKind] ?? 0
  const kind = String(metadata.kind ?? '').trim()
  return RESULT_SURFACE_PRIORITY[kind] ?? 0
}

function isResultSurface(message: ChatMessage): boolean {
  return resultSurfacePriority(message) > 0
}

function messagesShareIdentity(existing: ChatMessage, candidate: ChatMessage): boolean {
  if (existing.channelId !== candidate.channelId) return false
  const existingIds = messageIdentityKeys(existing)
  const candidateIds = messageIdentityKeys(candidate)
  for (const id of existingIds) {
    if (candidateIds.has(id)) return true
  }
  return false
}

function messagesSemanticallyMatch(existing: ChatMessage, candidate: ChatMessage): boolean {
  if (messagesShareIdentity(existing, candidate)) return true
  if (existing.channelId !== candidate.channelId) return false
  if (messageRoleBucket(existing) !== messageRoleBucket(candidate)) return false
  if (normalizeMessageContent(existing.content) !== normalizeMessageContent(candidate.content)) return false
  const bothResultSurfaces = isResultSurface(existing) && isResultSurface(candidate)
  if (!bothResultSurfaces && String(existing.replyToId ?? '') !== String(candidate.replyToId ?? '')) return false
  if (!(messageHasEngineSource(existing) || messageHasEngineSource(candidate))) return false

  const existingTs = messageTimestamp(existing)
  const candidateTs = messageTimestamp(candidate)
  if (!bothResultSurfaces && existingTs && candidateTs && Math.abs(existingTs - candidateTs) > DUPLICATE_WINDOW_MS) {
    return false
  }
  return true
}

function mergeDuplicateMessages(
  existing: ChatMessage,
  candidate: ChatMessage,
  preferCandidate = false,
): ChatMessage {
  let preferred = existing
  let secondary = candidate

  const existingOptimistic = isOptimisticMessage(existing)
  const candidateOptimistic = isOptimisticMessage(candidate)
  if (existingOptimistic !== candidateOptimistic) {
    preferred = existingOptimistic ? candidate : existing
    secondary = existingOptimistic ? existing : candidate
  } else if (preferCandidate) {
    preferred = candidate
    secondary = existing
  } else if (messagePreferenceScore(candidate) > messagePreferenceScore(existing)) {
    preferred = candidate
    secondary = existing
  }

  const mentions: string[] = []
  for (const values of [secondary.mentions, preferred.mentions]) {
    for (const value of values ?? []) {
      if (!mentions.includes(value)) mentions.push(value)
    }
  }

  const existingIds = messageIdentityKeys(existing)
  const candidateIds = messageIdentityKeys(candidate)
  let sharesConcreteIdentity = false
  let canonicalId = ''
  for (const id of existingIds) {
    if (!candidateIds.has(id)) continue
    if (!isDerivedIdentityKey(id)) {
      sharesConcreteIdentity = true
      if (!canonicalId) canonicalId = id
    }
  }

  const content = losslessIdentityContent(existing, candidate, preferred, sharesConcreteIdentity)

  const existingCheckpointId = String(messageMetadata(existing).checkpoint_id ?? '').trim()
  const candidateCheckpointId = String(messageMetadata(candidate).checkpoint_id ?? '').trim()
  const preservesCheckpointPosition = !!existingCheckpointId && existingCheckpointId === candidateCheckpointId
  const replacesResultSurface = isResultSurface(existing) && isResultSurface(candidate)
  const retainedTimelineId = replacesResultSurface
    ? String(
      messageMetadata(existing).ui_timeline_id
      ?? messageMetadata(candidate).ui_timeline_id
      ?? stableMessageTimelineKey(existing),
    ).trim()
    : ''
  const mergedMetadata = { ...messageMetadata(secondary), ...messageMetadata(preferred) }
  if (retainedTimelineId) mergedMetadata.ui_timeline_id = retainedTimelineId

  return {
    ...secondary,
    ...preferred,
    ...(canonicalId ? { id: canonicalId } : {}),
    content,
    metadata: mergedMetadata,
    mentions,
    timestamp: preservesCheckpointPosition || replacesResultSurface
      ? messageTimestamp(existing) || messageTimestamp(candidate)
      : messageTimestamp(preferred) || messageTimestamp(secondary),
  }
}

function dedupeMessages(messages: ChatMessage[]): ChatMessage[] {
  const deduped: ChatMessage[] = []
  // Map from identity key → index in deduped for O(1) identity lookups
  const identityKeyToIdx = new Map<string, number>()

  for (const message of [...messages].sort((a, b) => messageTimestamp(a) - messageTimestamp(b))) {
    const candidateIds = scopedMessageIdentityKeys(message)
    let matchIndex = -1
    let preferCandidate = false

    // O(1) identity lookup via Map instead of O(n) backward scan
    for (const id of candidateIds) {
      const idx = identityKeyToIdx.get(id)
      if (idx !== undefined) {
        matchIndex = idx
        preferCandidate = deduped[idx].id === message.id
        break
      }
    }

    // Semantic match: scan backwards with early-exit when only short-window matches remain.
    if (matchIndex === -1) {
      const candidateTs = messageTimestamp(message)
      const candidateIsResultSurface = isResultSurface(message)
      for (let i = deduped.length - 1; i >= 0; i--) {
        const existingTs = messageTimestamp(deduped[i])
        if (
          !candidateIsResultSurface
          && candidateTs > 0
          && existingTs > 0
          && candidateTs - existingTs > DUPLICATE_WINDOW_MS
        ) break
        if (messagesSemanticallyMatch(deduped[i], message)) {
          matchIndex = i
          break
        }
      }
    }

    const insertIdx = matchIndex === -1 ? deduped.length : matchIndex
    if (matchIndex === -1) {
      deduped.push(message)
    } else {
      deduped[matchIndex] = mergeDuplicateMessages(deduped[matchIndex], message, preferCandidate)
    }

    // Register all identity keys for the merged/inserted message for fast future lookups
    for (const id of scopedMessageIdentityKeys(deduped[insertIdx])) {
      if (!identityKeyToIdx.has(id)) identityKeyToIdx.set(id, insertIdx)
    }
  }

  return deduped
}

function mergeMessagesIntoExisting(
  state: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const merged = [...state]
  const identityKeyToIdx = new Map<string, number>()
  merged.forEach((message, index) => {
    for (const key of scopedMessageIdentityKeys(message)) identityKeyToIdx.set(key, index)
  })

  for (const candidate of [...incoming].sort((a, b) => messageTimestamp(a) - messageTimestamp(b))) {
    let matchIndex = -1
    for (const key of scopedMessageIdentityKeys(candidate)) {
      const index = identityKeyToIdx.get(key)
      if (index !== undefined) {
        matchIndex = index
        break
      }
    }
    if (matchIndex < 0) {
      for (let index = merged.length - 1; index >= 0; index -= 1) {
        if (messagesSemanticallyMatch(merged[index], candidate)) {
          matchIndex = index
          break
        }
      }
    }

    if (matchIndex < 0) {
      matchIndex = merged.length
      merged.push(candidate)
    } else {
      const mounted = merged[matchIndex]
      merged[matchIndex] = mergeDuplicateMessages(
        mounted,
        candidate,
        mounted.id === candidate.id,
      )
    }

    for (const key of scopedMessageIdentityKeys(candidate)) identityKeyToIdx.set(key, matchIndex)
    for (const key of scopedMessageIdentityKeys(merged[matchIndex])) identityKeyToIdx.set(key, matchIndex)
  }

  return merged.sort((a, b) => (
    messageTimestamp(a) === messageTimestamp(b)
      ? a.id.localeCompare(b.id)
      : messageTimestamp(a) - messageTimestamp(b)
  ))
}

export const __chatStoreTestUtils = {
  advanceReadTimestamp,
  dedupeMessages,
  latestPersistentMessageTimestamps,
  mergeMessagesIntoExisting,
  unreadMessageCounts,
}

type ChannelAction =
  | { type: 'SET'; channels: ChatChannel[] }
  | { type: 'ADD'; channel: ChatChannel }
  | { type: 'REMOVE'; channelId: string }
  | { type: 'REMOVE_PARTICIPANT'; agentId: string }
  | { type: 'CLEAR' }

function channelReducer(state: ChatChannel[], action: ChannelAction): ChatChannel[] {
  switch (action.type) {
    case 'SET': return action.channels
    case 'CLEAR': return []
    case 'ADD': return state.some(ch => ch.id === action.channel.id) ? state : [...state, action.channel]
    case 'REMOVE': return state.filter(ch => ch.id !== action.channelId)
    case 'REMOVE_PARTICIPANT': return state.map(ch => ({
      ...ch,
      participants: ch.participants.filter(p => p !== action.agentId),
    }))
    default: return state
  }
}

type MessageAction =
  | { type: 'SET'; messages: ChatMessage[] }
  | { type: 'ADD'; message: ChatMessage }
  | { type: 'MERGE'; messages: ChatMessage[] }
  | { type: 'MARK_SENDER_DELETED'; senderId: string }
  | { type: 'REMOVE_BY_CHANNEL'; channelId: string }
  | { type: 'REMOVE_BY_TASK_ID'; taskId: string }
  | { type: 'CLEAR' }

function messageReducer(state: ChatMessage[], action: MessageAction): ChatMessage[] {
  switch (action.type) {
    case 'SET': {
      // Backend snapshots (collab_sync / collab_sync_push) arrive frequently
      // while agents are running. A naive replace drops any client-side
      // optimistic message (id prefixed `msg-` from sendMessage) that the
      // backend has not round-tripped yet, which causes the composer's sent
      // text to flicker in and out and user input lines to disappear for
      // a beat. Preserve those local-only messages here until the backend
      // snapshot catches up.
      const incoming = dedupeMessages(action.messages)
      if (state.length === 0) return incoming
      const localOnly = state.filter(existing =>
        typeof existing.id === 'string' &&
        existing.id.startsWith('msg-') &&
        !incoming.some(inc => messagesSemanticallyMatch(existing, inc))
      )
      if (localOnly.length === 0) return incoming
      return dedupeMessages([...incoming, ...localOnly])
    }
    case 'CLEAR':
      return []
    case 'ADD': {
      // Fast path: only scan recent messages within the dedup window — avoids O(n²) full dedup
      // for the common case of a single new message arriving from the WebSocket.
      // Identity matches (same message_id) are checked across the full list so that
      // metadata-only updates (e.g. checkpoint_status changes) always merge in-place
      // regardless of how old the original message is.
      const candidateTs = messageTimestamp(action.message)
      const candidateIds = messageIdentityKeys(action.message)
      let pastWindow = false
      for (let i = state.length - 1; i >= 0; i--) {
        const existingTs = messageTimestamp(state[i])
        if (!pastWindow && candidateTs > 0 && existingTs > 0 && candidateTs - existingTs > DUPLICATE_WINDOW_MS) {
          pastWindow = true
        }
        if (messagesShareIdentity(state[i], action.message)) {
          const updated = [...state]
          updated[i] = mergeDuplicateMessages(state[i], action.message, state[i].id === action.message.id)
          return updated
        }
        if ((isResultSurface(action.message) || !pastWindow) && messagesSemanticallyMatch(state[i], action.message)) {
          const updated = [...state]
          updated[i] = mergeDuplicateMessages(state[i], action.message)
          return updated
        }
      }
      return [...state, action.message]
    }
    case 'MERGE': {
      if (action.messages.length === 0) return state
      return mergeMessagesIntoExisting(state, action.messages)
    }
    case 'MARK_SENDER_DELETED': return state.map(m =>
      m.sender === action.senderId ? { ...m, senderDeleted: true, senderName: '[已删除的 Agent]' } : m
    )
    case 'REMOVE_BY_CHANNEL': return state.filter(m => m.channelId !== action.channelId)
    case 'REMOVE_BY_TASK_ID': return state.filter(m =>
      m.channelId !== `session:${action.taskId}` &&
      !((m.metadata as Record<string, unknown>)?.task_id === action.taskId)
    )
    default: return state
  }
}

export interface ChatStoreState {
  scopeProjectId: string
  channels: ChatChannel[]
  messages: ChatMessage[]
  sendMessage: (opts: { channelId: string; sender: string; senderName: string; content: string; replyToId?: string; metadata?: ChatMessage['metadata'] }) => ChatMessage
  getChannelMessages: (channelId: string) => ChatMessage[]
  getUnreadCount: (channelId: string) => number
  markRead: (channelId: string) => void
  markSenderDeleted: (agentId: string) => void
  removeParticipant: (agentId: string) => void
  removeSessionData: (taskId: string) => void
  clear: () => void
  initFromBackend: (projectId: string, channels: ChatChannel[], messages: ChatMessage[]) => void
  addMessageFromBackend: (msg: ChatMessage) => void
  mergeMessagesFromBackend: (messages: ChatMessage[]) => void
  addChannelFromBackend: (ch: ChatChannel) => void
}

export function useChatStore(): ChatStoreState {
  const [channels, dispatchCh] = useReducer(channelReducer, [])
  const [messages, dispatchMsg] = useReducer(messageReducer, [])
  const [readTimestamps, setReadTimestamps] = useState<Record<string, number>>({})
  const [scopeProjectId, setScopeProjectId] = useState<string>('default')
  const readBaselineProjectRef = useRef<string | null>(null)

  const latestPersistentTimestamps = useMemo(
    () => latestPersistentMessageTimestamps(messages),
    [messages],
  )
  const latestPersistentTimestampsRef = useRef(latestPersistentTimestamps)
  latestPersistentTimestampsRef.current = latestPersistentTimestamps

  const messagesByChannel = useMemo<Record<string, ChatMessage[]>>(() => {
    const buckets: Record<string, ChatMessage[]> = {}
    for (const message of messages) {
      if (!buckets[message.channelId]) buckets[message.channelId] = []
      buckets[message.channelId].push(message)
    }
    return buckets
  }, [messages])

  const unreadCounts = useMemo(
    () => unreadMessageCounts(messages, readTimestamps),
    [messages, readTimestamps],
  )

  const sendMessage = useCallback((opts: {
    channelId: string; sender: string; senderName: string; content: string;
    replyToId?: string; metadata?: ChatMessage['metadata']
  }) => {
    const msg: ChatMessage = {
      id: `msg-${uid()}`,
      channelId: opts.channelId,
      sender: opts.sender,
      senderName: opts.senderName,
      content: opts.content,
      timestamp: Date.now(),
      replyToId: opts.replyToId,
      mentions: [],
      metadata: opts.metadata,
    }
    dispatchMsg({ type: 'ADD', message: msg })
    return msg
  }, [])

  const getChannelMessages = useCallback((channelId: string) => {
    return messagesByChannel[channelId] ?? []
  }, [messagesByChannel])

  const getUnreadCount = useCallback((channelId: string) => {
    return unreadCounts[channelId] ?? 0
  }, [unreadCounts])

  const markRead = useCallback((channelId: string) => {
    const latestTimestamp = latestPersistentTimestampsRef.current[channelId] ?? 0
    if (latestTimestamp <= 0) return
    setReadTimestamps(prev => advanceReadTimestamp(prev, channelId, latestTimestamp))
  }, [])

  const markSenderDeleted = useCallback((agentId: string) => {
    dispatchMsg({ type: 'MARK_SENDER_DELETED', senderId: agentId })
  }, [])

  const removeParticipant = useCallback((agentId: string) => {
    dispatchCh({ type: 'REMOVE_PARTICIPANT', agentId })
  }, [])

  const removeSessionData = useCallback((taskId: string) => {
    dispatchCh({ type: 'REMOVE', channelId: `session:${taskId}` })
    dispatchMsg({ type: 'REMOVE_BY_TASK_ID', taskId })
  }, [])

  const clear = useCallback(() => {
    dispatchCh({ type: 'CLEAR' })
    dispatchMsg({ type: 'CLEAR' })
    readBaselineProjectRef.current = null
    setReadTimestamps({})
  }, [])

  const initFromBackend = useCallback((projectId: string, chs: ChatChannel[], msgs: ChatMessage[]) => {
    const nextProjectId = projectId || 'default'
    const projectChanged = nextProjectId !== scopeProjectId
    const shouldResetReadBaseline = readBaselineProjectRef.current !== nextProjectId
    readBaselineProjectRef.current = nextProjectId
    setScopeProjectId(nextProjectId)
    dispatchCh({ type: 'SET', channels: chs })
    // Backend `collab_sync` / `collab_sync_push` payloads carry the
    // "current window" of messages, not the full history. Dispatching
    // SET here would drop any older messages that were loaded earlier
    // via `session_detail` (limit: 200) — the very first user-typed
    // project-goal message sits at the top of the channel and is the
    // first to fall out of this window. Every subsequent push would
    // then wipe it, and the next `session_detail` refresh would merge
    // it back, producing a ~1s flicker cycle on the top of the list.
    // MERGE instead so the snapshot is additive, not destructive.
    if (projectChanged) {
      dispatchMsg({ type: 'SET', messages: msgs })
    } else {
      dispatchMsg({ type: 'MERGE', messages: msgs })
    }
    // Establish one read baseline when entering a project. Repeated full-sync
    // payloads must not advance it behind the viewport controller's back.
    if (shouldResetReadBaseline) {
      setReadTimestamps(latestPersistentMessageTimestamps(msgs))
    }
  }, [scopeProjectId])

  const addMessageFromBackend = useCallback((msg: ChatMessage) => {
    dispatchMsg({ type: 'ADD', message: msg })
  }, [])

  const mergeMessagesFromBackend = useCallback((msgs: ChatMessage[]) => {
    dispatchMsg({ type: 'MERGE', messages: msgs })
  }, [])

  const addChannelFromBackend = useCallback((ch: ChatChannel) => {
    dispatchCh({ type: 'ADD', channel: ch })
  }, [])

  return useMemo(() => ({
    scopeProjectId, channels, messages,
    sendMessage, getChannelMessages, getUnreadCount, markRead,
    markSenderDeleted, removeParticipant, removeSessionData, clear, initFromBackend,
    addMessageFromBackend, mergeMessagesFromBackend, addChannelFromBackend,
  }), [scopeProjectId, channels, messages, sendMessage, getChannelMessages, getUnreadCount, markRead,
       markSenderDeleted, removeParticipant, removeSessionData, clear, initFromBackend,
       addMessageFromBackend, mergeMessagesFromBackend, addChannelFromBackend])
}
