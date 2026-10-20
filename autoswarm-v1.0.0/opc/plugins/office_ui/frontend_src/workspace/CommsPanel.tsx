import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  CommsStatePayload,
  CommsMessagePayload,
  CommsRolePayload,
  CommsMessageItem,
} from '../lib/wsClient'

interface CommsPanelProps {
  state: CommsStatePayload | null
  message: CommsMessagePayload | null
  onRefresh: () => void
  onReadMessage: (path: string) => void
  pollIntervalMs?: number
  /** When true, renders without its own border/header (embedded in ContextPanel tab). */
  embedded?: boolean
}

export function CommsPanel({
  state,
  message,
  onRefresh,
  onReadMessage,
  // Freshness is driven by the server's comms_state_dirty push (wsClient
  // refetches immediately on it); this poll is only a slow safety net. Each
  // tick makes the backend walk every role's comms workspace, so keep it rare.
  pollIntervalMs = 30000,
  embedded = false,
}: CommsPanelProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  // Auto-refresh fallback. onRefresh goes through a ref so the interval
  // always calls the latest callback (the old closure pinned a stale one).
  const onRefreshRef = useRef(onRefresh)
  onRefreshRef.current = onRefresh
  useEffect(() => {
    if (!pollIntervalMs) return
    onRefreshRef.current()
    const id = window.setInterval(() => onRefreshRef.current(), pollIntervalMs)
    return () => window.clearInterval(id)
  }, [pollIntervalMs])

  const totalUnread = useMemo(() => {
    if (!state?.roles) return 0
    return state.roles.reduce((acc, r) => acc + r.unread_count, 0)
  }, [state?.roles])

  const handleSelectMessage = (path: string) => {
    setSelectedPath(path)
    onReadMessage(path)
  }

  const wrapStyle: React.CSSProperties = embedded
    ? { fontSize: 13, color: 'var(--text)' }
    : {
        border: '1px solid var(--border, #333)',
        borderRadius: 6,
        background: 'var(--bg-primary, #1e1e1e)',
        fontSize: 13,
      }

  return (
    <div className="comms-panel" style={wrapStyle}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          borderBottom: '1px solid var(--border, #333)',
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>
          Agent Communications
        </span>
        {totalUnread > 0 && <Badge color="var(--accent, #3498db)">{totalUnread} unread</Badge>}
        <button
          onClick={onRefresh}
          style={{
            background: 'var(--surface, #2a2a2a)',
            border: '1px solid var(--border, #444)',
            color: 'var(--text-secondary, #aaa)',
            fontSize: 11,
            padding: '3px 10px',
            cursor: 'pointer',
            borderRadius: 4,
          }}
        >
          Refresh
        </button>
      </div>

      {/* Body */}
      {!state ? (
        <EmptyState>Loading communications...</EmptyState>
      ) : !state.available ? (
        <EmptyState>Not available{state.reason ? `: ${state.reason}` : ''}</EmptyState>
      ) : state.empty ? (
        <EmptyState>No communications yet. They will appear once agents start collaborating.</EmptyState>
      ) : (
        <CommsBody
          state={state}
          selectedPath={selectedPath}
          onSelectMessage={handleSelectMessage}
        />
      )}

      {/* Message viewer overlay */}
      {message && (
        <MessageViewer
          message={message}
          onClose={() => setSelectedPath(null)}
        />
      )}
    </div>
  )
}

/* ── Body ── */

function CommsBody({
  state,
  selectedPath,
  onSelectMessage,
}: {
  state: CommsStatePayload
  selectedPath: string | null
  onSelectMessage: (path: string) => void
}) {
  const hasRoles = (state.roles?.length ?? 0) > 0
  const hasMeetings = (state.meetings?.length ?? 0) > 0
  const hasFailures = (state.recent_failures?.length ?? 0) > 0

  return (
    <div style={{ padding: '4px 0' }}>
      {hasFailures && (
        <Section title="Failures">
          {(state.recent_failures || []).map((f, i) => (
            <div
              key={`${f.recorded_at || 'f'}-${i}`}
              style={{
                padding: '6px 12px',
                marginBottom: 4,
                background: 'color-mix(in srgb, var(--red, #e74c3c) 6%, transparent)',
                borderLeft: '3px solid var(--red, #e74c3c)',
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 12 }}>
                {f.operation} &middot; {f.from_role} &rarr; {f.to_role}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary, #999)', marginTop: 2 }}>
                {f.reason}
              </div>
            </div>
          ))}
        </Section>
      )}

      {hasRoles && (
        <Section title={`Inboxes (${state.roles!.length})`}>
          {state.roles!.map((role) => (
            <RoleSection
              key={role.role_id}
              role={role}
              selectedPath={selectedPath}
              onSelectMessage={onSelectMessage}
            />
          ))}
        </Section>
      )}

      {hasMeetings && (
        <Section title="Meetings">
          {(state.meetings || []).map((m) => (
            <div
              key={m.meeting_id}
              style={{
                padding: '6px 12px',
                marginBottom: 4,
                borderLeft: `3px solid ${m.status === 'open' ? 'var(--accent, #3498db)' : 'var(--text-dim, #555)'}`,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Badge
                  color={m.status === 'open' ? 'var(--accent, #3498db)' : 'var(--text-dim, #555)'}
                >
                  {m.status}
                </Badge>
                <strong style={{ fontSize: 12, flex: 1 }}>{m.topic}</strong>
                <span style={{ fontSize: 11, color: 'var(--text-dim, #888)' }}>
                  {m.entry_count} entries
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary, #999)', marginTop: 3 }}>
                by {m.organizer} &middot; {m.participants.join(', ')}
              </div>
              {m.decision && (
                <div style={{ fontSize: 11, marginTop: 3, color: 'var(--green, #27ae60)' }}>
                  Decision: {m.decision}
                </div>
              )}
            </div>
          ))}
        </Section>
      )}

      {!hasRoles && !hasMeetings && !hasFailures && (
        <EmptyState>No communication activity yet.</EmptyState>
      )}
    </div>
  )
}

/* ── Role Section ── */

type MessageTab = 'unread' | 'history' | 'sent'

function RoleSection({
  role,
  selectedPath,
  onSelectMessage,
}: {
  role: CommsRolePayload
  selectedPath: string | null
  onSelectMessage: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const hasAnyMessages =
    role.unread_count > 0 || (role.recent_seen?.length ?? 0) > 0 || (role.recent_outbox?.length ?? 0) > 0
  const [activeTab, setActiveTab] = useState<MessageTab>(role.unread_count > 0 ? 'unread' : 'history')

  const messages: CommsMessageItem[] = useMemo(() => {
    switch (activeTab) {
      case 'unread':
        return role.recent_unread || []
      case 'history':
        return role.recent_seen || []
      case 'sent':
        return role.recent_outbox || []
      default:
        return []
    }
  }, [activeTab, role.recent_unread, role.recent_seen, role.recent_outbox])

  const roleName = role.role_id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

  return (
    <div style={{ marginBottom: 2 }}>
      {/* Role header */}
      <div
        onClick={() => setExpanded((e) => !e)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          cursor: 'pointer',
          userSelect: 'none',
          background: expanded ? 'var(--surface-hover, rgba(255,255,255,0.03))' : 'transparent',
          borderRadius: 4,
        }}
      >
        <span style={{ fontSize: 10, width: 10 }}>{expanded ? '▼' : '▶'}</span>
        <span style={{ flex: 1, fontWeight: 600, fontSize: 12 }}>{roleName}</span>
        {role.has_blocking && <Badge color="var(--red, #e74c3c)">BLOCKING</Badge>}
        {role.unread_count > 0 && (
          <Badge color="var(--accent, #3498db)">{role.unread_count} new</Badge>
        )}
        <span style={{ fontSize: 11, color: 'var(--text-dim, #666)' }}>
          {role.seen_count} read &middot; {role.outbox_count} sent
        </span>
      </div>

      {expanded && hasAnyMessages && (
        <div style={{ paddingLeft: 12 }}>
          {/* Sub-tabs */}
          <div
            style={{
              display: 'flex',
              gap: 2,
              padding: '4px 0',
              borderBottom: '1px solid var(--border, #333)',
              marginBottom: 4,
            }}
          >
            <TabButton active={activeTab === 'unread'} onClick={() => setActiveTab('unread')}>
              Unread ({role.unread_count})
            </TabButton>
            <TabButton active={activeTab === 'history'} onClick={() => setActiveTab('history')}>
              Read ({role.recent_seen?.length ?? 0})
            </TabButton>
            <TabButton active={activeTab === 'sent'} onClick={() => setActiveTab('sent')}>
              Sent ({role.recent_outbox?.length ?? 0})
            </TabButton>
          </div>

          {/* Message list */}
          {messages.length === 0 ? (
            <div style={{ padding: '8px 0', fontSize: 11, color: 'var(--text-dim, #666)' }}>
              No messages.
            </div>
          ) : (
            <div style={{ maxHeight: 280, overflow: 'auto' }}>
              {messages.map((m) => (
                <MessageRow
                  key={m.message_id}
                  message={m}
                  selected={m.path === selectedPath}
                  onClick={() => onSelectMessage(m.path)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Message Row ── */

function MessageRow({
  message,
  selected,
  onClick,
}: {
  message: CommsMessageItem
  selected: boolean
  onClick: () => void
}) {
  const isSent = message.bucket === 'sent'
  const direction = isSent
    ? `To: ${message.to || '?'}`
    : `From: ${message.from}`

  return (
    <div
      onClick={onClick}
      style={{
        padding: '5px 8px',
        marginBottom: 2,
        background: selected ? 'var(--accent-soft, #2c4a6b)' : 'transparent',
        cursor: 'pointer',
        borderRadius: 4,
        borderLeft: message.blocking
          ? '3px solid var(--red, #e74c3c)'
          : '3px solid transparent',
        transition: 'background 0.1s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, color: 'var(--text-secondary, #aaa)', minWidth: 0, flex: 1 }}>
          <strong>{direction}</strong>{' '}
          <span style={{ color: 'var(--text, #ddd)' }}>{message.subject}</span>
        </span>
        {message.blocking && (
          <span style={{ fontSize: 9, color: 'var(--red, #e74c3c)', fontWeight: 700 }}>BLK</span>
        )}
        <span style={{ fontSize: 10, color: 'var(--text-dim, #666)', flexShrink: 0 }}>
          {message.sent_at?.slice(11, 19) || ''}
        </span>
      </div>
    </div>
  )
}

/* ── Message Viewer ── */

function MessageViewer({
  message,
  onClose,
}: {
  message: CommsMessagePayload
  onClose: () => void
}) {
  return (
    <div
      style={{
        margin: '8px 12px 12px',
        padding: 12,
        border: '1px solid var(--border, #444)',
        borderRadius: 6,
        background: 'var(--bg-secondary, #262626)',
        maxHeight: 400,
        overflow: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <strong style={{ flex: 1, fontSize: 13 }}>
          {(message.header.subject as string) || '(no subject)'}
        </strong>
        <button
          onClick={onClose}
          style={{
            background: 'var(--surface, #333)',
            border: '1px solid var(--border, #444)',
            color: 'var(--text-secondary, #aaa)',
            fontSize: 11,
            padding: '2px 10px',
            cursor: 'pointer',
            borderRadius: 4,
          }}
        >
          Close
        </button>
      </div>
      <div
        style={{
          fontSize: 11,
          color: 'var(--text-secondary, #999)',
          marginBottom: 8,
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <span>
          From: <strong>{(message.header.from as string) || '?'}</strong>
        </span>
        <span>
          To: <strong>{(message.header.to as string) || '?'}</strong>
        </span>
        <span>{(message.header.sent_at as string) || ''}</span>
        {message.header.blocking && (
          <Badge color="var(--red, #e74c3c)">BLOCKING</Badge>
        )}
      </div>
      <pre
        style={{
          margin: 0,
          fontSize: 12,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          color: 'var(--text, #ddd)',
          lineHeight: 1.5,
        }}
      >
        {message.body}
      </pre>
    </div>
  )
}

/* ── Shared UI helpers ── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 4 }}>
      <div
        style={{
          fontSize: 10,
          textTransform: 'uppercase',
          letterSpacing: 0.6,
          fontWeight: 700,
          color: 'var(--text-dim, #777)',
          padding: '8px 12px 4px',
        }}
      >
        {title}
      </div>
      {children}
    </div>
  )
}

function Badge({
  color,
  children,
}: {
  color: string
  children: React.ReactNode
}) {
  return (
    <span
      style={{
        background: color,
        color: 'white',
        borderRadius: 10,
        padding: '1px 7px',
        fontSize: 10,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? 'var(--accent-soft, #2c4a6b)' : 'transparent',
        border: 'none',
        color: active ? 'var(--text, #ddd)' : 'var(--text-secondary, #999)',
        fontSize: 11,
        padding: '3px 10px',
        cursor: 'pointer',
        borderRadius: 4,
        fontWeight: active ? 600 : 400,
      }}
    >
      {children}
    </button>
  )
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: '20px 16px',
        fontSize: 12,
        color: 'var(--text-secondary, #888)',
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  )
}
