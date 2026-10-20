import type { Direction, SeatDef, InteractableDef } from '../types'
import { OFFICE_COLS, OFFICE_ROWS } from '../config'
import { getOffices, parseOfficeMapStr, type OfficeConfig } from './OfficeStore'

export interface ZoneDef {
  name: string
  bounds: { x: number; y: number; w: number; h: number }
  seats: SeatDef[]
  interactables: InteractableDef[]
  doorways: { id: string; tileX: number; tileY: number }[]
}

function seat(id: string, tileX: number, tileY: number, facing: Direction): SeatDef {
  return { id, tileX, tileY, facing, assigned: false, assignedTo: null }
}

function interactable(id: string, tileX: number, tileY: number, type: string): InteractableDef {
  return { id, tileX, tileY, type }
}

interface ZoneTemplate {
  name: string
  localBounds: { x: number; y: number; w: number; h: number }
  interactables: { id: string; localX: number; localY: number; type: string }[]
  doorways: { id: string; localX: number; localY: number }[]
}

const ZONE_TEMPLATES: Record<string, ZoneTemplate> = {
  meetingRoom: {
    name: 'Meeting Room',
    localBounds: { x: 7, y: 3, w: 6, h: 5 },
    interactables: [{ id: 'whiteboard', localX: 9, localY: 1, type: 'whiteboard' }],
    doorways: [{ id: 'door-meeting', localX: 9, localY: 9 }],
  },
  workspace: {
    name: 'Workspace',
    localBounds: { x: 1, y: 10, w: 11, h: 7 },
    interactables: [{ id: 'printer', localX: 10, localY: 10, type: 'printer' }],
    doorways: [{ id: 'door-ws', localX: 9, localY: 10 }],
  },
  breakRoom: {
    name: 'Break Room',
    localBounds: { x: 13, y: 10, w: 6, h: 8 },
    interactables: [
      { id: 'coffee-machine', localX: 18, localY: 10, type: 'coffee_machine' },
      { id: 'fridge', localX: 18, localY: 11, type: 'fridge' },
    ],
    doorways: [{ id: 'door-break', localX: 13, localY: 10 }],
  },
  leaderOffice: {
    name: 'Leader Office',
    localBounds: { x: 13, y: 18, w: 6, h: 6 },
    interactables: [],
    doorways: [{ id: 'door-leader', localX: 16, localY: 17 }],
  },
  lobby: {
    name: 'Lobby',
    localBounds: { x: 1, y: 19, w: 12, h: 5 },
    interactables: [],
    doorways: [{ id: 'entrance', localX: 10, localY: 18 }],
  },
}

function inferFacing(col: number, row: number, zoneName: string): Direction {
  switch (zoneName) {
    case 'workspace':
      return 'up'
    case 'meetingRoom': {
      const tableCenterX = 9.5
      return col < tableCenterX ? 'right' : 'left'
    }
    case 'breakRoom': {
      const tableCenterX = 15.5
      return col < tableCenterX ? 'right' : 'left'
    }
    case 'leaderOffice':
      return 'up'
    default:
      return 'down'
  }
}

function classifyLocalSeat(col: number, row: number): string {
  for (const [name, z] of Object.entries(ZONE_TEMPLATES)) {
    const { x, y, w, h } = z.localBounds
    if (col >= x && col < x + w && row >= y && row < y + h) return name
  }
  return 'lobby'
}

export function buildZonesForOffice(office: OfficeConfig): Record<string, ZoneDef> {
  const off = office.offsetCol
  const zones: Record<string, ZoneDef> = {}

  for (const [zoneKey, tmpl] of Object.entries(ZONE_TEMPLATES)) {
    const globalKey = `${office.id}-${zoneKey}`
    zones[globalKey] = {
      name: `${tmpl.name} (${office.name})`,
      bounds: {
        x: tmpl.localBounds.x + off,
        y: tmpl.localBounds.y,
        w: tmpl.localBounds.w,
        h: tmpl.localBounds.h,
      },
      seats: [],
      interactables: tmpl.interactables.map(i =>
        interactable(`${office.id}-${i.id}`, i.localX + off, i.localY, i.type),
      ),
      doorways: tmpl.doorways.map(d => ({
        id: `${office.id}-${d.id}`,
        tileX: d.localX + off,
        tileY: d.localY,
      })),
    }
  }

  const counters: Record<string, number> = {}
  for (const [col, row] of office.seats) {
    const localZone = classifyLocalSeat(col, row)
    const globalKey = `${office.id}-${localZone}`
    if (!zones[globalKey]) continue
    counters[globalKey] = (counters[globalKey] ?? 0) + 1
    const idx = counters[globalKey]
    const prefix = localZone === 'workspace' ? 'desk' : localZone === 'meetingRoom' ? 'meeting' : localZone === 'breakRoom' ? 'break' : localZone === 'leaderOffice' ? 'leader' : 'lobby'
    const facing = inferFacing(col, row, localZone)
    zones[globalKey].seats.push(seat(`${office.id}-${prefix}-${idx}`, col + off, row, facing))
  }

  return zones
}

export function buildAllZones(offices?: OfficeConfig[]): Record<string, ZoneDef> {
  const all = offices ?? getOffices()
  const zones: Record<string, ZoneDef> = {}
  for (const office of all) {
    Object.assign(zones, buildZonesForOffice(office))
  }
  return zones
}

export let ZONES: Record<string, ZoneDef> = buildAllZones()

export function reloadZones(offices?: OfficeConfig[]) {
  ZONES = buildAllZones(offices)
}

export function getOfficeZoneKey(officeId: string, zoneName: string): string {
  return `${officeId}-${zoneName}`
}

export function getOfficeDeskSeats(officeId: string): SeatDef[] {
  const desks = ZONES[`${officeId}-workspace`]?.seats ?? []
  const leaders = ZONES[`${officeId}-leaderOffice`]?.seats ?? []
  return [...desks, ...leaders]
}

export function getOfficeAllSeats(officeId: string): SeatDef[] {
  return Object.entries(ZONES)
    .filter(([k]) => k.startsWith(`${officeId}-`))
    .flatMap(([, z]) => z.seats)
}

export function getAllDeskSeats(): SeatDef[] {
  return Object.entries(ZONES)
    .filter(([k]) => k.endsWith('-workspace'))
    .flatMap(([, z]) => z.seats)
}

export function getMeetingSeats(officeId?: string): SeatDef[] {
  if (officeId) return ZONES[`${officeId}-meetingRoom`]?.seats ?? []
  return Object.entries(ZONES)
    .filter(([k]) => k.endsWith('-meetingRoom'))
    .flatMap(([, z]) => z.seats)
}

export function getAllSeats(): SeatDef[] {
  return Object.values(ZONES).flatMap(z => z.seats)
}

export function randomTileInZone(zoneKey: string): { x: number; y: number } | null {
  const zone = ZONES[zoneKey]
  if (!zone) return null
  const { x, y, w, h } = zone.bounds

  const officeId = zoneKey.split('-').slice(0, 2).join('-')
  const offices = getOffices()
  const office = offices.find(o => o.id === officeId)
  if (!office) return { x: x + 1, y: y + 1 }

  const grid = parseOfficeMapStr(office.mapStr)
  const off = office.offsetCol
  const walkable: { x: number; y: number }[] = []
  for (let row = y; row < y + h; row++) {
    for (let col = x; col < x + w; col++) {
      const localCol = col - off
      if (localCol >= 0 && localCol < OFFICE_COLS && row < OFFICE_ROWS && grid[row]?.[localCol] === 0) {
        walkable.push({ x: col, y: row })
      }
    }
  }
  if (walkable.length === 0) return null
  return walkable[Math.floor(Math.random() * walkable.length)]
}

export function getDoorwayTargets(zoneKey: string): { x: number; y: number }[] {
  const zone = ZONES[zoneKey]
  if (!zone) return []
  return zone.doorways.map(d => ({ x: d.tileX, y: d.tileY }))
}

export function getOfficeLobbyDoorways(officeId: string): { x: number; y: number }[] {
  const zone = ZONES[`${officeId}-lobby`]
  if (!zone) return []
  return zone.doorways.map(d => ({ x: d.tileX, y: d.tileY }))
}
