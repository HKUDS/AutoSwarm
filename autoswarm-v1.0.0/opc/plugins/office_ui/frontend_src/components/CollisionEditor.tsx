import { useEffect, useRef, useState, useCallback } from 'react'
import type { GameBridge } from '../game/GameBridge'
import {
  DEFAULT_MAP_STR,
  DEFAULT_SEATS,
  parseMapStr,
  gridToMapStr,
} from '../game/map/OfficeMapBuilder'
import { getOffices, type OfficeConfig } from '../game/map/OfficeStore'
import { OFFICE_COLS, OFFICE_ROWS, TILE_SIZE } from '../game/config'

interface Props {
  bridge: GameBridge
}

type EditorMode = 'wall' | 'floor' | 'seat'

const BG_ASSET_URL = 'assets/office-bg.png'

export function CollisionEditor({ bridge }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const bgImgRef = useRef<HTMLImageElement | null>(null)
  const isPainting = useRef(false)

  const [offices, setOffices] = useState<OfficeConfig[]>(() => getOffices())
  const [selectedOfficeId, setSelectedOfficeId] = useState<string>(() => getOffices()[0]?.id ?? 'office-0')

  const selectedOffice = offices.find(o => o.id === selectedOfficeId) ?? offices[0]

  const [grid, setGrid] = useState<number[][]>(() => parseMapStr(selectedOffice?.mapStr ?? DEFAULT_MAP_STR, OFFICE_COLS, OFFICE_ROWS))
  const [seats, setSeats] = useState<[number, number][]>(() => [...(selectedOffice?.seats ?? DEFAULT_SEATS)])
  const [mode, setMode] = useState<EditorMode>('wall')
  const [bgLoaded, setBgLoaded] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [applied, setApplied] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [showGrid, setShowGrid] = useState(true)

  const cols = OFFICE_COLS
  const rows = OFFICE_ROWS

  const switchOffice = (officeId: string) => {
    const refreshed = getOffices()
    setOffices(refreshed)
    setSelectedOfficeId(officeId)
    const office = refreshed.find(o => o.id === officeId)
    if (office) {
      setGrid(parseMapStr(office.mapStr, OFFICE_COLS, OFFICE_ROWS))
      setSeats([...office.seats])
    }
    setApplied(false)
  }

  useEffect(() => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => { bgImgRef.current = img; setBgLoaded(true) }
    img.onerror = () => { bgImgRef.current = null; setBgLoaded(true) }
    img.src = BG_ASSET_URL
  }, [])

  const render = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const w = cols * TILE_SIZE
    const h = rows * TILE_SIZE
    canvas.width = w
    canvas.height = h

    ctx.clearRect(0, 0, w, h)

    if (bgImgRef.current) {
      ctx.drawImage(bgImgRef.current, 0, 0, w, h)
    } else {
      ctx.fillStyle = '#3d3d3d'
      ctx.fillRect(0, 0, w, h)
    }

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x0 = c * TILE_SIZE
        const y0 = r * TILE_SIZE
        const blocked = grid[r]?.[c] === 1
        ctx.fillStyle = blocked ? 'rgba(255, 40, 40, 0.35)' : 'rgba(40, 255, 40, 0.2)'
        ctx.fillRect(x0, y0, TILE_SIZE, TILE_SIZE)
      }
    }

    for (const [c, r] of seats) {
      const cx = c * TILE_SIZE + TILE_SIZE / 2
      const cy = r * TILE_SIZE + TILE_SIZE / 2
      ctx.beginPath()
      ctx.arc(cx, cy, 9, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(255, 220, 0, 0.85)'
      ctx.fill()
      ctx.strokeStyle = '#000'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }

    if (showGrid) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)'
      ctx.lineWidth = 0.5
      for (let c = 0; c <= cols; c++) {
        ctx.beginPath(); ctx.moveTo(c * TILE_SIZE, 0); ctx.lineTo(c * TILE_SIZE, h); ctx.stroke()
      }
      for (let r = 0; r <= rows; r++) {
        ctx.beginPath(); ctx.moveTo(0, r * TILE_SIZE); ctx.lineTo(w, r * TILE_SIZE); ctx.stroke()
      }
      ctx.font = '10px monospace'
      ctx.textBaseline = 'top'
      for (let c = 0; c < cols; c++) { ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.fillText(String(c), c * TILE_SIZE + 2, 2) }
      for (let r = 0; r < rows; r++) { ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.fillText(String(r), 2, r * TILE_SIZE + 2) }
    }
  }, [grid, seats, cols, rows, showGrid, bgLoaded])

  useEffect(() => { render() }, [render])

  const getCellFromEvent = (e: React.MouseEvent<HTMLCanvasElement>): { c: number; r: number } | null => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    const c = Math.floor((e.clientX - rect.left) * scaleX / TILE_SIZE)
    const r = Math.floor((e.clientY - rect.top) * scaleY / TILE_SIZE)
    if (c < 0 || c >= cols || r < 0 || r >= rows) return null
    return { c, r }
  }

  const paint = useCallback((c: number, r: number) => {
    setApplied(false)
    if (mode === 'seat') {
      setSeats(prev => {
        const idx = prev.findIndex(([sc, sr]) => sc === c && sr === r)
        if (idx >= 0) return prev.filter((_, i) => i !== idx)
        return [...prev, [c, r]]
      })
      setGrid(prev => {
        if (prev[r]?.[c] === 1) { const next = prev.map(row => [...row]); next[r][c] = 0; return next }
        return prev
      })
    } else {
      const value = mode === 'wall' ? 1 : 0
      setGrid(prev => {
        if (prev[r]?.[c] === value) return prev
        const next = prev.map(row => [...row]); next[r][c] = value; return next
      })
      if (mode === 'wall') setSeats(prev => prev.filter(([sc, sr]) => !(sc === c && sr === r)))
    }
  }, [mode])

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return
    isPainting.current = true
    const cell = getCellFromEvent(e)
    if (cell) paint(cell.c, cell.r)
  }
  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isPainting.current) return
    if (mode === 'seat') return
    const cell = getCellFromEvent(e)
    if (cell) paint(cell.c, cell.r)
  }
  const onMouseUp = () => { isPainting.current = false }

  const handleApply = () => {
    const mapStr = gridToMapStr(grid)
    bridge.rebuildOfficeCollision(selectedOfficeId, mapStr, seats)
    setApplied(true)
    setOffices(getOffices())
    setTimeout(() => setApplied(false), 2000)
  }

  const handleExport = () => setShowExport(v => !v)

  const handleReset = () => {
    setGrid(parseMapStr(DEFAULT_MAP_STR, OFFICE_COLS, OFFICE_ROWS))
    setSeats([...DEFAULT_SEATS])
    setApplied(false)
  }

  const exportText = (() => {
    const mapStr = gridToMapStr(grid)
    const mapLines = mapStr.map((line, i) => `  '${line}', // ${i}`).join('\n')
    const seatLines = seats.map(([c, r]) => `  [${c}, ${r}],`).join('\n')
    return `const MAP_STR: string[] = [\n${mapLines}\n]\n\nconst SEATS: [number, number][] = [\n${seatLines}\n]`
  })()

  const zoomIn = () => setZoom(z => Math.min(z + 0.25, 3))
  const zoomOut = () => setZoom(z => Math.max(z - 0.25, 0.5))

  const wallCount = grid.flat().filter(v => v === 1).length
  const floorCount = grid.flat().filter(v => v === 0).length

  return (
    <div className="collision-editor">
      <div className="ce-toolbar">
        <div className="ce-toolbar-group">
          <span className="ce-title">Map Editor</span>
          <select
            className="ce-office-select"
            value={selectedOfficeId}
            onChange={e => switchOffice(e.target.value)}
          >
            {offices.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>

        <div className="ce-toolbar-group">
          <button className={`ce-mode-btn ${mode === 'wall' ? 'active wall' : ''}`} onClick={() => setMode('wall')}>
            <span className="ce-mode-dot wall" /> Wall
          </button>
          <button className={`ce-mode-btn ${mode === 'floor' ? 'active floor' : ''}`} onClick={() => setMode('floor')}>
            <span className="ce-mode-dot floor" /> Floor
          </button>
          <button className={`ce-mode-btn ${mode === 'seat' ? 'active seat' : ''}`} onClick={() => setMode('seat')}>
            <span className="ce-mode-dot seat" /> Seat
          </button>
        </div>

        <div className="ce-toolbar-group">
          <button className="ce-btn" onClick={zoomOut}>-</button>
          <span className="ce-zoom-label">{Math.round(zoom * 100)}%</span>
          <button className="ce-btn" onClick={zoomIn}>+</button>
          <button className={`ce-btn${showGrid ? ' active' : ''}`} onClick={() => setShowGrid(v => !v)}>Grid</button>
        </div>

        <div className="ce-toolbar-group">
          <button className={`ce-btn apply${applied ? ' success' : ''}`} onClick={handleApply}>
            {applied ? 'Applied' : 'Apply'}
          </button>
          <button className={`ce-btn${showExport ? ' active' : ''}`} onClick={handleExport}>Export</button>
          <button className="ce-btn danger" onClick={handleReset}>Reset</button>
        </div>

        <div className="ce-toolbar-group ce-stats">
          <span>Walls: {wallCount}</span>
          <span>Floor: {floorCount}</span>
          <span>Seats: {seats.length}</span>
        </div>
      </div>

      <div className="ce-body">
        <div className="ce-canvas-wrap" style={{ overflow: 'auto' }}>
          <canvas
            ref={canvasRef}
            style={{
              width: cols * TILE_SIZE * zoom,
              height: rows * TILE_SIZE * zoom,
              imageRendering: 'pixelated',
              cursor: mode === 'seat' ? 'crosshair' : 'cell',
            }}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseUp}
          />
        </div>

        {showExport && (
          <div className="ce-export-panel">
            <div className="ce-export-header">
              <span>Export — copy to source code</span>
              <button className="ce-btn" onClick={() => navigator.clipboard.writeText(exportText)}>Copy</button>
            </div>
            <textarea className="ce-export-textarea" value={exportText} readOnly rows={20} />
          </div>
        )}
      </div>
    </div>
  )
}
