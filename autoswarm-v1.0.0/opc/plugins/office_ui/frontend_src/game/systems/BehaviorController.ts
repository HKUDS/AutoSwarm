import { AgentState, Direction } from '../types'
import {
  CELEBRATE_DURATION, COFFEE_DURATION_MIN, COFFEE_DURATION_MAX,
  CHAT_DURATION_MIN, CHAT_DURATION_MAX,
  WANDER_PAUSE_MIN, WANDER_PAUSE_MAX,
  WANDER_MOVES_BEFORE_REST_MIN, WANDER_MOVES_BEFORE_REST_MAX,
  SEAT_REST_MIN, SEAT_REST_MAX,
  INACTIVE_SEAT_TIMER_MIN, INACTIVE_SEAT_TIMER_RANGE,
  TILE_SIZE,
} from '../config'
import type { OfficeScene } from '../scenes/OfficeScene'
import type { Agent } from '../entities/Agent'
import { ZONES, randomTileInZone, getOfficeLobbyDoorways } from '../map/InteractionZones'
import type { VisualEvent } from '../../types/visual'

function randomRange(min: number, max: number) { return min + Math.random() * (max - min) }
function randomInt(min: number, max: number) { return Math.floor(randomRange(min, max + 1)) }
function trimPreview(s: string, maxLen: number) { return s.length > maxLen ? s.slice(0, maxLen) + '...' : s }

const READING_TOOLS = new Set([
  'read', 'read_file', 'browse', 'list', 'list_dir',
  'glob', 'grep', 'search', 'fetch', 'web_fetch',
  'webfetch', 'web_search', 'websearch',
])

function mapToolToState(toolName: string | null): AgentState {
  if (!toolName) return AgentState.TYPE
  const t = toolName.toLowerCase().trim()
  if (t === 'reflect') return AgentState.REFLECT
  if (t === 'practice') return AgentState.PRACTICE
  if (t === 'synthesize') return AgentState.PRESENT
  if (READING_TOOLS.has(t)) return AgentState.THINK
  return AgentState.TYPE
}

function mapToolDisplay(toolName: string | null): string {
  if (!toolName) return 'Tool'
  const t = toolName.toLowerCase().trim()
  if (t === 'shell') return 'Shell'
  if (t === 'write_file' || t === 'write') return 'Write'
  if (t === 'edit_file' || t === 'edit') return 'Edit'
  if (t === 'read_file' || t === 'read') return 'Read'
  if (t === 'grep' || t === 'search') return 'Search'
  if (t === 'web_search' || t === 'websearch') return 'WebSearch'
  if (t === 'web_fetch' || t === 'webfetch') return 'Fetch'
  return toolName.slice(0, 12)
}

export class BehaviorController {
  private scene: OfficeScene
  private pendingDespawn = new Set<string>()

  constructor(scene: OfficeScene) {
    this.scene = scene
  }

  // ── Event dispatch ────────────────────────────────────

  applyEvent(evt: VisualEvent) {
    const data = evt.data ?? {}
    const agentId = evt.agent_id || 'openopc-main'

    if (agentId === 'user') return

    const isSub = agentId.startsWith('subagent-')
    const parentId = isSub
      ? (typeof data.parent_agent_id === 'string' ? data.parent_agent_id : 'openopc-main')
      : null
    const displayName = isSub ? `Sub ${agentId.slice(-4)}` : undefined

    const agent = this.scene.ensureAgent(agentId, displayName, isSub, parentId)
    agent.lastEventAt = Date.now()
    if (typeof data.task_preview === 'string') agent.taskSummary = data.task_preview
    if (typeof data.result_preview === 'string') agent.taskSummary = data.result_preview

    switch (evt.type) {
      case 'tool_start':     this.onToolStart(agent, data); break
      case 'tool_done':      this.onToolDone(agent, data); break
      case 'agent_active':   this.onAgentActive(agent); break
      case 'waiting':        this.onWaiting(agent); break
      case 'reflect_start':  this.onReflectStart(agent); break
      case 'reflect_done':   this.onReflectDone(agent); break
      case 'skill_synthesized': this.onSkillSynthesized(agent, data); break
      case 'subagent_spawn': this.onSubagentSpawn(agent, agentId, parentId); break
      case 'subagent_done':  this.onSubagentDone(agent, agentId); break
      case 'message_in':     this.onMessageIn(agent, data); break
      case 'message_out':    this.onMessageOut(agent, data); break
      case 'practice_start': this.onPracticeStart(agent, data); break
      case 'practice_done':  this.onPracticeDone(agent); break
      case 'task_routed':    this.onTaskRouted(agent, data); break
      case 'task_delegated': this.onTaskDelegated(agent, agentId, data); break
      case 'delegation_done': this.onDelegationDone(agent, data); break
      case 'agent_spawned':  this.onAgentSpawned(agent, agentId, data); break
      case 'agent_removed':  this.onAgentRemoved(agent, agentId); break
      case 'collab_started': this.onCollabStarted(agent); break
      case 'collab_ended':   this.onCollabEnded(agent); break
      case 'skill_published': this.onSkillPublished(agent, data); break
      case 'skill_adopted':   this.onSkillAdopted(agent, data); break
      case 'mycelium_transport':   this.onMyceliumTransport(agent, agentId, data); break
      case 'mycelium_crystallize': this.onMyceliumCrystallize(agent, agentId, data); break
      case 'mycelium_spore':       this.onMyceliumSpore(agent, agentId, data); break
      case 'mycelium_decompose':   this.onMyceliumDecompose(agent, agentId, data); break
      case 'mycelium_unit_created': this.onMyceliumUnitCreated(agent, data); break
      case 'mycelium_germinate':    this.onMyceliumGerminate(agent, agentId, data); break
      case 'hyphal_strengthen':     this.onHyphalStrengthen(agentId, data); break
      case 'hyphal_weaken':         this.onHyphalWeaken(agentId, data); break
    }
  }

  // ── Individual event handlers ─────────────────────────

  private onToolStart(agent: Agent, data: Record<string, unknown>) {
    const toolName = typeof data.tool_name === 'string' ? data.tool_name : 'tool'
    const label = mapToolDisplay(toolName)
    agent.urgency = 'urgent'
    agent.currentTool = toolName
    agent.isActive = true
    agent.showBubble(`${label}...`)
    this.sendToSeat(agent)
  }

  private onToolDone(agent: Agent, data: Record<string, unknown>) {
    const toolName = typeof data.tool_name === 'string' ? data.tool_name : agent.currentTool
    const label = mapToolDisplay(toolName)
    agent.currentTool = null
    agent.setAgentState(AgentState.CELEBRATE)
    agent.stateTimer = CELEBRATE_DURATION
    agent.isActive = false
    agent.showBubble(`${label} done`)
  }

  private onAgentActive(agent: Agent) {
    agent.urgency = 'urgent'
    agent.isActive = true
    if (!agent.currentTool) {
      this.sendToSeat(agent)
    }
  }

  private onWaiting(agent: Agent) {
    agent.urgency = 'relaxed'
    agent.isActive = false
    agent.currentTool = null
    agent.showBubble('Waiting')
    const moved = this.moveToZone(agent, 'breakRoom', AgentState.COFFEE)
    if (!moved) agent.setAgentState(AgentState.IDLE)
    if (moved) agent.stateTimer = randomRange(COFFEE_DURATION_MIN, COFFEE_DURATION_MAX)
  }

  private onReflectStart(agent: Agent) {
    agent.urgency = 'normal'
    agent.isActive = false
    agent.currentTool = 'Reflect'
    const moved = this.moveToZone(agent, 'meetingRoom', AgentState.REFLECT)
    if (!moved) agent.setAgentState(AgentState.REFLECT)
    agent.stateTimer = 30.0
    agent.showBubble('Reflecting...')
  }

  private onReflectDone(agent: Agent) {
    agent.currentTool = null
    agent.setAgentState(AgentState.CELEBRATE)
    agent.stateTimer = CELEBRATE_DURATION
    agent.showBubble('Insight!')
  }

  private onSkillSynthesized(agent: Agent, data: Record<string, unknown>) {
    const name = trimPreview(String(data.skill_name ?? 'new'), 20)
    agent.setAgentState(AgentState.PRESENT)
    agent.showBubble(`New Skill: ${name}`)
  }

  private onSubagentSpawn(agent: Agent, agentId: string, parentId: string | null) {
    this.placeAtDoorway(agent)
    agent.urgency = 'urgent'
    agent.isActive = true
    agent.showBubble('Spawned')
    this.sendToSeat(agent)
  }

  private onSubagentDone(agent: Agent, agentId: string) {
    agent.urgency = 'relaxed'
    agent.currentTool = null
    agent.isActive = false
    agent.showBubble('Finished')
    const moved = this.moveToDoorway(agent)
    if (moved) {
      this.pendingDespawn.add(agentId)
    } else {
      this.scene.removeAgent(agentId)
    }
  }

  private onMessageIn(agent: Agent, data: Record<string, unknown>) {
    agent.urgency = 'urgent'
    const content = typeof data.content_preview === 'string' ? data.content_preview : ''
    if (content) agent.taskSummary = content
    const preview = content ? trimPreview(content, 30) : 'New task'
    agent.showBubble(preview)
    agent.isActive = true
    this.sendToSeat(agent)
  }

  private onMessageOut(agent: Agent, data: Record<string, unknown>) {
    const content = typeof data.content_preview === 'string' ? data.content_preview : ''
    if (content) agent.taskSummary = content
    const preview = content ? trimPreview(content, 30) : 'Reply sent'
    agent.showBubble(`Reply: ${preview}`)
  }

  private onPracticeStart(agent: Agent, data: Record<string, unknown>) {
    agent.urgency = 'normal'
    const domain = typeof data.target_domain === 'string' ? data.target_domain : 'Practice'
    agent.showBubble(`Practicing: ${trimPreview(domain, 20)}`)
    agent.isActive = false
    agent.currentTool = 'Practice'
    const moved = this.moveToZone(agent, 'meetingRoom', AgentState.PRACTICE)
    if (!moved) agent.setAgentState(AgentState.PRACTICE)
  }

  private onPracticeDone(agent: Agent) {
    agent.currentTool = null
    agent.setAgentState(AgentState.CELEBRATE)
    agent.stateTimer = CELEBRATE_DURATION
    agent.showBubble('Practice done!')
  }

  private onTaskRouted(agent: Agent, data: Record<string, unknown>) {
    agent.urgency = 'urgent'
    const method = typeof data.method === 'string' ? data.method : 'auto'
    agent.isActive = true
    this.sendToSeat(agent)
    agent.showBubble(`Assigned (${method})`)
  }

  private onTaskDelegated(agent: Agent, agentId: string, data: Record<string, unknown>) {
    agent.urgency = 'normal'
    agent.stateTimer = randomRange(CHAT_DURATION_MIN, CHAT_DURATION_MAX)
    const target = typeof data.target === 'string' ? data.target : '?'
    agent.setAgentState(AgentState.CHAT)
    agent.showBubble(`Delegating to ${target}...`)
    const targetAgent = this.scene.getAgent(target)
    if (targetAgent) {
      targetAgent.parentAgentId = agentId
      targetAgent.showBubble('Receiving task...')
    }
  }

  private onDelegationDone(agent: Agent, data: Record<string, unknown>) {
    const target = typeof data.target === 'string' ? data.target : ''
    agent.setAgentState(AgentState.IDLE)
    agent.showBubble('Delegation complete')
    if (target) {
      const targetAgent = this.scene.getAgent(target)
      if (targetAgent) targetAgent.parentAgentId = null
    }
  }

  private onAgentSpawned(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const roleName = typeof data.role_name === 'string' ? data.role_name : agentId
    agent.displayName = roleName
    this.placeAtDoorway(agent)
    this.sendToSeat(agent)
    agent.showBubble(`${roleName} joined`)
  }

  private onAgentRemoved(agent: Agent, agentId: string) {
    agent.showBubble('Leaving...')
    const moved = this.moveToDoorway(agent)
    if (!moved) {
      this.scene.removeAgent(agentId)
    } else {
      this.pendingDespawn.add(agentId)
    }
  }

  private onCollabStarted(agent: Agent) {
    agent.urgency = 'normal'
    agent.isActive = false
    agent.stateTimer = randomRange(CHAT_DURATION_MIN, CHAT_DURATION_MAX)
    this.moveToZone(agent, 'meetingRoom', AgentState.CHAT)
    agent.showBubble('Collaborating...')
  }

  private onCollabEnded(agent: Agent) {
    agent.stateTimer = CELEBRATE_DURATION
    agent.setAgentState(AgentState.CELEBRATE)
    agent.showBubble('Collaboration done!')
  }

  private onSkillPublished(agent: Agent, data: Record<string, unknown>) {
    const name = trimPreview(String(data.skill_name ?? 'skill'), 20)
    agent.setAgentState(AgentState.CELEBRATE)
    agent.stateTimer = CELEBRATE_DURATION
    agent.showBubble(`Published: ${name}`)
  }

  private onSkillAdopted(agent: Agent, data: Record<string, unknown>) {
    const name = trimPreview(String(data.skill_name ?? 'skill'), 20)
    agent.showBubble(`Adopted: ${name}`)
  }

  // ── Mycelium events ───────────────────────────────────

  private onMyceliumTransport(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const source = typeof data.source === 'string' ? data.source : agentId
    const target = typeof data.target === 'string' ? data.target : null
    const domain = typeof data.domain === 'string' ? trimPreview(data.domain, 15) : '?'

    const srcAgent = this.scene.ensureAgent(source)
    srcAgent.myceliumEffect = 'transport_send'
    srcAgent.myceliumEffectTimer = 3.0
    srcAgent.showBubble(`-> [${domain}]`)

    if (target) {
      const tgtAgent = this.scene.ensureAgent(target)
      tgtAgent.myceliumEffect = 'transport_recv'
      tgtAgent.myceliumEffectTimer = 2.0
      tgtAgent.showBubble('Receiving...')
      const tgtPos = tgtAgent.getTilePos()
      srcAgent.walkTo(tgtPos.x, tgtPos.y + 1)
    }
  }

  private onMyceliumCrystallize(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const corrobAgents: string[] = Array.isArray(data.corroborating_agents)
      ? data.corroborating_agents : []
    const contentPreview = trimPreview(String(data.content_preview ?? 'Knowledge'), 22)
    const allParticipants = Array.from(new Set([agentId, ...corrobAgents]))

    const meetingZoneKey = `${agent.officeId}-meetingRoom`
    const meetingSeats = ZONES[meetingZoneKey]?.seats ?? []
    for (let i = 0; i < allParticipants.length; i++) {
      const pid = allParticipants[i]
      const pAgent = this.scene.ensureAgent(pid)
      pAgent.myceliumEffect = 'crystal'
      pAgent.myceliumEffectTimer = 15.0
      pAgent.isActive = false

      const seatIdx = i % meetingSeats.length
      const seat = meetingSeats[seatIdx]
      pAgent.walkTo(seat.tileX, seat.tileY, () => {
        pAgent.setDirection(seat.facing)
        pAgent.setAgentState(AgentState.CHAT)
      })

      pAgent.showBubble(pid === agentId ? `Crystal: ${contentPreview}` : 'Crystal!')
    }
  }

  private onMyceliumSpore(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const preview = trimPreview(String(data.content_preview ?? 'Breakthrough'), 20)
    agent.myceliumEffect = 'spore_send'
    agent.myceliumEffectTimer = 4.0
    agent.setAgentState(AgentState.PRESENT)
    agent.showBubble(`Breakthrough! ${preview}`)
    // Move to break room for broadcast
    this.moveToZone(agent, 'breakRoom')
  }

  private onMyceliumDecompose(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const preview = trimPreview(String(data.humus_preview ?? 'Lesson learned'), 22)
    agent.myceliumEffect = 'decompose'
    agent.myceliumEffectTimer = 4.0
    agent.setAgentState(AgentState.REFLECT)
    agent.showBubble(`Learning: ${preview}`)
    this.moveToZone(agent, 'meetingRoom')
  }

  private onMyceliumUnitCreated(agent: Agent, data: Record<string, unknown>) {
    const nutrientType = String(data.nutrient_type ?? 'insight').slice(0, 1).toUpperCase()
    agent.showBubble(`[${nutrientType}]...`, 2.0)
  }

  private onMyceliumGerminate(agent: Agent, agentId: string, data: Record<string, unknown>) {
    const targetAgent = typeof data.target_agent === 'string' ? data.target_agent : agentId
    const tgt = this.scene.ensureAgent(targetAgent)
    tgt.myceliumEffect = null
    tgt.setAgentState(AgentState.CELEBRATE)
    tgt.stateTimer = CELEBRATE_DURATION
    tgt.showBubble('Insight took root!')
  }

  private onHyphalStrengthen(_agentId: string, _data: Record<string, unknown>) {
    // Visual connection tracking could be added here
  }

  private onHyphalWeaken(_agentId: string, _data: Record<string, unknown>) {
    // Visual connection tracking could be added here
  }

  // ── Idle behavior (called each frame) ─────────────────

  updateIdle(dt: number) {
    for (const agent of this.scene.agents.values()) {
      // Check pending despawn
      if (this.pendingDespawn.has(agent.agentId) && !agent.isMoving) {
        this.pendingDespawn.delete(agent.agentId)
        this.scene.removeAgent(agent.agentId)
        continue
      }

      // Mycelium effect timer
      if (agent.myceliumEffectTimer > 0) {
        agent.myceliumEffectTimer -= dt
        if (agent.myceliumEffectTimer <= 0) {
          agent.myceliumEffect = null
          agent.myceliumEffectTimer = 0
          agent.myceliumSession = null
        }
      }

      // Non-active agents sitting at desk: count down seatTimer then transition to IDLE
      if (agent.agentState === AgentState.TYPE && !agent.isActive) {
        if (agent.seatTimer > 0) {
          agent.seatTimer -= dt
          if (agent.seatTimer <= 0) {
            agent.seatTimer = 0
            agent.setAgentState(AgentState.IDLE)
            agent.wanderCount = 0
            agent.wanderLimit = randomInt(WANDER_MOVES_BEFORE_REST_MIN, WANDER_MOVES_BEFORE_REST_MAX)
            agent.wanderTimer = randomRange(WANDER_PAUSE_MIN, WANDER_PAUSE_MAX)
          }
        } else {
          agent.setAgentState(AgentState.IDLE)
        }
        continue
      }

      if (agent.agentState !== AgentState.IDLE) continue

      // Active agents should go to seat
      if (agent.isActive) {
        agent.urgency = 'urgent'
        if (!agent.seatId) {
          agent.setAgentState(AgentState.TYPE)
          continue
        }
        this.sendToSeat(agent)
        continue
      }

      // Idle wander logic
      agent.wanderTimer -= dt
      if (agent.wanderTimer <= 0) {
        // Wander limit reached => go back to seat and rest
        if (agent.wanderCount >= agent.wanderLimit && agent.seatId) {
          agent.urgency = 'relaxed'
          this.sendToSeat(agent)
          agent.wanderTimer = randomRange(WANDER_PAUSE_MIN, WANDER_PAUSE_MAX)
          continue
        }

        // Random wander (confined to agent's office)
        const tiles = this.scene.getWalkableTilesForOffice(agent.officeId)
        if (tiles.length > 0) {
          agent.urgency = 'relaxed'
          const target = tiles[Math.floor(Math.random() * tiles.length)]
          agent.walkTo(target.x, target.y)
          agent.wanderCount++
        }
        agent.wanderTimer = randomRange(WANDER_PAUSE_MIN, WANDER_PAUSE_MAX)
      }
    }
  }

  // ── Movement helpers ──────────────────────────────────

  sendToSeat(agent: Agent) {
    if (!agent.seatId) return
    const seat = this.scene.getSeatById(agent.seatId)
    if (!seat) return

    agent.walkTo(seat.tileX, seat.tileY, () => {
      agent.setAgentState(AgentState.TYPE)
      agent.setDirection(seat.facing)
      if (!agent.isActive) {
        agent.seatTimer = INACTIVE_SEAT_TIMER_MIN + Math.random() * INACTIVE_SEAT_TIMER_RANGE
      }
    }).then(moved => {
      if (!moved) {
        agent.setAgentState(AgentState.TYPE)
        agent.setDirection(seat.facing)
        if (!agent.isActive) {
          agent.seatTimer = INACTIVE_SEAT_TIMER_MIN + Math.random() * INACTIVE_SEAT_TIMER_RANGE
        }
      }
    })
  }

  moveToZone(agent: Agent, zoneName: string, arrivalState?: string): boolean {
    const zoneKey = `${agent.officeId}-${zoneName}`
    const zone = ZONES[zoneKey]
    if (!zone) return false

    if (zone.seats.length > 0) {
      const occupiedTiles = new Set<string>()
      for (const other of this.scene.agents.values()) {
        if (other === agent) continue
        if (other.isMoving) continue
        const pos = other.getTilePos()
        occupiedTiles.add(`${pos.x},${pos.y}`)
      }
      for (const other of this.scene.agents.values()) {
        if (other === agent) continue
        if (!other.isMoving) continue
        const path = (other as any).currentPath as { x: number; y: number }[]
        if (path?.length) {
          const dest = path[path.length - 1]
          occupiedTiles.add(`${dest.x},${dest.y}`)
        }
      }
      const freeSeat = zone.seats.find(s => !occupiedTiles.has(`${s.tileX},${s.tileY}`))
      if (freeSeat) {
        agent.walkTo(freeSeat.tileX, freeSeat.tileY, () => {
          agent.setDirection(freeSeat.facing)
          if (arrivalState) agent.setAgentState(arrivalState as any)
          else agent.setAgentState(AgentState.IDLE)
        })
        return true
      }
    }

    const pos = randomTileInZone(zoneKey)
    if (!pos) return false
    agent.walkTo(pos.x, pos.y, () => {
      if (arrivalState) agent.setAgentState(arrivalState as any)
      else agent.setAgentState(AgentState.IDLE)
    })
    return true
  }

  moveToDoorway(agent: Agent): boolean {
    const doorways = getOfficeLobbyDoorways(agent.officeId)
    if (doorways.length === 0) return false
    const target = doorways[Math.floor(Math.random() * doorways.length)]
    agent.walkTo(target.x, target.y)
    return true
  }

  placeAtDoorway(agent: Agent) {
    const doorways = getOfficeLobbyDoorways(agent.officeId)
    if (doorways.length > 0) {
      const d = doorways[Math.floor(Math.random() * doorways.length)]
      agent.setPosition(d.x * TILE_SIZE + TILE_SIZE / 2, d.y * TILE_SIZE + TILE_SIZE / 2)
    }
  }
}
