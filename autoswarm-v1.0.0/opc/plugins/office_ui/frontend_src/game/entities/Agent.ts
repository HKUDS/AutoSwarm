import Phaser from 'phaser'
import {
  TILE_SIZE,
  CHAR_SCALE_X, CHAR_SCALE_Y,
  CHAR_SHADOW_ALPHA, CHAR_SHADOW_HEIGHT, CHAR_SHADOW_WIDTH, CHAR_SHADOW_Y,
  WALK_SPEED_URGENT, WALK_SPEED_NORMAL, WALK_SPEED_RELAXED,
  CELEBRATE_DURATION, STATUS_BUBBLE_DURATION,
  WANDER_PAUSE_MIN, WANDER_PAUSE_MAX,
  WANDER_MOVES_BEFORE_REST_MIN, WANDER_MOVES_BEFORE_REST_MAX,
} from '../config'
import { AgentState, Direction } from '../types'
import type { PathfindingManager } from '../systems/PathfindingManager'

function randomRange(min: number, max: number) {
  return min + Math.random() * (max - min)
}
function randomInt(min: number, max: number) {
  return Math.floor(randomRange(min, max + 1))
}

export class Agent extends Phaser.GameObjects.Container {
  declare body: Phaser.Physics.Arcade.Body

  agentId: string
  displayName: string
  officeId = 'office-0'
  agentState: AgentState = AgentState.IDLE
  dir: Direction = Direction.DOWN
  palette: number
  isActive = false
  currentTool: string | null = null
  seatId: string | null = null
  urgency: 'urgent' | 'normal' | 'relaxed' = 'relaxed'
  isSubagent = false
  parentAgentId: string | null = null
  taskSummary?: string
  lastEventAt = 0
  stateTimer = 0
  seatTimer = 0
  wanderTimer: number
  wanderCount = 0
  wanderLimit: number
  hueShift = 0

  bubbleText: string | null = null
  bubbleTimer = 0

  myceliumEffect: string | null = null
  myceliumEffectTimer = 0
  myceliumSession: string | null = null

  private sprite: Phaser.GameObjects.Sprite
  private shadow: Phaser.GameObjects.Ellipse
  private bubbleObj: Phaser.GameObjects.Container | null = null
  private currentPath: { x: number; y: number }[] = []
  private pathIndex = 0
  private pathfinder: PathfindingManager | null = null
  private arrivalCallback: (() => void) | null = null

  constructor(
    scene: Phaser.Scene,
    agentId: string,
    displayName: string,
    palette: number,
    tileX: number,
    tileY: number,
  ) {
    const px = tileX * TILE_SIZE + TILE_SIZE / 2
    const py = tileY * TILE_SIZE + TILE_SIZE / 2

    super(scene, px, py)

    this.agentId = agentId
    this.displayName = displayName
    this.palette = palette % 6
    this.wanderTimer = randomRange(0.5, 2.5)
    this.wanderLimit = randomInt(WANDER_MOVES_BEFORE_REST_MIN, WANDER_MOVES_BEFORE_REST_MAX)

    const spriteKey = `char_${this.palette}`
    this.shadow = scene.add.ellipse(
      0,
      CHAR_SHADOW_Y,
      CHAR_SHADOW_WIDTH,
      CHAR_SHADOW_HEIGHT,
      0x151820,
      CHAR_SHADOW_ALPHA,
    )
    this.shadow.setOrigin(0.5, 0.5)
    this.add(this.shadow)

    this.sprite = scene.add.sprite(0, 0, spriteKey)
    this.sprite.setScale(CHAR_SCALE_X, CHAR_SCALE_Y)
    this.sprite.setOrigin(0.5, 1)
    this.add(this.sprite)

    scene.add.existing(this)
    scene.physics.world.enable(this)

    const bodyW = Math.min(12, TILE_SIZE - 4)
    const bodyH = Math.min(6, TILE_SIZE / 2)
    this.body.setSize(bodyW, bodyH)
    this.body.setOffset(-bodyW / 2, -bodyH)
    this.body.setCollideWorldBounds(true)

    this.setDepth(py)
    this.playAnimForState()
  }

  setPathfinder(pf: PathfindingManager) {
    this.pathfinder = pf
  }

  getState(): AgentState { return this.agentState }

  getTilePos(): { x: number; y: number } {
    return {
      x: Math.floor(this.x / TILE_SIZE),
      y: Math.floor(this.y / TILE_SIZE),
    }
  }

  // ── State management ──────────────────────────────────────

  setAgentState(newState: AgentState) {
    if (this.agentState === newState) return
    this.agentState = newState
    this.playAnimForState()
  }

  setDirection(dir: Direction) {
    if (this.dir === dir) return
    this.dir = dir
    this.playAnimForState()
  }

  private playAnimForState() {
    if (!this.sprite?.anims) return

    const key = `char_${this.palette}`
    const dir = this.dir

    const isLeft = dir === Direction.LEFT
    this.sprite.setFlipX(isLeft)
    const animDir = isLeft ? 'right' : dir

    switch (this.agentState) {
      case AgentState.WALK:
        this.sprite.play(`${key}_walk_${animDir}`, true)
        break
      case AgentState.CELEBRATE:
        this.sprite.play(`${key}_celebrate_${animDir}`, true)
        break
      case AgentState.TYPE:
      case AgentState.PRESENT:
      case AgentState.PRACTICE:
        this.sprite.play(`${key}_type_${animDir}`, true)
        break
      case AgentState.THINK:
      case AgentState.REFLECT:
        this.sprite.play(`${key}_read_${animDir}`, true)
        break
      case AgentState.COFFEE:
      case AgentState.CHAT:
        this.sprite.play(`${key}_coffee_${animDir}`, true)
        break
      case AgentState.SLEEP:
      case AgentState.IDLE:
      default:
        this.sprite.play(`${key}_idle_${animDir}`, true)
        break
    }
  }

  // ── Movement ──────────────────────────────────────────────

  async walkTo(tileX: number, tileY: number, onArrival?: () => void): Promise<boolean> {
    if (!this.pathfinder || !this.sprite?.anims) return false

    const from = this.getTilePos()
    const path = await this.pathfinder.findPath(from, { x: tileX, y: tileY })

    if (path.length === 0) return false

    this.currentPath = path
    this.pathIndex = 0
    this.arrivalCallback = onArrival ?? null
    this.setAgentState(AgentState.WALK)

    return true
  }

  stopMovement() {
    this.currentPath = []
    this.pathIndex = 0
    this.arrivalCallback = null
    this.body?.setVelocity(0, 0)
  }

  get isMoving(): boolean {
    return this.agentState === AgentState.WALK && this.currentPath.length > 0
  }

  private getWalkSpeed(): number {
    switch (this.urgency) {
      case 'urgent': return WALK_SPEED_URGENT
      case 'normal': return WALK_SPEED_NORMAL
      default:       return WALK_SPEED_RELAXED
    }
  }

  // ── Bubble ────────────────────────────────────────────────

  showBubble(text: string, duration = STATUS_BUBBLE_DURATION) {
    this.bubbleText = text
    this.bubbleTimer = duration
    this.updateBubbleDisplay()
  }

  clearBubble() {
    this.bubbleText = null
    this.bubbleTimer = 0
    if (this.bubbleObj) {
      this.bubbleObj.destroy()
      this.bubbleObj = null
    }
  }

  private updateBubbleDisplay() {
    if (this.bubbleObj) {
      this.bubbleObj.destroy()
      this.bubbleObj = null
    }
    if (!this.bubbleText) return

    const container = this.scene.add.container(this.x, this.y - 70)

    const textObj = this.scene.add.text(0, 0, this.bubbleText, {
      fontSize: '10px',
      fontFamily: 'monospace',
      color: '#1a1a2e',
      backgroundColor: '#ffffff',
      padding: { x: 4, y: 2 },
      resolution: 2,
    })
    textObj.setOrigin(0.5, 1)

    const bg = this.scene.add.graphics()
    const w = textObj.width + 8
    const h = textObj.height + 4
    bg.fillStyle(0xffffff, 0.95)
    bg.fillRoundedRect(-w / 2, -h, w, h, 4)
    bg.lineStyle(1, 0x666666, 0.5)
    bg.strokeRoundedRect(-w / 2, -h, w, h, 4)

    container.add([bg, textObj])
    container.setDepth(100000)
    this.bubbleObj = container
  }

  // ── Per-frame update ──────────────────────────────────────

  update(dt: number) {
    // Depth sorting
    this.setDepth(this.y)

    // Bubble position tracking + timer
    if (this.bubbleObj) {
      this.bubbleObj.setPosition(this.x, this.y - 70)
      if (this.bubbleTimer > 0) {
        this.bubbleTimer -= dt
        if (this.bubbleTimer <= 0) {
          this.clearBubble()
        }
      }
    }

    // State timer
    if (this.stateTimer > 0) {
      this.stateTimer -= dt
      if (this.stateTimer <= 0) {
        this.stateTimer = 0
        if (this.agentState === AgentState.CELEBRATE) {
          this.setAgentState(AgentState.IDLE)
        }
        if (this.agentState === AgentState.COFFEE) {
          this.setAgentState(AgentState.IDLE)
          this.wanderTimer = randomRange(WANDER_PAUSE_MIN, WANDER_PAUSE_MAX)
        }
        if (this.agentState === AgentState.CHAT) {
          this.setAgentState(AgentState.IDLE)
        }
      }
    }

    // Path following
    if (this.agentState === AgentState.WALK && this.currentPath.length > 0) {
      this.followPath(dt)
    }
  }

  private followPath(dt: number) {
    this.body.setVelocity(0, 0)

    if (this.pathIndex >= this.currentPath.length) {
      this.arriveAtDestination()
      return
    }

    const target = this.currentPath[this.pathIndex]
    const targetPx = target.x * TILE_SIZE + TILE_SIZE / 2
    const targetPy = target.y * TILE_SIZE + TILE_SIZE / 2

    const dx = targetPx - this.x
    const dy = targetPy - this.y
    const dist = Math.sqrt(dx * dx + dy * dy)

    const speed = this.getWalkSpeed()
    const step = speed * dt

    if (dist <= step + 0.5) {
      this.x = targetPx
      this.y = targetPy
      this.pathIndex++

      if (this.pathIndex >= this.currentPath.length) {
        this.arriveAtDestination()
      }
      return
    }

    this.x += (dx / dist) * step
    this.y += (dy / dist) * step

    if (Math.abs(dx) > Math.abs(dy)) {
      this.setDirection(dx > 0 ? Direction.RIGHT : Direction.LEFT)
    } else {
      this.setDirection(dy > 0 ? Direction.DOWN : Direction.UP)
    }
  }

  private arriveAtDestination() {
    this.currentPath = []
    this.pathIndex = 0
    this.body.setVelocity(0, 0)

    const cb = this.arrivalCallback
    this.arrivalCallback = null

    if (cb) {
      cb()
    } else if (this.agentState === AgentState.WALK) {
      this.setAgentState(AgentState.IDLE)
    }
  }

  destroy(fromScene?: boolean) {
    this.clearBubble()
    super.destroy(fromScene)
  }
}
