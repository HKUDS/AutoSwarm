import type { TalentTemplate } from '../types/visual'

interface TalentCardProps {
  template: TalentTemplate
  hiringId?: string | null
  onHire: (templateId: string) => void
  onClick: (template: TalentTemplate) => void
}

/** Derive a 2-letter monogram from a name. "Creative Director" → "CD". */
function monogram(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[words.length - 1][0]).toUpperCase()
}

export function TalentCard({ template: t, hiringId, onHire, onClick }: TalentCardProps) {
  const isHiring = hiringId === t.template_id
  const avatarStyle = t.color
    ? {
        background: `color-mix(in srgb, ${t.color} 18%, transparent)`,
        color: t.color,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${t.color} 28%, transparent)`,
      }
    : undefined

  const chipPool: string[] = []
  const seen = new Set<string>()
  for (const c of [...t.tags, ...t.domains]) {
    if (!seen.has(c)) { seen.add(c); chipPool.push(c) }
  }
  const visibleChips = chipPool.slice(0, 3)
  const overflow = chipPool.length - visibleChips.length

  return (
    <div className="tm-card" onClick={() => onClick(t)}>
      <div className="tm-card-head">
        <div className="tm-card-avatar" style={avatarStyle} aria-hidden>
          {t.emoji ? (
            <span className="tm-card-avatar-emoji">{t.emoji}</span>
          ) : (
            <span className="tm-card-avatar-mono">{monogram(t.name)}</span>
          )}
        </div>
        <div className="tm-card-head-text">
          <div className="tm-card-name-row">
            <span className="tm-card-name" title={t.name}>{t.name}</span>
            {t.preferred_external_agent && (
              <span
                className="tm-card-agent-badge"
                title={`Agent: ${t.preferred_external_agent}`}
              >
                {t.preferred_external_agent}
              </span>
            )}
          </div>
          {t.category && <span className="tm-card-category">{t.category}</span>}
        </div>
      </div>

      <div className="tm-card-body">
        {t.vibe && <div className="tm-card-vibe">"{t.vibe}"</div>}
        {t.description && <div className="tm-card-desc">{t.description}</div>}
      </div>

      {visibleChips.length > 0 && (
        <div className="tm-card-chips">
          {visibleChips.map(c => (
            <span key={c} className="tm-card-chip">{c}</span>
          ))}
          {overflow > 0 && (
            <span className="tm-card-chip tm-card-chip-more">+{overflow}</span>
          )}
        </div>
      )}

      <div className="tm-card-footer">
        <button
          className="tm-card-hire-btn"
          disabled={isHiring}
          onClick={(e) => { e.stopPropagation(); onHire(t.template_id) }}
        >
          {isHiring ? <><span className="spinner-inline" /> Hiring</> : 'Hire →'}
        </button>
      </div>
    </div>
  )
}
