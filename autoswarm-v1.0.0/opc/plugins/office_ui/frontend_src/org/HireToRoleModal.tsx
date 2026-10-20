import { useEffect, useState } from 'react'
import type { OrgRole, TalentTemplate, RoleId, TemplateId } from '../types/visual'
import { asRoleId, asTemplateId } from '../types/visual'

interface HireToRoleModalProps {
  open: boolean
  template: TalentTemplate | null
  vacantRoles: OrgRole[]
  onConfirm: (template: TemplateId, role: RoleId) => void
  onClose: () => void
}

export function HireToRoleModal({
  open, template, vacantRoles, onConfirm, onClose,
}: HireToRoleModalProps) {
  const [selectedRoleId, setSelectedRoleId] = useState<string>('')

  useEffect(() => {
    if (open) setSelectedRoleId('')
  }, [open, template?.template_id])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !template) return null

  const noVacancies = vacantRoles.length === 0
  const canConfirm = !noVacancies && selectedRoleId !== ''

  const handleConfirm = () => {
    if (!canConfirm) return
    onConfirm(asTemplateId(template.template_id), asRoleId(selectedRoleId))
  }

  return (
    <div className="htr-overlay" role="dialog" aria-modal="true" onMouseDown={onClose}>
      <div className="htr-modal" onMouseDown={event => event.stopPropagation()}>
        <header className="htr-header">
          <div>
            <h3 className="htr-title">Hire {template.name}</h3>
            <p className="htr-subtitle">Pick the role to fill with this employee.</p>
          </div>
          <button className="htr-close" type="button" onClick={onClose} aria-label="Close">x</button>
        </header>

        <div className="htr-body">
          {noVacancies ? (
            <div className="htr-empty">
              <p className="htr-empty-title">No vacant roles.</p>
              <p className="htr-empty-hint">
                Add a role in the Team tab first, then come back to hire.
              </p>
            </div>
          ) : (
            <div className="htr-role-list" role="listbox" aria-label="Vacant roles">
              {vacantRoles.map(role => {
                const selected = role.role_id === selectedRoleId
                return (
                  <button
                    key={role.role_id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`htr-role-row${selected ? ' is-selected' : ''}`}
                    onClick={() => setSelectedRoleId(role.role_id)}
                  >
                    <span className="htr-role-name">{role.name}</span>
                    {role.responsibility && (
                      <span className="htr-role-resp">{role.responsibility}</span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <footer className="htr-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            {noVacancies ? 'Close' : 'Cancel'}
          </button>
          {!noVacancies && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleConfirm}
              disabled={!canConfirm}
            >
              Hire to selected role
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}
