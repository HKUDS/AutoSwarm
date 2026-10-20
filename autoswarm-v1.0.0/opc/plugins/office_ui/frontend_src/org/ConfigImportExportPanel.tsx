import { useEffect, useRef, useState, type ChangeEvent } from 'react'

/* ── Inline SVG icon data-URIs ──────────────────────────────────── */
const ICON = {
  download: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z'/%3E%3C/svg%3E",
  upload: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23888' d='M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z'/%3E%3C/svg%3E",
  check: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%2322c55e' d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z'/%3E%3C/svg%3E",
  warn: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23ef4444' d='M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z'/%3E%3C/svg%3E",
}

interface ConfigImportExportPanelProps {
  onExport: () => void
  onImport: (yaml: string, dryRun: boolean) => void
  configExportYaml?: string | null
  importPreview?: { roles_added: number; roles_removed: number; employees_changed: number } | null
  importError?: string | null
}

export function ConfigImportExportPanel({
  onExport, onImport, configExportYaml, importPreview, importError,
}: ConfigImportExportPanelProps) {
  const [yamlText, setYamlText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [dryRunDone, setDryRunDone] = useState(false)
  const exportPending = useRef(false)
  const lastExportedYaml = useRef<string | null>(null)

  // Trigger browser download when server returns the exported YAML
  useEffect(() => {
    if (!exportPending.current) return
    if (!configExportYaml) return
    if (configExportYaml === lastExportedYaml.current) return
    lastExportedYaml.current = configExportYaml
    exportPending.current = false

    const blob = new Blob([configExportYaml], { type: 'application/x-yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    a.download = `org_config_${stamp}.yaml`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [configExportYaml])

  // Reset dry-run state when user edits the YAML
  useEffect(() => {
    setDryRunDone(false)
  }, [yamlText])

  // Flip dry-run state on successful preview (not on error)
  useEffect(() => {
    if (importPreview && !importError) setDryRunDone(true)
  }, [importPreview, importError])

  const handleExport = () => {
    exportPending.current = true
    onExport()
  }

  const handleFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result === 'string') setYamlText(result)
    }
    reader.readAsText(file)
    e.target.value = '' // allow re-selecting same file
  }

  const handleDryRun = () => {
    if (!yamlText.trim()) return
    onImport(yamlText, true)
  }

  const handleApply = () => {
    if (!dryRunDone || !yamlText.trim()) return
    if (!confirm('Apply this config? The current company architecture will be overwritten.')) return
    onImport(yamlText, false)
    setDryRunDone(false)
    setYamlText('')
    setFileName(null)
  }

  const handleClear = () => {
    setYamlText('')
    setFileName(null)
    setDryRunDone(false)
  }

  return (
    <div className="cfg-io-panel" data-testid="config-import-export-panel">
      <div className="cfg-io-header">
        <h3 className="cfg-io-title">Config Import / Export</h3>
        <p className="cfg-io-subtitle">Download the current company architecture as YAML, or upload one to replace it.</p>
      </div>

      {/* Export */}
      <div className="cfg-io-section">
        <div className="cfg-io-section-header">
          <img src={ICON.download} alt="" className="cfg-io-section-icon" />
          <span className="cfg-io-section-title">Download current config</span>
        </div>
        <button className="cfg-io-btn cfg-io-btn-primary" onClick={handleExport}>
          <img src={ICON.download} alt="" className="cfg-io-btn-icon" /> Download YAML
        </button>
      </div>

      {/* Import */}
      <div className="cfg-io-section">
        <div className="cfg-io-section-header">
          <img src={ICON.upload} alt="" className="cfg-io-section-icon" />
          <span className="cfg-io-section-title">Upload config</span>
        </div>

        <div className="cfg-io-upload-row">
          <label className="cfg-io-file-label">
            <input type="file" accept=".yaml,.yml" onChange={handleFile} className="cfg-io-file-input" />
            <span className="cfg-io-file-btn">Choose file…</span>
            <span className="cfg-io-file-name">{fileName ?? 'no file selected'}</span>
          </label>
        </div>

        <textarea
          className="cfg-io-textarea"
          placeholder="…or paste YAML here"
          value={yamlText}
          onChange={e => setYamlText(e.target.value)}
          spellCheck={false}
          rows={10}
        />

        <div className="cfg-io-actions">
          <button className="cfg-io-btn cfg-io-btn-ghost"
            onClick={handleDryRun}
            disabled={!yamlText.trim()}>
            Dry run
          </button>
          <button className="cfg-io-btn cfg-io-btn-primary"
            onClick={handleApply}
            disabled={!dryRunDone || !yamlText.trim()}>
            Apply
          </button>
          {yamlText && (
            <button className="cfg-io-btn cfg-io-btn-ghost" onClick={handleClear}>Clear</button>
          )}
        </div>

        {/* Preview */}
        {importPreview && !importError && (
          <div className="cfg-io-preview">
            <img src={ICON.check} alt="" className="cfg-io-preview-icon" />
            <div className="cfg-io-preview-body">
              <div className="cfg-io-preview-title">Dry run OK — ready to apply</div>
              <div className="cfg-io-preview-stats">
                <span className="cfg-io-preview-stat">
                  <span className="cfg-io-stat-label">Roles added</span>
                  <span className="cfg-io-stat-value">{importPreview.roles_added}</span>
                </span>
                <span className="cfg-io-preview-stat">
                  <span className="cfg-io-stat-label">Roles removed</span>
                  <span className="cfg-io-stat-value">{importPreview.roles_removed}</span>
                </span>
                <span className="cfg-io-preview-stat">
                  <span className="cfg-io-stat-label">Employees changed</span>
                  <span className="cfg-io-stat-value">{importPreview.employees_changed}</span>
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Error */}
        {importError && (
          <div className="cfg-io-error">
            <img src={ICON.warn} alt="" className="cfg-io-error-icon" />
            <div className="cfg-io-error-body">
              <div className="cfg-io-error-title">Validation failed</div>
              <pre className="cfg-io-error-text">{importError}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
