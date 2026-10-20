import { OFFICE_COLS, OFFICE_ROWS, GAP_COLS } from '../config'

export const DEFAULT_MAP_STR: string[] = [
  '####################',
  '####################',
  '####################',
  '#######......#######',
  '#######..##..#######',
  '#######..##..#######',
  '#######..##..#######',
  '#######......#######',
  '#########.##########',
  '#########.##########',
  '#.............#....#',
  '#..................#',
  '#.#########....##..#',
  '#...........#......#',
  '#...........#..##..#',
  '#.#########.#......#',
  '#...........#......#',
  '#...........####.###',
  '##########..####.###',
  '##########..#......#',
  '#...........#..#...#',
  '#...####....#.###..#',
  '#...........#......#',
  '#.##....#####....###',
  '####################',
]

export const DEFAULT_SEATS: [number, number][] = [
  [8, 4], [8, 5], [8, 6], [11, 4], [11, 5], [11, 6],
  [3, 13], [6, 13], [9, 13], [3, 16], [6, 16], [9, 16],
  [14, 12], [17, 12], [14, 14], [17, 14],
  [15, 22],
]

export interface OfficeConfig {
  id: string
  name: string
  offsetCol: number
  mapStr: string[]
  seats: [number, number][]
  assignedAgents: string[]
}

const STORAGE_KEY = 'office-multi-config'

function makeDefaultOffices(): OfficeConfig[] {
  const step = OFFICE_COLS + GAP_COLS
  return [
    { id: 'office-0', name: 'Office A', offsetCol: 0, mapStr: [...DEFAULT_MAP_STR], seats: [...DEFAULT_SEATS], assignedAgents: [] },
    { id: 'office-1', name: 'Office B', offsetCol: step, mapStr: [...DEFAULT_MAP_STR], seats: [...DEFAULT_SEATS], assignedAgents: [] },
    { id: 'office-2', name: 'Office C', offsetCol: step * 2, mapStr: [...DEFAULT_MAP_STR], seats: [...DEFAULT_SEATS], assignedAgents: [] },
  ]
}

export const DEFAULT_OFFICES = makeDefaultOffices()

export function getOffices(): OfficeConfig[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored) as OfficeConfig[]
      if (Array.isArray(parsed) && parsed.length > 0) return parsed
    }
  } catch { /* ignore */ }
  return makeDefaultOffices()
}

export function saveOffices(offices: OfficeConfig[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(offices))
}

export function getOfficeById(id: string): OfficeConfig | undefined {
  return getOffices().find(o => o.id === id)
}

export function renameOffice(id: string, name: string) {
  const offices = getOffices()
  const office = offices.find(o => o.id === id)
  if (office) {
    office.name = name
    saveOffices(offices)
  }
}

export function assignAgent(officeId: string, agentId: string) {
  const offices = getOffices()
  for (const o of offices) {
    o.assignedAgents = o.assignedAgents.filter(a => a !== agentId)
  }
  const target = offices.find(o => o.id === officeId)
  if (target) target.assignedAgents.push(agentId)
  saveOffices(offices)
}

export function unassignAgent(agentId: string) {
  const offices = getOffices()
  for (const o of offices) {
    o.assignedAgents = o.assignedAgents.filter(a => a !== agentId)
  }
  saveOffices(offices)
}

export function getAgentOffice(agentId: string): OfficeConfig | undefined {
  return getOffices().find(o => o.assignedAgents.includes(agentId))
}

export function updateOfficeMap(officeId: string, mapStr: string[], seats: [number, number][]) {
  const offices = getOffices()
  const office = offices.find(o => o.id === officeId)
  if (office) {
    office.mapStr = mapStr
    office.seats = seats
    saveOffices(offices)
  }
}

export function parseOfficeMapStr(mapStr: string[]): number[][] {
  const grid: number[][] = []
  for (let r = 0; r < OFFICE_ROWS; r++) {
    const row: number[] = []
    const line = r < mapStr.length ? mapStr[r] : '#'.repeat(OFFICE_COLS)
    for (let c = 0; c < OFFICE_COLS; c++) {
      row.push(c < line.length && line[c] === '.' ? 0 : 1)
    }
    grid.push(row)
  }
  return grid
}

export function buildCompositeGrid(offices: OfficeConfig[]): number[][] {
  const worldCols = offices.length > 0
    ? offices[offices.length - 1].offsetCol + OFFICE_COLS
    : OFFICE_COLS
  const grid: number[][] = []
  for (let r = 0; r < OFFICE_ROWS; r++) {
    grid.push(new Array(worldCols).fill(1))
  }
  for (const office of offices) {
    const officeGrid = parseOfficeMapStr(office.mapStr)
    for (let r = 0; r < OFFICE_ROWS; r++) {
      for (let c = 0; c < OFFICE_COLS; c++) {
        grid[r][office.offsetCol + c] = officeGrid[r][c]
      }
    }
  }
  return grid
}

export function getWorkspaceSeatCount(office: OfficeConfig): number {
  let count = 0
  for (const [col, row] of office.seats) {
    if (row >= 10 && row <= 17 && col < 12) count++
    else if (row >= 18 && row <= 23 && col >= 13 && col <= 18) count++
  }
  return count
}
