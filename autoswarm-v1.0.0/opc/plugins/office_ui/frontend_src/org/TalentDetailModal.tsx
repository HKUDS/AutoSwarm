import { useEffect, useState } from 'react'
import type { OrgRole, TalentTemplate, HireTalentHandler } from '../types/visual'
import { asRoleId, asTemplateId } from '../types/visual'

interface TalentDetailModalProps {
  template: TalentTemplate
  vacantRoles: OrgRole[]
  hiringId?: string | null
  readOnly?: boolean
  onHire: HireTalentHandler
  onClose: () => void
}

export function TalentDetailModal({
  template: t, vacantRoles, hiringId, readOnly, onHire, onClose,
}: TalentDetailModalProps) {
  const [selectedRoleId, setSelectedRoleId] = useState<string>('')
  const isHiring = hiringId === t.template_id
  const noVacancies = vacantRoles.length === 0
  const canHire = !readOnly && !isHiring && !noVacancies && selectedRoleId !== ''

  useEffect(() => {
    setSelectedRoleId('')
  }, [t.template_id])

  const handleHire = () => {
    if (!canHire) return
    onHire(asTemplateId(t.template_id), asRoleId(selectedRoleId))
  }

  return (
    <div className="tm-detail-overlay" onClick={onClose}>
      <div className="tm-detail-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="tm-detail-header" style={{ borderBottomColor: t.color || 'var(--border)' }}>
          {t.emoji && <span className="tm-detail-emoji">{t.emoji}</span>}
          <div>
            <h2 className="tm-detail-name">{t.name}</h2>
            <span className="tm-detail-category">{t.category}</span>
          </div>
          <button className="tm-detail-close" onClick={onClose}>&times;</button>
        </div>

        {/* Body */}
        <div className="tm-detail-body">
          {t.vibe && (
            <blockquote className="tm-detail-vibe">"{t.vibe}"</blockquote>
          )}

          {t.description && (
            <p className="tm-detail-desc">{t.description}</p>
          )}

          {t.domains.length > 0 && (
            <div className="tm-detail-section">
              <div className="tm-detail-label">Domains</div>
              <div className="tm-detail-tags">
                {t.domains.map(d => <span key={d} className="org-domain-tag">{d}</span>)}
              </div>
            </div>
          )}

          {t.tags.length > 0 && (
            <div className="tm-detail-section">
              <div className="tm-detail-label">Tags</div>
              <div className="tm-detail-tags">
                {t.tags.map(tag => <span key={tag} className="org-tool-tag">{tag}</span>)}
              </div>
            </div>
          )}

          {t.preferred_external_agent && (
            <div className="tm-detail-section">
              <div className="tm-detail-label">Recommended Agent</div>
              <span className="tm-card-agent-badge">{t.preferred_external_agent}</span>
            </div>
          )}

          {!readOnly && (
            <div className="tm-detail-section">
              <div className="tm-detail-label">Hire into role</div>
              {noVacancies ? (
                <p className="tm-detail-vacancy-empty">
                  No vacant roles. Create a role in the Team tab first.
                </p>
              ) : (
                <div className="tm-detail-role-list" role="listbox" aria-label="Vacant roles">
                  {vacantRoles.map(role => {
                    const selected = role.role_id === selectedRoleId
                    return (
                      <button
                        key={role.role_id}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        className={`tm-detail-role-row${selected ? ' is-selected' : ''}`}
                        onClick={() => setSelectedRoleId(role.role_id)}
                      >
                        <span className="tm-detail-role-name">{role.name}</span>
                        {role.responsibility && (
                          <span className="tm-detail-role-resp">{role.responsibility}</span>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Hire footer */}
        {!readOnly && (
          <div className="tm-detail-footer">
            <button
              className="tm-detail-hire-btn"
              disabled={!canHire}
              onClick={handleHire}
            >
              {isHiring ? <><span className="spinner-inline" /> Hiring...</> : 'Hire to selected role'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
