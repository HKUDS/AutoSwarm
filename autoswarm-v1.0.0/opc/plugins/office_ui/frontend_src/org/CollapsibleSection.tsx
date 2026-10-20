import { useState, type ReactNode } from 'react'

interface CollapsibleSectionProps {
  icon: string
  title: string
  count: number
  extra?: ReactNode
  children: ReactNode
  defaultExpanded?: boolean
}

export function CollapsibleSection({ icon, title, count, extra, children, defaultExpanded = false }: CollapsibleSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return (
    <div className="myorg-collapsible">
      <button className="myorg-collapsible-toggle" onClick={() => setExpanded(!expanded)}>
        <span className="myorg-toggle-icon">{expanded ? '\u25BE' : '\u25B8'}</span>
        <img src={icon} alt="" className="myorg-section-icon" />
        <span className="myorg-collapsible-title">{title}</span>
        <span className="myorg-collapsible-count">{count}</span>
        {extra && <span className="myorg-collapsible-extra" onClick={e => e.stopPropagation()}>{extra}</span>}
      </button>
      {expanded && <div className="myorg-collapsible-body">{children}</div>}
    </div>
  )
}
