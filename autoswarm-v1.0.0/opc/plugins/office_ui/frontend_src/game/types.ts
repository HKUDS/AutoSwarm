export const AgentState = {
  IDLE: 'idle',
  WALK: 'walk',
  TYPE: 'type',
  THINK: 'think',
  CELEBRATE: 'celebrate',
  COFFEE: 'coffee',
  CHAT: 'chat',
  PRACTICE: 'practice',
  REFLECT: 'reflect',
  SLEEP: 'sleep',
  PRESENT: 'present',
} as const
export type AgentState = (typeof AgentState)[keyof typeof AgentState]

export const Direction = {
  DOWN: 'down',
  LEFT: 'left',
  RIGHT: 'right',
  UP: 'up',
} as const
export type Direction = (typeof Direction)[keyof typeof Direction]

export interface SeatDef {
  id: string
  tileX: number
  tileY: number
  facing: Direction
  assigned: boolean
  assignedTo: string | null
}

export interface InteractableDef {
  id: string
  tileX: number
  tileY: number
  type: string
}

export interface ZoneDef {
  bounds: { x: number; y: number; w: number; h: number }
  seats: SeatDef[]
  interactables: InteractableDef[]
  doorways: { id: string; tileX: number; tileY: number }[]
}

export interface AgentInfo {
  id: string
  displayName: string
  state: AgentState
  isActive: boolean
  currentTool: string | null
  seatId: string | null
  urgency: 'urgent' | 'normal' | 'relaxed'
  bubble: string | null
  bubbleTimer: number
  isSubagent: boolean
  parentAgentId: string | null
  palette: number
  hueShift: number
  taskSummary?: string
  lastEventAt: number
  wanderTimer: number
  wanderCount: number
  wanderLimit: number
  seatTimer: number
  stateTimer: number
  myceliumEffect: 'crystal' | 'transport_send' | 'transport_recv' | 'spore_send' | 'decompose' | null
  myceliumEffectTimer: number
  myceliumSession: string | null
}
