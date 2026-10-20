/**
 * OrgVersionSwitcher — editor-toolbar "version picker" for the org.
 *
 * Conceptually equivalent to Figma's page switcher or VS Code's git-branch
 * indicator: a compact pill showing the active saved-org name (+ modified
 * indicator when the editor has changed since the last loaded snapshot),
 * plus a glass popover command-menu for search / load / save-as-copy
 * / delete.
 *
 * All visual tokens match the house dialect (refined-technical, dark, no
 * emoji, no purple gradients). Styles live in structure.css under `.sos-*`.
 *
 * Lives in the StructureEditor toolbar (Team tab, org mode only).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

interface SavedOrg {
  name: string
  organization_name?: string
  saved_at: number
  roles_count: number
  employees_count: number
}

export interface OrgVersionSwitcherProps {
  savedOrgs: SavedOrg[] | null
  activeName: string | null
  isDirty: boolean
  onRefresh: () => void
  onSaveAs: (name: string, overwrite: boolean) => void
  onLoad: (name: string) => void
  onDelete: (name: string) => void
}

const MAX_DISPLAY_NAME = 80

function formatRelativeTime(epochSeconds: number): string {
  const now = Date.now() / 1000
  const diff = Math.max(0, now - epochSeconds)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  try {
    return new Date(epochSeconds * 1000).toLocaleDateString()
  } catch {
    return ''
  }
}

function displayOrgName(org: SavedOrg): string {
  return (org.organization_name || org.name).trim() || org.name
}

function isValidDisplayName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0
    && trimmed.length <= MAX_DISPLAY_NAME
    && !/[\\/]/.test(trimmed)
    && !/[\u0000-\u001f]/.test(trimmed)
}

function slugifyOrgDisplayName(value: string): string {
  const ascii = value.normalize('NFKD').replace(/[^\x00-\x7F]/g, '')
  const slug = ascii
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^[_-]+|[_-]+$/g, '')
  return slug.slice(0, 64) || 'org'
}

/* Inline SVG glyphs — match the house convention (no emoji, no font icons). */
function LayersGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 1.5L1.5 4.5L8 7.5L14.5 4.5L8 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M2 8L8 10.8L14 8" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" opacity="0.6" />
      <path d="M2 11.5L8 14.2L14 11.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" opacity="0.35" />
    </svg>
  )
}

function Caret({ className }: { className?: string }) {
  return (
    <svg className={className} width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden>
      <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SearchGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
      <circle cx="5" cy="5" r="3.2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M7.5 7.5L10 10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}

export function OrgVersionSwitcher({
  savedOrgs, activeName, isDirty, onRefresh, onSaveAs, onLoad, onDelete,
}: OrgVersionSwitcherProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [highlighted, setHighlighted] = useState(0)
  const [saveAsMode, setSaveAsMode] = useState(false)
  const [saveAsName, setSaveAsName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [loadingName, setLoadingName] = useState<string | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const saveInputRef = useRef<HTMLInputElement>(null)

  // Clear loading state once the list or activeName updates (proxy for ack).
  useEffect(() => { setLoadingName(null) }, [activeName, savedOrgs])

  // Filter list by search.
  const filtered = (savedOrgs ?? []).filter(o =>
    !search.trim() || o.name.toLowerCase().includes(search.trim().toLowerCase()),
  )

  // Refresh once on first open.
  const firstOpenRef = useRef(false)
  useEffect(() => {
    if (open && !firstOpenRef.current) {
      firstOpenRef.current = true
      onRefresh()
    }
  }, [open, onRefresh])

  // Auto-focus save input when entering save-as mode.
  useEffect(() => {
    if (saveAsMode) saveInputRef.current?.focus()
  }, [saveAsMode])

  // Clamp highlight when filtered list shrinks.
  useEffect(() => {
    setHighlighted(h => Math.min(h, Math.max(0, filtered.length - 1)))
  }, [filtered.length])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        closePopover()
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const closePopover = useCallback(() => {
    setOpen(false)
    setSearch('')
    setHighlighted(0)
    setSaveAsMode(false)
    setSaveAsName('')
    setConfirmDelete(null)
  }, [])

  const handleLoad = useCallback((name: string) => {
    if (name === activeName) {
      // Already active — no need to round-trip; just close the popover.
      closePopover()
      return
    }
    setLoadingName(name)
    onLoad(name)
    closePopover()
  }, [onLoad, closePopover, activeName])

  const handleDelete = useCallback((name: string) => {
    onDelete(name)
    setConfirmDelete(null)
  }, [onDelete])

  const saveAsTrimmed = saveAsName.trim()
  const saveAsValid = isValidDisplayName(saveAsTrimmed)
  const saveAsSlug = slugifyOrgDisplayName(saveAsTrimmed)
  const saveAsExists = (savedOrgs ?? []).some(o =>
    o.name === saveAsSlug || displayOrgName(o).toLowerCase() === saveAsTrimmed.toLowerCase(),
  )

  const handleSaveAs = useCallback(() => {
    if (!saveAsValid) return
    onSaveAs(saveAsTrimmed, saveAsExists)
    setSaveAsMode(false)
    setSaveAsName('')
    closePopover()
  }, [saveAsValid, saveAsTrimmed, saveAsExists, onSaveAs, closePopover])

  // Keyboard navigation inside the popover.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      closePopover()
      return
    }
    if (saveAsMode) {
      if (e.key === 'Enter') {
        e.preventDefault()
        handleSaveAs()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlighted(h => Math.min(h + 1, Math.max(0, filtered.length - 1)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlighted(h => Math.max(0, h - 1))
    } else if (e.key === 'Enter') {
      const target = filtered[highlighted]
      if (target) handleLoad(target.name)
    } else if ((e.key === 'Backspace' || e.key === 'Delete') && (e.metaKey || e.ctrlKey)) {
      const target = filtered[highlighted]
      if (target) {
        e.preventDefault()
        setConfirmDelete(target.name)
      }
    }
  }

  return (
    <div className="sos-wrap" ref={wrapperRef}>
      <button
        type="button"
        className="sos-pill"
        data-open={open ? 'true' : 'false'}
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={activeName ? `Active saved architecture: ${activeName}` : 'No saved architecture loaded'}
      >
        <LayersGlyph className="sos-pill-glyph" />
        {activeName ? (
          <span className="sos-pill-name">{activeName}</span>
        ) : (
          <span className="sos-pill-name sos-pill-name--placeholder">draft</span>
        )}
        {isDirty && <span className="sos-pill-dirty" aria-label="Modified since opened" />}
        <Caret className="sos-pill-caret" />
      </button>

      {open && (
        <div className="sos-popover" role="listbox" onKeyDown={onKeyDown} tabIndex={-1}>
          <div className="sos-search-row">
            <SearchGlyph className="sos-search-glyph" />
            <input
              type="text"
              className="sos-search-input"
              placeholder="Search architectures…"
              value={search}
              onChange={e => { setSearch(e.target.value); setHighlighted(0) }}
              autoFocus
              onKeyDown={onKeyDown}
            />
            <kbd className="sos-search-hint">↑↓ ↵</kbd>
          </div>

          <div className="sos-list">
            {savedOrgs === null ? (
              <div className="sos-empty">Loading…</div>
            ) : filtered.length === 0 ? (
              <div className="sos-empty">
                {(savedOrgs ?? []).length === 0 ? 'No saved architectures.' : 'No matches.'}
              </div>
            ) : (
              filtered.map((org, idx) => {
                const isActive = activeName === org.name
                const isHighlighted = highlighted === idx
                const title = displayOrgName(org)
                return (
                  <div
                    key={org.name}
                    className={`sos-row${isActive ? ' sos-row--active' : ''}`}
                    data-highlighted={isHighlighted ? 'true' : 'false'}
                    onMouseEnter={() => setHighlighted(idx)}
                    onClick={() => handleLoad(org.name)}
                    role="option"
                    aria-selected={isActive}
                  >
                    <div className="sos-row-meta">
                      <span className="sos-row-name">{title}</span>
                      <span className="sos-row-stats">
                        {title !== org.name && `${org.name} · `}
                        {org.roles_count} {org.roles_count === 1 ? 'role' : 'roles'}
                        {' · '}
                        {org.employees_count} {org.employees_count === 1 ? 'employee' : 'employees'}
                        {' · '}
                        {formatRelativeTime(org.saved_at)}
                      </span>
                    </div>
                    {loadingName === org.name ? (
                      <span className="sos-row-loading">loading…</span>
                    ) : isActive ? (
                      <span className="sos-row-active-chip">active</span>
                    ) : confirmDelete === org.name ? (
                      <button
                        type="button"
                        className="sos-row-delete sos-row-delete--confirm"
                        onClick={e => { e.stopPropagation(); handleDelete(org.name) }}
                      >
                        confirm
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="sos-row-delete"
                        onClick={e => { e.stopPropagation(); setConfirmDelete(org.name) }}
                        title="Delete"
                      >
                        delete
                      </button>
                    )}
                  </div>
                )
              })
            )}
          </div>

          <div className="sos-save-as">
            {saveAsMode ? (
              <div className="sos-save-as-form">
                <input
                  ref={saveInputRef}
                  type="text"
                  className="sos-save-as-input"
                  placeholder="Organization name"
                  value={saveAsName}
                  onChange={e => setSaveAsName(e.target.value)}
                  onKeyDown={onKeyDown}
                />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleSaveAs}
                  disabled={!saveAsValid}
                >
                  {saveAsExists ? 'Overwrite copy' : 'Save as copy'}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => { setSaveAsMode(false); setSaveAsName('') }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="sos-save-as-trigger"
                onClick={() => setSaveAsMode(true)}
              >
                + Save as copy...
              </button>
            )}
            {saveAsMode && saveAsName && !saveAsValid && (
              <div className="sos-save-as-hint sos-save-as-hint--warn">
                Use up to 80 characters. Slashes are not allowed.
              </div>
            )}
            {saveAsMode && saveAsValid && saveAsExists && (
              <div className="sos-save-as-hint sos-save-as-hint--warn">
                Name exists - this will overwrite that copy.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
