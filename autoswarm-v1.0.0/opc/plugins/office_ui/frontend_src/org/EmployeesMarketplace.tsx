import { useMemo, useState } from 'react'
import type { TalentTemplate, OrgRole, HireTalentHandler } from '../types/visual'
import { TalentCard } from './TalentCard'
import { TalentDetailModal } from './TalentDetailModal'
import { HireToRoleModal } from './HireToRoleModal'

/* ── Inline SVG icon data-URIs ──────────────────────────────────── */
const ICON = {
  search: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z'/%3E%3C/svg%3E",
  talent: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E",
}

interface EmployeesMarketplaceProps {
  templates: TalentTemplate[]
  vacantRoles: OrgRole[]
  hiringTemplateId: string | null
  readOnly: boolean
  onHireTalent: HireTalentHandler
}

export function EmployeesMarketplace({
  templates, vacantRoles, hiringTemplateId, readOnly, onHireTalent,
}: EmployeesMarketplaceProps) {
  const [search, setSearch] = useState('')
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [detailTemplate, setDetailTemplate] = useState<TalentTemplate | null>(null)
  const [hireForTemplate, setHireForTemplate] = useState<TalentTemplate | null>(null)

  const categories = useMemo(() => {
    const cats = new Set<string>()
    for (const t of templates) { if (t.category) cats.add(t.category) }
    return Array.from(cats).sort()
  }, [templates])

  const filteredTemplates = useMemo(() => {
    let result = templates
    if (activeCategory) result = result.filter(t => t.category === activeCategory)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.domains.some(d => d.toLowerCase().includes(q)) ||
        t.tags.some(tag => tag.toLowerCase().includes(q)) ||
        (t.vibe ?? '').toLowerCase().includes(q),
      )
    }
    return result
  }, [templates, activeCategory, search])

  const handleCardHire = (templateId: string) => {
    if (readOnly) return
    const template = templates.find(t => t.template_id === templateId)
    if (template) setHireForTemplate(template)
  }

  return (
    <div className="mkt-container" data-testid="employees-marketplace">
      {/* Toolbar */}
      <div className="mkt-toolbar">
        <div className="mkt-search-wrap">
          <img src={ICON.search} alt="" className="mkt-search-icon" />
          <input
            className="mkt-search"
            placeholder="Search talent templates..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <span className="mkt-count">
          {filteredTemplates.length} employees
          {vacantRoles.length > 0 && <> · {vacantRoles.length} vacant role{vacantRoles.length === 1 ? '' : 's'}</>}
        </span>
      </div>

      {/* Category pills */}
      {categories.length > 0 && (
        <div className="mkt-filters">
          <div className="mkt-pill-row">
            <button className={`mkt-pill${!activeCategory ? ' active' : ''}`}
              onClick={() => setActiveCategory(null)}>All</button>
            {categories.map(cat => (
              <button key={cat} className={`mkt-pill${activeCategory === cat ? ' active' : ''}`}
                onClick={() => setActiveCategory(activeCategory === cat ? null : cat)}
              >{cat}</button>
            ))}
          </div>
        </div>
      )}

      {/* Talent grid */}
      {filteredTemplates.length > 0 ? (
        <div className="mkt-section">
          <div className="mkt-section-header">
            <img src={ICON.talent} alt="" className="mkt-section-icon" />
            <h3 className="mkt-section-title">Talent Templates</h3>
            <span className="mkt-section-count">{filteredTemplates.length}</span>
          </div>
          <div className="tm-grid">
            {filteredTemplates.map(t => (
              <TalentCard key={t.template_id} template={t}
                hiringId={hiringTemplateId}
                onHire={handleCardHire}
                onClick={setDetailTemplate}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="mkt-empty">
          <p>{templates.length === 0 ? 'No talent templates available.' : 'No employees match your search.'}</p>
        </div>
      )}

      {/* Detail modal */}
      {detailTemplate && (
        <TalentDetailModal
          template={detailTemplate}
          vacantRoles={vacantRoles}
          hiringId={hiringTemplateId}
          readOnly={readOnly}
          onHire={(tid, rid) => { if (!readOnly) { onHireTalent(tid, rid); setDetailTemplate(null) } }}
          onClose={() => setDetailTemplate(null)}
        />
      )}

      <HireToRoleModal
        open={hireForTemplate !== null}
        template={hireForTemplate}
        vacantRoles={vacantRoles}
        onConfirm={(tid, rid) => { onHireTalent(tid, rid); setHireForTemplate(null) }}
        onClose={() => setHireForTemplate(null)}
      />
    </div>
  )
}
