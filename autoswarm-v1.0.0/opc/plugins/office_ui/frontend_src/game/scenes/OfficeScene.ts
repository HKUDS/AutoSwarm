import Phaser from 'phaser'
import {
  TILE_SIZE, MAP_COLS, MAP_ROWS, OFFICE_COLS, OUTDOOR_MARGIN_X, OUTDOOR_MARGIN_TOP, OUTDOOR_MARGIN_BOTTOM,
  isLocalDaytime, SCENE_CLEAR_DAY, SCENE_CLEAR_NIGHT,
} from '../config'
import { OfficeMapBuilder, type MapData } from '../map/OfficeMapBuilder'
import { Agent } from '../entities/Agent'
import { PathfindingManager } from '../systems/PathfindingManager'
import { BehaviorController } from '../systems/BehaviorController'
import type { GameBridge } from '../GameBridge'
import { getAllSeats, getOfficeDeskSeats, reloadZones } from '../map/InteractionZones'
import {
  getOffices, assignAgent as storeAssignAgent,
  updateOfficeMap, buildCompositeGrid, renameOffice as storeRenameOffice,
  getAgentOffice,
  type OfficeConfig,
} from '../map/OfficeStore'
import { AgentState, type SeatDef } from '../types'

export class OfficeScene extends Phaser.Scene {
  mapData!: MapData
  pathfinder!: PathfindingManager
  behavior!: BehaviorController
  bridge!: GameBridge
  mapBuilder!: OfficeMapBuilder
  private yachtLoopStarted = false
  private outdoorIsDay: boolean | null = null

  agents: Map<string, Agent> = new Map()
  seats: SeatDef[] = []
  walkableTiles: { x: number; y: number }[] = []
  private walkableTilesByOffice: Map<string, { x: number; y: number }[]> = new Map()

  private dragState: {
    pointerId: number | null
    lastX: number
    lastY: number
    moved: boolean
  } = {
    pointerId: null,
    lastX: 0,
    lastY: 0,
    moved: false,
  }

  constructor() {
    super('Office')
  }

  create() {
    this.bridge = this.registry.get('bridge') as GameBridge

    const isDay = isLocalDaytime()
    this.outdoorIsDay = isDay
    this.cameras.main.setBackgroundColor(isDay ? SCENE_CLEAR_DAY : SCENE_CLEAR_NIGHT)

    this.mapBuilder = new OfficeMapBuilder()
    this.mapData = this.mapBuilder.buildMap(this, isDay)

    this.pathfinder = new PathfindingManager(this.mapData.collisionGrid)
    this.walkableTiles = this.pathfinder.getWalkableTiles()
    this.buildWalkableTilesByOffice()

    this.seats = getAllSeats().map(s => ({ ...s }))

    this.behavior = new BehaviorController(this)

    const mapW = MAP_COLS * TILE_SIZE
    const mapH = MAP_ROWS * TILE_SIZE
    const worldX = -OUTDOOR_MARGIN_X
    const worldY = -OUTDOOR_MARGIN_TOP
    const worldW = mapW + OUTDOOR_MARGIN_X * 2
    const worldH = mapH + OUTDOOR_MARGIN_TOP + OUTDOOR_MARGIN_BOTTOM
    this.physics.world.setBounds(worldX, worldY, worldW, worldH)
    this.cameras.main.setBounds(worldX, worldY, worldW, worldH)

    this.resetCameraView(false)
    this.startWaterfrontLoop()

    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (!pointer.leftButtonDown()) return
      this.dragState.pointerId = pointer.id
      this.dragState.lastX = pointer.x
      this.dragState.lastY = pointer.y
      this.dragState.moved = false
    })
    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (!pointer.isDown || this.dragState.pointerId !== pointer.id) return
      const dx = pointer.x - this.dragState.lastX
      const dy = pointer.y - this.dragState.lastY
      if (!this.dragState.moved && Math.abs(pointer.downX - pointer.x) + Math.abs(pointer.downY - pointer.y) < 6) {
        return
      }
      this.dragState.moved = true
      const cam = this.cameras.main
      cam.stopFollow()
      cam.scrollX -= dx / cam.zoom
      cam.scrollY -= dy / cam.zoom
      this.dragState.lastX = pointer.x
      this.dragState.lastY = pointer.y
    })
    this.input.on('wheel', (_p: Phaser.Input.Pointer, _g: Phaser.GameObjects.GameObject[], _dx: number, dy: number) => {
      const cam = this.cameras.main
      const pointer = this.input.activePointer
      const before = cam.getWorldPoint(pointer.x, pointer.y)
      const minZ = this.getMinCameraZoom(cam)
      const nextZoom = Phaser.Math.Clamp(cam.zoom - dy * 0.0015, minZ, 3)
      cam.setZoom(nextZoom)
      const after = cam.getWorldPoint(pointer.x, pointer.y)
      cam.scrollX += before.x - after.x
      cam.scrollY += before.y - after.y
      cam.scrollX = cam.clampX(cam.scrollX)
      cam.scrollY = cam.clampY(cam.scrollY)
    })

    this.scale.on('resize', () => {
      const cam = this.cameras.main
      const minZ = this.getMinCameraZoom(cam)
      if (cam.zoom < minZ) cam.setZoom(minZ)
      cam.scrollX = cam.clampX(cam.scrollX)
      cam.scrollY = cam.clampY(cam.scrollY)
    })

    this.time.addEvent({
      delay: 45000,
      loop: true,
      callback: this.checkOutdoorDayNight,
      callbackScope: this,
    })

    this.input.on('pointerup', (pointer: Phaser.Input.Pointer) => {
      if (this.dragState.pointerId !== pointer.id) return
      const dragged = this.dragState.pointerId === pointer.id && this.dragState.moved
      this.dragState.pointerId = null
      this.dragState.moved = false
      if (dragged) return
      const wp = this.cameras.main.getWorldPoint(pointer.x, pointer.y)
      let closest: Agent | null = null
      let closestDist = Infinity
      for (const agent of this.agents.values()) {
        const d = Phaser.Math.Distance.Between(wp.x, wp.y, agent.x, agent.y)
        if (d < 24 && d < closestDist) { closest = agent; closestDist = d }
      }
      if (closest) {
        this.bridge.emit('agentSelected', closest.agentId)
        this.cameras.main.startFollow(closest, true, 0.1, 0.1)
      }
    })

    if (this.bridge) this.bridge.setScene(this)

    console.log('[OfficeScene] Ready — walkable tiles:', this.walkableTiles.length)
  }

  update(_time: number, delta: number) {
    const dt = delta / 1000
    for (const agent of this.agents.values()) agent.update(dt)
    this.behavior.updateIdle(dt)
  }

  private startWaterfrontLoop() {
    if (this.yachtLoopStarted) return
    this.yachtLoopStarted = true

    const { dockCenterX, dockY, waterTopY, waterBottomY } = this.mapData.waterfront
    const yachtScale = 3
    const boatY = Phaser.Math.Clamp(dockY + TILE_SIZE * 5.0, waterTopY + TILE_SIZE * 3.8, waterBottomY - TILE_SIZE * 4.6)
    const startX = dockCenterX - TILE_SIZE * 20
    const dockX = dockCenterX + TILE_SIZE * 1.65
    const exitX = dockCenterX + TILE_SIZE * 19

    const shadow = this.add.ellipse(0, 13, 122, 24, 0x173443, 0.28)
    const wakeA = this.add.ellipse(-78, 8, 24, 7, 0xeaf8ff, 0.42)
    const wakeB = this.add.ellipse(-92, 9, 16, 5, 0xd7eef7, 0.3)
    const wakeC = this.add.ellipse(-106, 10, 10, 4, 0xbfdceb, 0.18)
    const hullMain = this.add.rectangle(-2, 6, 94, 16, 0xf6f7f8, 1)
    const sternBlock = this.add.rectangle(-49, 5, 12, 14, 0xe5e8ec, 1)
    const bowMid = this.add.rectangle(44, 4, 12, 12, 0xf6f7f8, 1)
    const bowTip = this.add.triangle(56, 4, 0, -6, 13, 0, 0, 6, 0xf6f7f8, 1)
    const waterline = this.add.rectangle(-1, 11, 66, 4, 0xb9c3cb, 0.95)
    const hullStripe = this.add.rectangle(6, 3, 74, 3, 0x8dbfd8, 0.95)
    const lowerCabin = this.add.rectangle(-6, -3, 52, 10, 0xffffff, 1)
    const lowerCabinAft = this.add.rectangle(-28, -2, 18, 8, 0xecf0f2, 1)
    const bridgeBase = this.add.rectangle(18, -5, 18, 8, 0xffffff, 1)
    const upperDeck = this.add.rectangle(6, -13, 40, 8, 0xf8fafb, 1)
    const upperDeckAft = this.add.rectangle(-18, -12, 18, 6, 0xf0f4f6, 1)
    const bridgeGlass = this.add.rectangle(19, -5, 14, 4, 0x97d3f7, 0.92)
    const deckGlassBand = this.add.rectangle(0, -3, 42, 3, 0xcfe9f7, 0.88)
    const rail = this.add.rectangle(4, -16, 44, 2, 0xd9e1e6, 0.96)
    const mast = this.add.rectangle(7, -25, 2, 9, 0xe9edf1, 0.95)
    const radar = this.add.rectangle(11, -28, 9, 2, 0xe9edf1, 0.95)
    const flag = this.add.triangle(17, -27, 0, -3, 7, 0, 0, 3, 0x9fc5dc, 0.95)
    const yacht = this.add.container(startX, boatY, [
      shadow,
      wakeC,
      wakeB,
      wakeA,
      sternBlock,
      hullMain,
      bowMid,
      bowTip,
      hullStripe,
      waterline,
      lowerCabinAft,
      lowerCabin,
      bridgeBase,
      upperDeckAft,
      upperDeck,
      bridgeGlass,
      deckGlassBand,
      rail,
      mast,
      radar,
      flag,
    ])
    const portholes = [-26, -10, 8, 26].map(px => this.add.circle(px, 5, 2.1, 0xd9f4ff, 0.95))
    yacht.add(portholes)

    yacht.setDepth(-250)
    yacht.setAlpha(0)
    yacht.setRotation(-0.04)
    yacht.setScale(yachtScale)
    this.tweens.add({
      targets: [wakeA, wakeB, wakeC],
      scaleX: { from: 0.85, to: 1.2 },
      alpha: { from: 0.5, to: 0.2 },
      duration: 900,
      yoyo: true,
      repeat: -1,
    })

    const runCycle = () => {
      yacht.setPosition(startX, boatY)
      yacht.setAlpha(0)
      yacht.setRotation(-0.04)
      wakeA.setAlpha(0.55)
      wakeB.setAlpha(0.42)
      wakeC.setAlpha(0.24)

      this.tweens.add({
        targets: yacht,
        x: dockX,
        alpha: 1,
        rotation: 0.015,
        duration: 7200,
        ease: 'Sine.InOut',
        onComplete: () => {
          wakeA.setAlpha(0.22)
          wakeB.setAlpha(0.14)
          wakeC.setAlpha(0.08)
          this.tweens.add({
            targets: yacht,
            x: dockX + 4,
            y: boatY + TILE_SIZE * 0.12,
            duration: 1400,
            yoyo: true,
            repeat: 2,
            ease: 'Sine.InOut',
          })

          this.time.delayedCall(3600, () => {
            wakeA.setAlpha(0.48)
            wakeB.setAlpha(0.32)
            wakeC.setAlpha(0.18)
            this.tweens.add({
              targets: yacht,
              x: exitX,
              alpha: 0,
              rotation: -0.055,
              duration: 7600,
              ease: 'Sine.InOut',
              onComplete: () => {
                this.time.delayedCall(2400, runCycle)
              },
            })
          })
        },
      })
    }

    runCycle()
  }

  // ── Office helpers ──────────────────────────────────

  private buildWalkableTilesByOffice() {
    this.walkableTilesByOffice.clear()
    const offices = getOffices()
    for (const office of offices) {
      const tiles: { x: number; y: number }[] = []
      for (const t of this.walkableTiles) {
        if (t.x >= office.offsetCol && t.x < office.offsetCol + OFFICE_COLS) {
          tiles.push(t)
        }
      }
      this.walkableTilesByOffice.set(office.id, tiles)
    }
  }

  getWalkableTilesForOffice(officeId: string): { x: number; y: number }[] {
    return this.walkableTilesByOffice.get(officeId) ?? []
  }

  resetCameraView(animate = true) {
    const cam = this.cameras.main
    const { worldW, worldH } = this.getWorldSize()
    const minZ = this.getMinCameraZoom(cam)
    const fitZoom = Math.min(this.scale.width / worldW, this.scale.height / worldH)
    const maxReset = Math.max(1.22, minZ)
    const targetZoom = Phaser.Math.Clamp(Math.max(fitZoom * 1.18, 0.62), minZ, maxReset)
    const targetX = MAP_COLS * TILE_SIZE / 2
    const targetY = (MAP_ROWS * TILE_SIZE - OUTDOOR_MARGIN_TOP + OUTDOOR_MARGIN_BOTTOM) / 2 + TILE_SIZE * 2.6
    cam.stopFollow()
    if (!animate) {
      cam.setZoom(targetZoom)
      cam.centerOn(targetX, targetY)
      return
    }
    this.tweens.add({
      targets: cam,
      zoom: targetZoom,
      duration: 260,
      ease: 'Cubic.easeOut',
    })
    cam.pan(targetX, targetY, 260, 'Cubic.easeOut')
  }

  panToOffice(officeId: string) {
    const offices = getOffices()
    const office = offices.find(o => o.id === officeId)
    if (!office) return
    const cx = (office.offsetCol + OFFICE_COLS / 2) * TILE_SIZE
    const cy = (MAP_ROWS / 2) * TILE_SIZE
    const cam = this.cameras.main
    const minZ = this.getMinCameraZoom(cam)
    const targetZoom = Phaser.Math.Clamp(
      Math.min(this.scale.width / ((OFFICE_COLS + 4) * TILE_SIZE), this.scale.height / ((MAP_ROWS - 4) * TILE_SIZE)),
      minZ,
      Math.max(1.5, minZ),
    )
    cam.stopFollow()
    this.tweens.add({
      targets: cam,
      zoom: targetZoom,
      duration: 380,
      ease: 'Cubic.easeOut',
    })
    cam.pan(cx, cy, 380, 'Cubic.easeOut')
  }

  // ── Agent management ──────────────────────────────────

  resolveOfficeForAgent(agentId: string): string {
    const stored = getAgentOffice(agentId)
    if (stored) return stored.id
    const offices = getOffices()
    let best: OfficeConfig | null = null
    let bestFree = -1
    for (const o of offices) {
      const deskSeats = getOfficeDeskSeats(o.id)
      const assignedCount = o.assignedAgents.length
      const free = deskSeats.length - assignedCount
      if (free > bestFree) { best = o; bestFree = free }
    }
    const officeId = best?.id ?? offices[0]?.id ?? 'office-0'
    storeAssignAgent(officeId, agentId)
    return officeId
  }

  addAgent(agentId: string, displayName?: string, isSubagent = false, parentAgentId: string | null = null, backendOfficeId?: string, backendPalette?: number, backendDeskId?: string): Agent {
    if (this.agents.has(agentId)) return this.agents.get(agentId)!

    const name = displayName ?? agentId
    const palette = backendPalette ?? (this.agents.size % 6)

    // Use backend office_id if provided, otherwise fall back to local resolution
    const officeId = backendOfficeId
      ? (() => { storeAssignAgent(backendOfficeId, agentId); return backendOfficeId })()
      : this.resolveOfficeForAgent(agentId)

    // Use backend desk_id for precise seat, otherwise find free seat
    const seat = backendDeskId
      ? (this.seats.find(s => s.id === backendDeskId && !s.assigned) ?? this.findFreeSeatInOffice(officeId))
      : this.findFreeSeatInOffice(officeId)

    const offices = getOffices()
    const office = offices.find(o => o.id === officeId)
    const fallbackX = (office?.offsetCol ?? 0) + 5
    const fallbackY = 20

    let startX = fallbackX
    let startY = fallbackY
    if (seat) {
      startX = seat.tileX
      startY = seat.tileY
      seat.assigned = true
      seat.assignedTo = agentId
    }

    const agent = new Agent(this, agentId, name, palette, startX, startY)
    agent.officeId = officeId
    agent.setPathfinder(this.pathfinder)
    agent.seatId = seat?.id ?? null
    agent.isSubagent = isSubagent
    agent.parentAgentId = parentAgentId

    if (seat) {
      agent.setAgentState(AgentState.TYPE)
      agent.setDirection(seat.facing)
      agent.seatTimer = 10
    }

    this.agents.set(agentId, agent)
    return agent
  }

  removeAgent(agentId: string) {
    const agent = this.agents.get(agentId)
    if (!agent) return
    if (agent.seatId) {
      const seat = this.seats.find(s => s.id === agent.seatId)
      if (seat) { seat.assigned = false; seat.assignedTo = null }
    }
    agent.destroy()
    this.agents.delete(agentId)
    try {
      const { unassignAgent } = require('../map/OfficeStore')
      unassignAgent(agentId)
    } catch { /* OfficeStore may not be available */ }
  }

  getAgent(agentId: string): Agent | undefined { return this.agents.get(agentId) }

  ensureAgent(agentId: string, displayName?: string, isSubagent = false, parentAgentId: string | null = null, backendOfficeId?: string, backendPalette?: number, backendDeskId?: string): Agent {
    const existing = this.agents.get(agentId)
    if (existing) {
      // If backend specifies a different office, reassign
      if (backendOfficeId && existing.officeId !== backendOfficeId) {
        this.reassignAgent(agentId, backendOfficeId)
      }
      // If backend specifies a specific desk, move to it
      if (backendDeskId && existing.seatId !== backendDeskId) {
        this.changeAgentSeat(agentId, backendDeskId)
      }
      return existing
    }
    return this.addAgent(agentId, displayName, isSubagent, parentAgentId, backendOfficeId, backendPalette, backendDeskId)
  }

  findFreeSeatInOffice(officeId: string): SeatDef | null {
    const deskSeat = this.seats.filter(s => s.id.startsWith(`${officeId}-desk-`)).find(s => !s.assigned)
    if (deskSeat) return deskSeat
    return this.seats.filter(s => s.id.startsWith(`${officeId}-leader-`)).find(s => !s.assigned) ?? null
  }

  getSeatById(id: string): SeatDef | undefined { return this.seats.find(s => s.id === id) }

  reassignAgent(agentId: string, newOfficeId: string) {
    const agent = this.agents.get(agentId)
    if (!agent) return

    if (agent.seatId) {
      const oldSeat = this.seats.find(s => s.id === agent.seatId)
      if (oldSeat) { oldSeat.assigned = false; oldSeat.assignedTo = null }
    }
    agent.stopMovement()

    storeAssignAgent(newOfficeId, agentId)
    agent.officeId = newOfficeId

    const newSeat = this.findFreeSeatInOffice(newOfficeId)
    const offices = getOffices()
    const office = offices.find(o => o.id === newOfficeId)
    const fallbackX = (office?.offsetCol ?? 0) + 5
    const fallbackY = 20

    if (newSeat) {
      newSeat.assigned = true
      newSeat.assignedTo = agentId
      agent.seatId = newSeat.id
      agent.setPosition(newSeat.tileX * TILE_SIZE + TILE_SIZE / 2, newSeat.tileY * TILE_SIZE + TILE_SIZE / 2)
      agent.setAgentState(AgentState.TYPE)
      agent.setDirection(newSeat.facing)
      agent.seatTimer = 10
    } else {
      agent.seatId = null
      agent.setPosition(fallbackX * TILE_SIZE + TILE_SIZE / 2, fallbackY * TILE_SIZE + TILE_SIZE / 2)
    }
  }

  getCharacterCards() {
    const cards: Array<{
      id: string; displayName: string; state: string; currentTool: string | null
      isSubagent: boolean; parentAgentId: string | null; taskSummary?: string
      lastEventAt: number; officeId: string; seatId: string | null
    }> = []
    for (const agent of this.agents.values()) {
      cards.push({
        id: agent.agentId, displayName: agent.displayName, state: agent.agentState,
        currentTool: agent.currentTool, isSubagent: agent.isSubagent,
        parentAgentId: agent.parentAgentId, taskSummary: agent.taskSummary,
        lastEventAt: agent.lastEventAt, officeId: agent.officeId, seatId: agent.seatId,
      })
    }
    return cards
  }

  changeAgentSeat(agentId: string, newSeatId: string) {
    const agent = this.agents.get(agentId)
    if (!agent) return
    if (agent.seatId) {
      const oldSeat = this.seats.find(s => s.id === agent.seatId)
      if (oldSeat) { oldSeat.assigned = false; oldSeat.assignedTo = null }
    }
    const newSeat = this.seats.find(s => s.id === newSeatId)
    if (!newSeat || (newSeat.assigned && newSeat.assignedTo !== agentId)) return
    newSeat.assigned = true
    newSeat.assignedTo = agentId
    agent.seatId = newSeatId
    agent.stopMovement()
    agent.setPosition(newSeat.tileX * TILE_SIZE + TILE_SIZE / 2, newSeat.tileY * TILE_SIZE + TILE_SIZE / 2)
    agent.setAgentState(AgentState.TYPE)
    agent.setDirection(newSeat.facing)
    agent.seatTimer = 10
  }

  rebuildOfficeCollision(officeId: string, mapStr: string[], seatCoords: [number, number][]) {
    updateOfficeMap(officeId, mapStr, seatCoords)

    if (this.mapData.wallBodies) {
      this.mapData.wallBodies.clear(true, true)
    }

    const offices = getOffices()
    const grid = buildCompositeGrid(offices)
    const wallBodies = OfficeMapBuilder.buildWallBodies(this, grid)
    this.mapData = { ...this.mapData, wallBodies, collisionGrid: grid }

    this.pathfinder = new PathfindingManager(grid)
    this.walkableTiles = this.pathfinder.getWalkableTiles()
    this.buildWalkableTilesByOffice()

    reloadZones(offices)
    this.seats = getAllSeats().map(s => ({ ...s }))

    for (const agent of this.agents.values()) {
      agent.setPathfinder(this.pathfinder)
      agent.stopMovement()
    }

    console.log('[OfficeScene] Office collision rebuilt —', officeId, 'walkable:', this.walkableTiles.length)
  }

  renameOffice(officeId: string, newName: string) {
    storeRenameOffice(officeId, newName)
    this.mapBuilder.updateLabel(officeId, newName)
  }

  /** Matches `setBounds` in create(); used for zoom floor so the viewport never extends past the world. */
  private checkOutdoorDayNight() {
    this.applyOutdoorLighting(isLocalDaytime())
  }

  /** Call after changing URL or `opc_outdoor_override` in localStorage (via GameBridge). */
  syncOutdoorLighting() {
    this.applyOutdoorLighting(isLocalDaytime())
  }

  private applyOutdoorLighting(next: boolean) {
    if (next === this.outdoorIsDay) return
    this.outdoorIsDay = next
    this.cameras.main.setBackgroundColor(next ? SCENE_CLEAR_DAY : SCENE_CLEAR_NIGHT)
    this.mapBuilder.refreshOutdoorDayNight(this, next)
  }

  private getWorldSize() {
    const mapW = MAP_COLS * TILE_SIZE
    const mapH = MAP_ROWS * TILE_SIZE
    return {
      worldW: mapW + OUTDOOR_MARGIN_X * 2,
      worldH: mapH + OUTDOOR_MARGIN_TOP + OUTDOOR_MARGIN_BOTTOM,
    }
  }

  /** Minimum zoom so `displayWidth/Height` never exceeds world bounds (avoids empty margin past the map). */
  private getMinCameraZoom(cam: Phaser.Cameras.Scene2D.Camera) {
    const { worldW, worldH } = this.getWorldSize()
    return Math.max(cam.width / worldW, cam.height / worldH)
  }
}
