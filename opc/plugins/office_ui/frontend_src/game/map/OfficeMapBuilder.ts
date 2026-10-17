import Phaser from 'phaser'
import { PRODUCT_NAME } from '../../lib/branding'
import {
  TILE_SIZE, OFFICE_COLS, OFFICE_ROWS, GAP_COLS, OUTDOOR_MARGIN_X, OUTDOOR_MARGIN_TOP, OUTDOOR_MARGIN_BOTTOM,
  isLocalDaytime,
} from '../config'
import { getOffices, buildCompositeGrid, DEFAULT_MAP_STR, DEFAULT_SEATS, type OfficeConfig } from './OfficeStore'

export { DEFAULT_MAP_STR, DEFAULT_SEATS }

export interface MapData {
  wallBodies: Phaser.Physics.Arcade.StaticGroup
  collisionGrid: number[][]
  waterfront: {
    dockCenterX: number
    dockY: number
    waterTopY: number
    waterBottomY: number
  }
}

export function parseMapStr(mapStr: string[], cols = OFFICE_COLS, rows = OFFICE_ROWS): number[][] {
  const grid: number[][] = []
  for (let r = 0; r < rows; r++) {
    const row: number[] = []
    const line = r < mapStr.length ? mapStr[r] : '#'.repeat(cols)
    for (let c = 0; c < cols; c++) {
      row.push(c < line.length && line[c] === '.' ? 0 : 1)
    }
    grid.push(row)
  }
  return grid
}

export function gridToMapStr(grid: number[][]): string[] {
  return grid.map(row => row.map(v => (v === 0 ? '.' : '#')).join(''))
}

interface ForegroundCrop {
  x: number
  y: number
  w: number
  h: number
  depthY: number
}

function cropTiles(x: number, y: number, w: number, h: number, depthY = y + h): ForegroundCrop {
  return {
    x: Math.round(x * TILE_SIZE),
    y: Math.round(y * TILE_SIZE),
    w: Math.round(w * TILE_SIZE),
    h: Math.round(h * TILE_SIZE),
    depthY: Math.round(depthY * TILE_SIZE),
  }
}

const OFFICE_FOREGROUND_CROPS: ForegroundCrop[] = [
  cropTiles(8.0, 4.0, 4.3, 3.35, 7.15),
  cropTiles(2.3, 8.65, 5.35, 1.95, 10.55),
  cropTiles(11.1, 8.55, 3.1, 1.7, 10.45),
  cropTiles(14.15, 8.55, 4.95, 2.0, 10.95),
  cropTiles(2.1, 11.45, 3.05, 1.8, 12.95),
  cropTiles(5.25, 11.45, 3.05, 1.8, 12.95),
  cropTiles(8.45, 11.45, 3.05, 1.8, 12.95),
  cropTiles(2.1, 14.55, 3.05, 1.85, 16.1),
  cropTiles(5.25, 14.55, 3.05, 1.85, 16.1),
  cropTiles(8.45, 14.55, 3.05, 1.85, 16.1),
  cropTiles(14.0, 12.15, 4.85, 3.0, 15.3),
  cropTiles(1.0, 19.55, 3.05, 1.15, 20.7),
  cropTiles(4.45, 20.45, 3.35, 1.35, 21.8),
  cropTiles(14.1, 20.5, 3.15, 1.85, 22.3),
  cropTiles(8.05, 23.1, 4.2, 1.0, 24.1),
  cropTiles(17.0, 23.1, 2.1, 1.0, 24.1),
]

export class OfficeMapBuilder {
  private bgSprites: Phaser.GameObjects.Image[] = []
  private foregroundSprites: Phaser.GameObjects.Image[] = []
  private nameLabels: Phaser.GameObjects.Text[] = []
  private decor: Phaser.GameObjects.GameObject[] = []
  /** Outdoor campus graphics (skyline through waterfront); replaced on day/night switch. */
  private outdoorBackdrop: Phaser.GameObjects.Graphics | null = null
  /** Parking slabs on their own layer so asphalt always paints over full-width lawn. */
  private outdoorParking: Phaser.GameObjects.Graphics | null = null
  private readonly tilesetFrames = {
    shelfA: 61,
    shelfB: 62,
    waterCooler: 105,
    waterCoolerAlt: 106,
    fridge: 108,
    vending: 110,
    plantA: 148,
    plantB: 150,
    boxA: 153,
    boxB: 154,
    boxC: 158,
  }

  private clearSceneDecor() {
    for (const sprite of this.bgSprites) sprite.destroy()
    for (const sprite of this.foregroundSprites) sprite.destroy()
    for (const label of this.nameLabels) label.destroy()
    for (const item of this.decor) item.destroy()
    this.bgSprites = []
    this.foregroundSprites = []
    this.nameLabels = []
    this.decor = []
    this.outdoorBackdrop = null
    this.outdoorParking = null
  }

  private addForegroundOccluders(scene: Phaser.Scene, office: OfficeConfig) {
    const officeX = office.offsetCol * TILE_SIZE
    for (const crop of OFFICE_FOREGROUND_CROPS) {
      const sprite = scene.add.image(officeX, 0, 'office-bg')
      sprite.setOrigin(0, 0)
      sprite.setCrop(crop.x, crop.y, crop.w, crop.h)
      sprite.setDepth(crop.depthY)
      this.foregroundSprites.push(sprite)
    }
  }

  private addPixelLabel(
    scene: Phaser.Scene,
    x: number,
    y: number,
    text: string,
    bgColor: string,
    color = '#f5efe3',
    size = '10px',
  ) {
    const label = scene.add.text(x, y, text, {
      fontSize: size,
      fontFamily: 'monospace',
      color,
      backgroundColor: bgColor,
      padding: { left: 4, right: 4, top: 2, bottom: 2 },
      resolution: 2,
      align: 'center',
    })
    label.setOrigin(0.5, 0.5)
    label.setDepth(-94)
    this.decor.push(label)
    return label
  }

  private addTilesetSprite(
    scene: Phaser.Scene,
    x: number,
    y: number,
    frame: number,
    scale = 1,
  ) {
    const sprite = scene.add.image(x, y, 'office-tileset-32', frame)
    sprite.setOrigin(0, 0)
    sprite.setScale(scale)
    sprite.setDepth(-92)
    this.decor.push(sprite)
    return sprite
  }

  private getOutdoorMetrics(totalWidth: number, totalHeight: number) {
    const roadY = totalHeight + TILE_SIZE * 10.1
    const roadHeight = TILE_SIZE * 4.1
    const waterTopY = roadY + roadHeight + TILE_SIZE * 0.15
    const dockY = waterTopY + TILE_SIZE * 1.1
    return {
      gateCenterX: totalWidth / 2,
      roadY,
      roadHeight,
      waterTopY,
      dockY,
      bottomY: totalHeight + OUTDOOR_MARGIN_BOTTOM,
    }
  }

  /**
   * Original top-down pixel cars: bold outline, stepped shading, glass highlights.
   * Tires are drawn to the sides of the body (mostly outside the paint); solid rubber, no hub in plan view.
   */
  private paintStylizedParkingCar(
    g: Phaser.GameObjects.Graphics,
    cx: number,
    cy: number,
    mirrorX: boolean,
    warmBody: boolean,
    drawRunningLights: boolean,
  ) {
    const P = warmBody
      ? {
          outline: 0x141018,
          body: 0xd94a52,
          bodyMid: 0xc73c44,
          bodyDark: 0x8e2a32,
          cabin: 0xe0686e,
          cabinHi: 0xf2888c,
          cabinLo: 0xb0303c,
          glass: 0x5c80b8,
          glassDeep: 0x3a5c88,
          glassHi: 0xb8e0fc,
          tire: 0x1c1e24,
          tireOuter: 0x12141a,
          bonnetHi: 0xe87074,
        }
      : {
          outline: 0x101420,
          body: 0x3e7cc8,
          bodyMid: 0x2f64a8,
          bodyDark: 0x244878,
          cabin: 0x5c98dc,
          cabinHi: 0x7eb4f0,
          cabinLo: 0x3468a8,
          glass: 0x4a6fa0,
          glassDeep: 0x304868,
          glassHi: 0xa8d8ff,
          tire: 0x1c1e24,
          tireOuter: 0x12141a,
          bonnetHi: 0x6ca8e8,
        }

    const blot = (x0: number, y: number, w: number, h: number, col: number, a = 1) => {
      g.fillStyle(col, a)
      const sx = mirrorX ? -x0 - w : x0
      g.fillRect(Math.round(cx + sx), Math.round(cy + y), w, h)
    }

    g.fillStyle(0x0f140f, 0.28)
    g.fillEllipse(cx, cy + 36, 36, 9)

    blot(-16, -38, 32, 76, P.outline)
    blot(-15, -37, 30, 74, P.body)
    blot(-14, 2, 28, 35, P.bodyDark)
    blot(-14, -4, 28, 10, P.bodyMid)
    blot(-13, 8, 26, 22, P.body)
    blot(-13, 8, 26, 7, P.bonnetHi, 0.92)

    blot(-13, -35, 26, 32, P.cabin)
    blot(-13, -35, 26, 4, P.cabinHi)
    blot(-13, -8, 26, 5, P.cabinLo)

    blot(-11, -7, 22, 11, P.glassDeep)
    blot(-11, -7, 22, 5, P.glass)
    blot(-10, -6, 8, 3, P.glassHi)
    blot(-1, -6, 6, 2, P.glassHi, 0.75)

    blot(-16, -2, 3, 8, P.bodyDark)
    blot(13, -2, 3, 8, P.bodyDark)

    const wheelYFront = 22
    const wheelYRear = -30
    const tireW = 7
    const tireH = 9
    for (const wy of [wheelYFront, wheelYRear]) {
      const leftX = -22
      const rightX = 15
      blot(leftX, wy, tireW, tireH, P.tire)
      blot(rightX, wy, tireW, tireH, P.tire)
      blot(leftX, wy, 1, tireH, P.tireOuter)
      blot(rightX + tireW - 1, wy, 1, tireH, P.tireOuter)
    }
    for (const wy of [wheelYFront, wheelYRear]) {
      blot(-18, wy + 1, 4, tireH - 2, P.bodyDark)
      blot(14, wy + 1, 4, tireH - 2, P.bodyDark)
    }

    if (drawRunningLights) {
      blot(-12, 33, 5, 4, 0xf4f2ea)
      blot(7, 33, 5, 4, 0xf4f2ea)
      blot(-11, 34, 2, 2, 0xfff8c8)
      blot(8, 34, 2, 2, 0xfff8c8)
      blot(-13, -38, 4, 3, 0xff5530)
      blot(9, -38, 4, 3, 0xc83820)
      blot(-18, 4, 3, 4, 0xffcc48)
      blot(15, 4, 3, 4, 0xffa030)
    }

    blot(-15, -37, 30, 1, P.cabinHi, 0.45)
    blot(-14, 28, 28, 1, 0x000000, 0.12)
  }

  private buildOutdoorBackdrop(
    scene: Phaser.Scene,
    totalWidth: number,
    totalHeight: number,
    offices: OfficeConfig[],
    isDay: boolean,
    opts?: { skipLabels?: boolean },
  ) {
    const backdrop = scene.add.graphics()
    backdrop.setDepth(-320)

    const startX = -OUTDOOR_MARGIN_X
    const startY = -OUTDOOR_MARGIN_TOP
    const width = totalWidth + OUTDOOR_MARGIN_X * 2
    const height = totalHeight + OUTDOOR_MARGIN_TOP + OUTDOOR_MARGIN_BOTTOM
    const endX = startX + width
    const bottomY = totalHeight + OUTDOOR_MARGIN_BOTTOM
    const horizonY = startY + TILE_SIZE * 6.2
    const lawnY = totalHeight + TILE_SIZE * 0.35
    const fenceY = totalHeight + TILE_SIZE * 4.25
    const parkingY = totalHeight + TILE_SIZE * 5.9
    const { gateCenterX, roadY, roadHeight, waterTopY, dockY } = this.getOutdoorMetrics(totalWidth, totalHeight)

    const pal = isDay
      ? {
          skyTop: 0x6ec4f0,
          skyBand: 0xa8daf8,
          towerFar: 0x889fb4,
          towerNear: 0x9cb0c4,
          towerRoof: 0xb4c8dc,
          winColor: 0xffffff,
          winAlpha: 0.22,
          facadeColor: 0xd8e4f0,
          facadeAlpha: 0.36,
          bridgeColor: 0xc0d0e0,
          bridgeAlpha: 0.92,
          grassBase: 0x3a7844,
          grassLawn: 0x5cb868,
          treeTrunk: 0x365c40,
          treeMid: 0x4ca868,
          treeHi: 0x72d884,
          grove0: 0x305838,
          grove1: 0x4e9860,
          grove2: 0x68c078,
          fence: 0x9a8a78,
          road: 0x3c4048,
          roadDash: 0xd8c890,
          curb: 0x363230,
          water: 0x3588b8,
          ripple: 0x5cacd4,
          rippleHi: 0x98d8f0,
          edgeGreen: 0x58a868,
          edgeGreenHi: 0x7ed090,
          logoDirt: 0xb8b898,
          logoDirtHi: 0xd0d0b8,
        }
      : {
          skyTop: 0x263345,
          skyBand: 0x35516a,
          towerFar: 0x132032,
          towerNear: 0x1d2b3e,
          towerRoof: 0x24364b,
          winColor: 0xf3d58c,
          winAlpha: 0.42,
          facadeColor: 0x30455c,
          facadeAlpha: 0.45,
          bridgeColor: 0x233247,
          bridgeAlpha: 0.95,
          grassBase: 0x31523b,
          grassLawn: 0x4f814f,
          treeTrunk: 0x2b442d,
          treeMid: 0x3f6b3f,
          treeHi: 0x548654,
          grove0: 0x284028,
          grove1: 0x406f40,
          grove2: 0x5d8c58,
          fence: 0x7c6a55,
          road: 0x30343a,
          roadDash: 0xd0b17a,
          curb: 0x2e2a27,
          water: 0x214a62,
          ripple: 0x3c88a3,
          rippleHi: 0x78bfd0,
          edgeGreen: 0x6f8b5f,
          edgeGreenHi: 0x8fb47a,
          logoDirt: 0x9ca277,
          logoDirtHi: 0xbbb38d,
        }

    backdrop.fillStyle(pal.skyTop, 1)
    backdrop.fillRect(startX, startY, width, TILE_SIZE * 8.2)
    backdrop.fillStyle(pal.skyBand, 1)
    backdrop.fillRect(startX, startY + TILE_SIZE * 6.15, width, TILE_SIZE * 2.05)

    if (isDay) {
      backdrop.fillStyle(0xfff8e8, 0.45)
      for (let i = 0; i < 5; i++) {
        const cx = startX + width * (0.12 + i * 0.19) + ((i * 17) % 40)
        const cy = startY + TILE_SIZE * (1.4 + (i % 3) * 0.35)
        backdrop.fillEllipse(cx, cy, TILE_SIZE * 2.2, TILE_SIZE * 0.95)
      }
      backdrop.fillStyle(0xfff2b0, 0.92)
      backdrop.fillCircle(endX - TILE_SIZE * 2.4, startY + TILE_SIZE * 1.25, TILE_SIZE * 0.85)
      backdrop.fillStyle(0xfffcf0, 0.35)
      backdrop.fillCircle(endX - TILE_SIZE * 2.4, startY + TILE_SIZE * 1.25, TILE_SIZE * 1.45)
    }

    // Distant city skyline with layered towers, roof forms, and lit windows.
    backdrop.fillStyle(pal.towerFar, 1)
    for (let x = startX - TILE_SIZE; x < endX + TILE_SIZE; x += TILE_SIZE * 3.4) {
      const stepIndex = Math.floor((x - startX + TILE_SIZE) / (TILE_SIZE * 3.4))
      const widthSteps = [2.3, 1.7, 2.9, 2.1, 3.2, 1.9]
      const heightSteps = [6.8, 8.7, 7.2, 10.4, 8.1, 9.6]
      const towerWidth = TILE_SIZE * widthSteps[stepIndex % widthSteps.length]
      const towerHeight = TILE_SIZE * heightSteps[stepIndex % heightSteps.length]
      const towerTop = horizonY - towerHeight - TILE_SIZE * 0.9
      backdrop.fillRect(x, towerTop, towerWidth, towerHeight)

      if (stepIndex % 3 === 0) {
        backdrop.fillRect(x + towerWidth * 0.22, towerTop - TILE_SIZE * 0.7, towerWidth * 0.56, TILE_SIZE * 0.7)
      } else if (stepIndex % 3 === 1) {
        backdrop.fillRect(x + towerWidth * 0.4, towerTop - TILE_SIZE * 1.0, 4, TILE_SIZE)
      } else {
        backdrop.fillRect(x + towerWidth * 0.18, towerTop - TILE_SIZE * 0.45, towerWidth * 0.64, TILE_SIZE * 0.45)
      }
    }

    backdrop.fillStyle(pal.towerNear, 1)
    for (let x = startX; x < endX; x += TILE_SIZE * 2.65) {
      const stepIndex = Math.floor((x - startX) / (TILE_SIZE * 2.65))
      const widthSteps = [1.8, 2.4, 1.55, 2.8, 2.1, 1.7, 2.5]
      const heightSteps = [5.4, 7.1, 6.0, 8.6, 6.7, 7.8, 5.8]
      const towerWidth = TILE_SIZE * widthSteps[stepIndex % widthSteps.length]
      const towerHeight = TILE_SIZE * heightSteps[stepIndex % heightSteps.length]
      const towerTop = horizonY - towerHeight
      backdrop.fillRect(x, towerTop, towerWidth, towerHeight)

      // Rooftop shapes to avoid flat silhouettes.
      if (stepIndex % 4 === 0) {
        backdrop.fillStyle(pal.towerRoof, 1)
        backdrop.fillRect(x + towerWidth * 0.24, towerTop - TILE_SIZE * 0.42, towerWidth * 0.52, TILE_SIZE * 0.42)
        backdrop.fillStyle(pal.towerNear, 1)
      } else if (stepIndex % 4 === 1) {
        backdrop.fillStyle(pal.towerRoof, 1)
        backdrop.fillRect(x + towerWidth * 0.48, towerTop - TILE_SIZE * 0.8, 4, TILE_SIZE * 0.8)
        backdrop.fillStyle(pal.towerNear, 1)
      } else if (stepIndex % 4 === 2) {
        backdrop.fillStyle(pal.towerRoof, 1)
        backdrop.fillRect(x + towerWidth * 0.18, towerTop - TILE_SIZE * 0.35, towerWidth * 0.64, TILE_SIZE * 0.35)
        backdrop.fillStyle(pal.towerNear, 1)
      }

      // Night: warm lit windows. Day: faint glass reflections (no "hub" glow).
      backdrop.fillStyle(pal.winColor, pal.winAlpha)
      for (let wy = towerTop + TILE_SIZE * 0.7; wy < towerTop + towerHeight - TILE_SIZE * 0.5; wy += TILE_SIZE * 1.05) {
        if (isDay && (stepIndex + Math.floor(wy)) % 2 !== 0) continue
        if ((Math.floor(wy) + stepIndex) % 3 === 0) {
          backdrop.fillRect(x + 5, wy, 3, 3)
        }
        if ((Math.floor(wy) + stepIndex) % 2 === 0 && towerWidth > TILE_SIZE * 1.9) {
          backdrop.fillRect(x + towerWidth - 8, wy + 4, 3, 3)
        }
      }
      backdrop.fillStyle(pal.facadeColor, pal.facadeAlpha)
      backdrop.fillRect(x + towerWidth * 0.5, towerTop + TILE_SIZE * 0.4, 2, towerHeight - TILE_SIZE * 0.8)
      backdrop.fillStyle(pal.towerNear, 1)
    }

    // A few skybridges / podiums between towers to make the silhouette richer.
    backdrop.fillStyle(pal.bridgeColor, pal.bridgeAlpha)
    for (let x = startX + TILE_SIZE * 6; x < endX - TILE_SIZE * 6; x += TILE_SIZE * 11) {
      const bridgeY = horizonY - TILE_SIZE * (2.8 + ((x / TILE_SIZE) % 2))
      backdrop.fillRect(x, bridgeY, TILE_SIZE * 2.2, TILE_SIZE * 0.35)
    }

    // Campus grass field around the offices.
    backdrop.fillStyle(pal.grassBase, 1)
    backdrop.fillRect(startX, horizonY, width, height - (horizonY - startY))
    backdrop.fillStyle(pal.grassLawn, 1)
    backdrop.fillRect(startX, lawnY, width, roadY - lawnY)

    // Tree belts on both sides and behind the complex.
    const treeXs: number[] = []
    for (let x = startX + TILE_SIZE * 0.1; x < endX; x += TILE_SIZE * 3.9) treeXs.push(x)
    for (const x of treeXs) {
      const canopyW = TILE_SIZE * (2.25 + (Math.abs(Math.floor(x / TILE_SIZE)) % 3) * 0.32)
      backdrop.fillStyle(pal.treeTrunk, 1)
      backdrop.fillRect(x + TILE_SIZE * 1.05, horizonY + TILE_SIZE * 1.2, 8, TILE_SIZE * 1.65)
      backdrop.fillStyle(pal.treeMid, 1)
      backdrop.fillRect(x, horizonY - TILE_SIZE * 1.0, canopyW, TILE_SIZE * 2.15)
      backdrop.fillStyle(pal.treeHi, 1)
      backdrop.fillRect(x + 5, horizonY - TILE_SIZE * 1.3, canopyW - 10, TILE_SIZE * 0.95)
    }

    // Side groves so the left/right margins feel intentional.
    for (const groveX of [startX + TILE_SIZE * 1.2, endX - TILE_SIZE * 6.5]) {
      for (let i = 0; i < 4; i++) {
        const x = groveX + i * TILE_SIZE * 1.8
        const y = totalHeight - TILE_SIZE * (4.5 + (i % 2) * 0.9)
        backdrop.fillStyle(pal.grove0, 1)
        backdrop.fillRect(x + 10, y + TILE_SIZE * 1.5, 8, TILE_SIZE * 1.45)
        backdrop.fillStyle(pal.grove1, 1)
        backdrop.fillRect(x, y, TILE_SIZE * 2.35, TILE_SIZE * 1.9)
        backdrop.fillStyle(pal.grove2, 1)
        backdrop.fillRect(x + 4, y - 6, TILE_SIZE * 1.75, TILE_SIZE * 0.82)
      }
    }

    // Keep the forecourt green on both sides; only the central axis should read as a paved approach.

    // Center arrival walk from the lobby to the street.
    const officeDoorCenters = offices.map(office => (office.offsetCol + 2.95) * TILE_SIZE)
    const hallSpanX = TILE_SIZE * 0.9
    const hallSpanW = totalWidth - TILE_SIZE * 1.8
    const wallThickness = 6
    const doorOpeningW = TILE_SIZE * 1.7
    const hallY = totalHeight + TILE_SIZE * 0.08
    const hallH = TILE_SIZE * 2.05
    const hallBottom = hallY + hallH
    const lobbyWidth = TILE_SIZE * 9.4
    const lobbyX = gateCenterX - lobbyWidth / 2
    const lobbyY = hallBottom - 2
    const lobbyH = TILE_SIZE * 3.1
    const lobbyBottom = lobbyY + lobbyH
    const frontOpeningW = TILE_SIZE * 2.3

    backdrop.fillStyle(0xbda57b, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 4.25, lobbyBottom, TILE_SIZE * 8.5, roadY - lobbyBottom)
    backdrop.fillStyle(0xd7c6a1, 0.95)
    for (let y = lobbyBottom + TILE_SIZE * 0.35; y < roadY - 6; y += TILE_SIZE * 0.8) {
      backdrop.fillRect(gateCenterX - TILE_SIZE * 3.35, y, TILE_SIZE * 6.7, 3)
    }
    // Outdoor arrival node and stone lawn edging so the forecourt reads as organized landscape.
    const forecourtNodeY = lobbyBottom + TILE_SIZE * 1.65
    backdrop.fillStyle(0xcab48b, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 3.6, forecourtNodeY, TILE_SIZE * 7.2, TILE_SIZE * 2.15)
    backdrop.fillStyle(0xe1d1ad, 0.95)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 2.8, forecourtNodeY + TILE_SIZE * 0.42, TILE_SIZE * 5.6, TILE_SIZE * 0.18)
    backdrop.fillStyle(0x756a59, 1)
    backdrop.fillRect(startX, roadY - TILE_SIZE * 0.35, width, 3)
    backdrop.fillRect(startX, lawnY + TILE_SIZE * 0.18, width, 3)
    for (const edgeY of [forecourtNodeY + TILE_SIZE * 0.35, forecourtNodeY + TILE_SIZE * 1.45]) {
      backdrop.fillStyle(pal.edgeGreen, 1)
      backdrop.fillRect(gateCenterX - TILE_SIZE * 5.0, edgeY, TILE_SIZE * 1.25, TILE_SIZE * 0.4)
      backdrop.fillRect(gateCenterX + TILE_SIZE * 3.75, edgeY, TILE_SIZE * 1.25, TILE_SIZE * 0.4)
      backdrop.fillStyle(pal.edgeGreenHi, 1)
      backdrop.fillRect(gateCenterX - TILE_SIZE * 4.78, edgeY - 2, TILE_SIZE * 0.82, 3)
      backdrop.fillRect(gateCenterX + TILE_SIZE * 3.96, edgeY - 2, TILE_SIZE * 0.82, 3)
    }

    // Shared indoor hall directly beneath the offices.
    backdrop.fillStyle(0xbfd7d6, 1)
    backdrop.fillRect(hallSpanX, hallY, hallSpanW, hallH)
    backdrop.fillStyle(0xcfe2e1, 1)
    for (let x = hallSpanX + TILE_SIZE * 0.2; x < hallSpanX + hallSpanW - TILE_SIZE * 0.2; x += TILE_SIZE) {
      backdrop.fillRect(x, hallY + TILE_SIZE * 0.42, 2, hallH - TILE_SIZE * 0.62)
    }
    for (let y = hallY + TILE_SIZE * 0.35; y < hallBottom - TILE_SIZE * 0.25; y += TILE_SIZE * 0.72) {
      backdrop.fillRect(hallSpanX + TILE_SIZE * 0.2, y, hallSpanW - TILE_SIZE * 0.4, 2)
    }

    // Top wall with three openings aligned to office exits.
    backdrop.fillStyle(0xf2ede4, 1)
    let cursorX = hallSpanX
    for (const centerX of officeDoorCenters) {
      const openingX = centerX - doorOpeningW / 2
      if (openingX > cursorX) {
        backdrop.fillRect(cursorX, hallY, openingX - cursorX, wallThickness)
      }
      backdrop.fillStyle(0x80684e, 1)
      backdrop.fillRect(openingX - 4, hallY, 4, TILE_SIZE * 0.62)
      backdrop.fillRect(openingX + doorOpeningW, hallY, 4, TILE_SIZE * 0.62)
      backdrop.fillStyle(0xa88961, 1)
      backdrop.fillRect(openingX, hallY, doorOpeningW, 4)
      backdrop.fillStyle(0xf2ede4, 1)
      cursorX = openingX + doorOpeningW
    }
    if (cursorX < hallSpanX + hallSpanW) {
      backdrop.fillRect(cursorX, hallY, hallSpanX + hallSpanW - cursorX, wallThickness)
    }

    // Hall side walls and bottom wall segments.
    backdrop.fillRect(hallSpanX, hallY, wallThickness, hallH)
    backdrop.fillRect(hallSpanX + hallSpanW - wallThickness, hallY, wallThickness, hallH)
    backdrop.fillRect(hallSpanX, hallBottom - wallThickness, lobbyX - hallSpanX, wallThickness)
    backdrop.fillRect(lobbyX + lobbyWidth, hallBottom - wallThickness, hallSpanX + hallSpanW - (lobbyX + lobbyWidth), wallThickness)

    // Door thresholds and shallow wall openings from each office into the hall.
    for (const centerX of officeDoorCenters) {
      const openingX = centerX - doorOpeningW / 2
      backdrop.fillStyle(0x9c835e, 1)
      backdrop.fillRect(openingX, hallY + TILE_SIZE * 0.02, doorOpeningW, TILE_SIZE * 0.16)
      backdrop.fillStyle(0xd9c79d, 1)
      backdrop.fillRect(openingX + TILE_SIZE * 0.12, hallY + TILE_SIZE * 0.24, doorOpeningW - TILE_SIZE * 0.24, 3)
      backdrop.fillStyle(0x6a5642, 0.55)
      backdrop.fillRect(openingX + TILE_SIZE * 0.1, hallY + TILE_SIZE * 0.34, doorOpeningW - TILE_SIZE * 0.2, TILE_SIZE * 0.26)
    }

    // Central lobby as a true top-down indoor room extending from the hall.
    backdrop.fillStyle(0xc6ddd8, 1)
    backdrop.fillRect(lobbyX, lobbyY, lobbyWidth, lobbyH)
    backdrop.fillStyle(0xd7e7e3, 1)
    for (let x = lobbyX + TILE_SIZE * 0.3; x < lobbyX + lobbyWidth - TILE_SIZE * 0.2; x += TILE_SIZE) {
      backdrop.fillRect(x, lobbyY + TILE_SIZE * 0.4, 2, lobbyH - TILE_SIZE * 0.6)
    }
    for (let y = lobbyY + TILE_SIZE * 0.35; y < lobbyBottom - TILE_SIZE * 0.2; y += TILE_SIZE * 0.72) {
      backdrop.fillRect(lobbyX + TILE_SIZE * 0.24, y, lobbyWidth - TILE_SIZE * 0.48, 2)
    }

    backdrop.fillStyle(0xf2ede4, 1)
    backdrop.fillRect(lobbyX, lobbyY, wallThickness, lobbyH)
    backdrop.fillRect(lobbyX + lobbyWidth - wallThickness, lobbyY, wallThickness, lobbyH)
    backdrop.fillRect(lobbyX, lobbyBottom - wallThickness, (lobbyWidth - frontOpeningW) / 2, wallThickness)
    backdrop.fillRect(gateCenterX + frontOpeningW / 2, lobbyBottom - wallThickness, (lobbyWidth - frontOpeningW) / 2, wallThickness)

    // Short returns where the hall opens into the lobby.
    backdrop.fillRect(lobbyX, lobbyY, TILE_SIZE * 1.1, wallThickness)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 1.1, lobbyY, TILE_SIZE * 1.1, wallThickness)
    backdrop.fillStyle(0x9f8566, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.2, lobbyY + TILE_SIZE * 0.18, TILE_SIZE * 0.7, 3)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 0.9, lobbyY + TILE_SIZE * 0.18, TILE_SIZE * 0.7, 3)
    // Side returns and glazed side screens behind the logo wall.
    backdrop.fillStyle(0xe8f0ef, 0.7)
    backdrop.fillRect(lobbyX + TILE_SIZE * 1.15, lobbyY + TILE_SIZE * 0.38, TILE_SIZE * 1.15, TILE_SIZE * 0.9)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.3, lobbyY + TILE_SIZE * 0.38, TILE_SIZE * 1.15, TILE_SIZE * 0.9)
    backdrop.fillStyle(0x7ca0a5, 0.65)
    for (let glassY = lobbyY + TILE_SIZE * 0.44; glassY < lobbyY + TILE_SIZE * 1.16; glassY += 8) {
      backdrop.fillRect(lobbyX + TILE_SIZE * 1.38, glassY, TILE_SIZE * 0.68, 2)
      backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.06, glassY, TILE_SIZE * 0.68, 2)
    }
    backdrop.fillStyle(0x465357, 0.9)
    backdrop.fillRect(lobbyX + TILE_SIZE * 1.1, lobbyY + TILE_SIZE * 0.34, 2, TILE_SIZE * 0.98)
    backdrop.fillRect(lobbyX + TILE_SIZE * 2.26, lobbyY + TILE_SIZE * 0.34, 2, TILE_SIZE * 0.98)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.3, lobbyY + TILE_SIZE * 0.34, 2, TILE_SIZE * 0.98)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 1.14, lobbyY + TILE_SIZE * 0.34, 2, TILE_SIZE * 0.98)

    // Simple lobby furnishings: rug, reception desk, logo wall, and side seating/planting.
    backdrop.fillStyle(0x6f8f93, 0.95)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.65, lobbyY + TILE_SIZE * 0.65, TILE_SIZE * 3.3, TILE_SIZE * 1.1)
    backdrop.fillStyle(0x87a7ab, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.2, lobbyY + TILE_SIZE * 0.92, TILE_SIZE * 2.4, TILE_SIZE * 0.18)

    // Soft logo-wall backlight and wall washers.
    backdrop.fillStyle(0xb8d8d1, 0.38)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 2.45, lobbyY + TILE_SIZE * 0.08, TILE_SIZE * 4.9, TILE_SIZE * 1.25)
    backdrop.fillStyle(0xe8f3ef, 0.7)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.7, lobbyY + TILE_SIZE * 0.18, TILE_SIZE * 3.4, TILE_SIZE * 0.18)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.55, lobbyY + TILE_SIZE * 0.22, TILE_SIZE * 0.9, TILE_SIZE * 0.12)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 1.45, lobbyY + TILE_SIZE * 0.22, TILE_SIZE * 0.9, TILE_SIZE * 0.12)

    backdrop.fillStyle(0x4e6b68, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.95, lobbyY + TILE_SIZE * 0.18, TILE_SIZE * 3.9, TILE_SIZE * 0.42)
    backdrop.fillStyle(0xdfeee8, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.2, lobbyY + TILE_SIZE * 0.3, TILE_SIZE * 2.4, TILE_SIZE * 0.12)

    // Reception desk shadow / light falloff onto the floor.
    backdrop.fillStyle(0x566f76, 0.35)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.65, lobbyY + TILE_SIZE * 2.05, TILE_SIZE * 3.3, TILE_SIZE * 0.32)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.1, lobbyY + TILE_SIZE * 2.38, TILE_SIZE * 2.2, TILE_SIZE * 0.18)

    backdrop.fillStyle(0x8e8f9b, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.28, lobbyY + TILE_SIZE * 1.45, TILE_SIZE * 2.56, TILE_SIZE * 0.72)
    backdrop.fillStyle(0xe7eef0, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.0, lobbyY + TILE_SIZE * 1.6, TILE_SIZE * 2.0, TILE_SIZE * 0.18)
    backdrop.fillStyle(0x5f6472, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 0.22, lobbyY + TILE_SIZE * 1.42, TILE_SIZE * 0.44, TILE_SIZE * 0.18)
    backdrop.fillStyle(0xf6f7f2, 0.78)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 0.9, lobbyY + TILE_SIZE * 1.72, TILE_SIZE * 1.8, TILE_SIZE * 0.12)

    backdrop.fillStyle(0xbfa67e, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.7, lobbyY + TILE_SIZE * 1.55, TILE_SIZE * 1.2, TILE_SIZE * 0.52)
    backdrop.fillStyle(0xf0f0ea, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.95, lobbyY + TILE_SIZE * 1.7, TILE_SIZE * 0.7, TILE_SIZE * 0.18)

    backdrop.fillStyle(0x9a9faa, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.72, lobbyY + TILE_SIZE * 2.2, TILE_SIZE * 1.6, TILE_SIZE * 0.42)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.32, lobbyY + TILE_SIZE * 2.2, TILE_SIZE * 1.6, TILE_SIZE * 0.42)
    backdrop.fillStyle(0xc8d1d6, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.88, lobbyY + TILE_SIZE * 2.08, TILE_SIZE * 1.28, TILE_SIZE * 0.16)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.16, lobbyY + TILE_SIZE * 2.08, TILE_SIZE * 1.28, TILE_SIZE * 0.16)

    backdrop.fillStyle(0x6a7f55, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.38, lobbyY + TILE_SIZE * 2.0, TILE_SIZE * 0.34, TILE_SIZE * 0.62)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 0.72, lobbyY + TILE_SIZE * 2.0, TILE_SIZE * 0.34, TILE_SIZE * 0.62)
    backdrop.fillStyle(0x8eab74, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 0.22, lobbyY + TILE_SIZE * 1.78, TILE_SIZE * 0.66, TILE_SIZE * 0.34)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 0.88, lobbyY + TILE_SIZE * 1.78, TILE_SIZE * 0.66, TILE_SIZE * 0.34)
    // Reception approach lines, rug edging, and side wayfinding.
    backdrop.fillStyle(0xe9dcc0, 0.88)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.78, lobbyY + TILE_SIZE * 2.46, TILE_SIZE * 3.56, 2)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 0.06, lobbyY + TILE_SIZE * 1.98, 2, TILE_SIZE * 1.18)
    backdrop.fillStyle(0x9c8866, 0.92)
    backdrop.strokeRect(gateCenterX - TILE_SIZE * 1.82, lobbyY + TILE_SIZE * 2.3, TILE_SIZE * 3.64, TILE_SIZE * 0.66)
    backdrop.fillStyle(0xb79e72, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 2.42, lobbyY + TILE_SIZE * 2.03, TILE_SIZE * 0.24, TILE_SIZE * 0.78)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.66, lobbyY + TILE_SIZE * 2.03, TILE_SIZE * 0.24, TILE_SIZE * 0.78)
    backdrop.fillStyle(0xe8dfc6, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 2.16, lobbyY + TILE_SIZE * 1.9, TILE_SIZE * 0.76, TILE_SIZE * 0.16)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.92, lobbyY + TILE_SIZE * 1.9, TILE_SIZE * 0.76, TILE_SIZE * 0.16)
    backdrop.fillStyle(0x5f7b69, 1)
    backdrop.fillRect(lobbyX + TILE_SIZE * 2.16, lobbyY + TILE_SIZE * 2.18, TILE_SIZE * 0.76, TILE_SIZE * 0.56)
    backdrop.fillRect(lobbyX + lobbyWidth - TILE_SIZE * 2.92, lobbyY + TILE_SIZE * 2.18, TILE_SIZE * 0.76, TILE_SIZE * 0.56)

    // Threshold from lobby to outside path.
    backdrop.fillStyle(0x8f7557, 1)
    backdrop.fillRect(gateCenterX - frontOpeningW / 2, lobbyBottom - wallThickness, frontOpeningW, wallThickness)
    backdrop.fillStyle(0xd7c79e, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 0.72, lobbyBottom + 2, TILE_SIZE * 1.44, 3)
    // Small buffer vestibule between indoor lobby and outdoor approach.
    const vestibuleW = TILE_SIZE * 4.7
    const vestibuleH = TILE_SIZE * 1.2
    const vestibuleY = lobbyBottom - 1
    backdrop.fillStyle(0xc1d4cf, 0.98)
    backdrop.fillRect(gateCenterX - vestibuleW / 2, vestibuleY, vestibuleW, vestibuleH)
    backdrop.fillStyle(0xf2ede4, 1)
    backdrop.fillRect(gateCenterX - vestibuleW / 2, vestibuleY, wallThickness, vestibuleH)
    backdrop.fillRect(gateCenterX + vestibuleW / 2 - wallThickness, vestibuleY, wallThickness, vestibuleH)
    backdrop.fillStyle(0xd7e7e3, 1)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 1.35, vestibuleY + TILE_SIZE * 0.34, TILE_SIZE * 2.7, 2)
    backdrop.fillStyle(0x94a7a3, 0.8)
    backdrop.fillRect(gateCenterX - vestibuleW / 2 + TILE_SIZE * 0.35, vestibuleY + TILE_SIZE * 0.18, TILE_SIZE * 0.55, 2)
    backdrop.fillRect(gateCenterX + vestibuleW / 2 - TILE_SIZE * 0.9, vestibuleY + TILE_SIZE * 0.18, TILE_SIZE * 0.55, 2)

    // Courtyard feature on the front lawn: a landscape logo marker.
    const courtyardY = hallBottom + TILE_SIZE * 1.7
    const logoX = gateCenterX + TILE_SIZE * 11.2

    // Landscape logo now rendered as standalone letters without a backing plinth.
    backdrop.fillStyle(pal.logoDirt, 0.95)
    backdrop.fillRect(logoX - TILE_SIZE * 4.3, courtyardY - TILE_SIZE * 1.2, TILE_SIZE * 8.6, TILE_SIZE * 2.35)
    backdrop.fillStyle(pal.logoDirtHi, 0.9)
    backdrop.fillRect(logoX - TILE_SIZE * 3.5, courtyardY - TILE_SIZE * 0.8, TILE_SIZE * 7.0, TILE_SIZE * 1.55)
    for (const shrubX of [logoX - TILE_SIZE * 4.9, logoX - TILE_SIZE * 3.9, logoX + TILE_SIZE * 3.9, logoX + TILE_SIZE * 4.9]) {
      backdrop.fillStyle(0x5a7a49, 1)
      backdrop.fillRect(shrubX, courtyardY - TILE_SIZE * 0.42, TILE_SIZE * 0.72, TILE_SIZE * 0.42)
      backdrop.fillStyle(0x7ea56a, 1)
      backdrop.fillRect(shrubX + 2, courtyardY - TILE_SIZE * 0.54, TILE_SIZE * 0.48, TILE_SIZE * 0.22)
    }

    // Fence line across the campus front.
    backdrop.fillStyle(pal.fence, 1)
    for (let x = startX + TILE_SIZE * 0.8; x < endX; x += TILE_SIZE * 1.25) {
      if (x > gateCenterX - TILE_SIZE * 5.4 && x < gateCenterX + TILE_SIZE * 5.4) continue
      backdrop.fillRect(x, fenceY, 3, TILE_SIZE * 0.8)
    }
    backdrop.fillRect(startX + TILE_SIZE * 0.8, fenceY + TILE_SIZE * 0.6, gateCenterX - TILE_SIZE * 5.45 - (startX + TILE_SIZE * 0.8), 3)
    backdrop.fillRect(gateCenterX + TILE_SIZE * 5.45, fenceY + TILE_SIZE * 0.6, endX - TILE_SIZE * 0.8 - (gateCenterX + TILE_SIZE * 5.45), 3)

    backdrop.fillStyle(pal.road, 1)
    backdrop.fillRect(startX, roadY, width, roadHeight)
    backdrop.fillStyle(pal.roadDash, 1)
    for (let x = startX + TILE_SIZE; x < endX; x += TILE_SIZE * 2.6) {
      backdrop.fillRect(x, roadY + TILE_SIZE * 2.0, TILE_SIZE * 1.6, 4)
    }

    for (let x = startX + TILE_SIZE * 2; x < endX; x += TILE_SIZE * 4.8) {
      backdrop.fillStyle(pal.curb, 1)
      backdrop.fillRect(x, roadY - TILE_SIZE * 1.45, 4, TILE_SIZE * 1.45)
      backdrop.fillStyle(0xe5c670, 0.95)
      backdrop.fillRect(x - 4, roadY - TILE_SIZE * 1.5, 12, 4)
    }
    // Bollards + warm glow read as "night"; daytime keeps slim posts only so the lot still feels sun-lit.
    for (const lampX of [gateCenterX - TILE_SIZE * 4.8, gateCenterX - TILE_SIZE * 2.4, gateCenterX + TILE_SIZE * 2.4, gateCenterX + TILE_SIZE * 4.8, logoX - TILE_SIZE * 5.7, logoX + TILE_SIZE * 5.7]) {
      const lampY = lampX === logoX - TILE_SIZE * 5.7 || lampX === logoX + TILE_SIZE * 5.7 ? courtyardY + TILE_SIZE * 0.75 : forecourtNodeY + TILE_SIZE * 0.85
      backdrop.fillStyle(0x4d4438, 1)
      backdrop.fillRect(lampX, lampY, 3, TILE_SIZE * 0.72)
      if (!isDay) {
        backdrop.fillStyle(0xefcf7a, 0.95)
        backdrop.fillRect(lampX - 4, lampY - 4, 11, 4)
        backdrop.fillStyle(0xf2dd9a, 0.2)
        backdrop.fillRect(lampX - TILE_SIZE * 0.38, lampY + TILE_SIZE * 0.42, TILE_SIZE * 0.76, TILE_SIZE * 0.24)
      }
    }

    // Full-width water below the road.
    backdrop.fillStyle(pal.water, 1)
    backdrop.fillRect(startX, waterTopY, width, bottomY - waterTopY)

    // Sparse ripple groups so the lake reads as water, without reverting to full-width dashed lines.
    const rippleRows = [waterTopY + TILE_SIZE * 0.9, waterTopY + TILE_SIZE * 2.0, waterTopY + TILE_SIZE * 3.25]
    for (let rowIndex = 0; rowIndex < rippleRows.length; rowIndex++) {
      const y = rippleRows[rowIndex]
      const offset = rowIndex % 2 === 0 ? TILE_SIZE * 1.1 : TILE_SIZE * 2.4
      backdrop.fillStyle(pal.ripple, isDay ? 0.38 : 0.42)
      for (let x = startX + offset; x < endX - TILE_SIZE; x += TILE_SIZE * 5.2) {
        backdrop.fillRect(x, y, TILE_SIZE * 1.8, 2)
        backdrop.fillRect(x + TILE_SIZE * 2.35, y + 3, TILE_SIZE * 0.9, 2)
      }
    }

    // Localized ripple ring around the dock.
    backdrop.fillStyle(pal.rippleHi, isDay ? 0.38 : 0.34)
    for (const rippleY of [waterTopY + TILE_SIZE * 1.05, waterTopY + TILE_SIZE * 1.8]) {
      backdrop.fillRect(gateCenterX - TILE_SIZE * 4.8, rippleY, TILE_SIZE * 2.0, 2)
      backdrop.fillRect(gateCenterX + TILE_SIZE * 2.8, rippleY, TILE_SIZE * 2.0, 2)
    }

    const dockWidth = TILE_SIZE * 12.5
    const pierTopY = roadY + roadHeight - 2
    backdrop.fillStyle(0x6f543b, 1)
    backdrop.fillRect(gateCenterX - dockWidth / 2, dockY, dockWidth, TILE_SIZE * 1.75)
    backdrop.fillRect(gateCenterX - TILE_SIZE * 0.55, pierTopY, TILE_SIZE * 1.1, dockY - pierTopY)
    backdrop.fillStyle(0x8d6a49, 1)
    for (let x = gateCenterX - dockWidth / 2 + 6; x < gateCenterX + dockWidth / 2 - 6; x += TILE_SIZE * 0.7) {
      backdrop.fillRect(x, dockY + TILE_SIZE * 0.35, 3, TILE_SIZE * 1.2)
    }

    // Subtle retaining edge above the water so the dock reads cleanly.
    backdrop.fillStyle(0x5b6a62, 1)
    backdrop.fillRect(startX, waterTopY - 4, width, 4)
    backdrop.fillStyle(0x6f8177, 1)
    backdrop.fillRect(startX, waterTopY - 8, width, 3)

    if (this.outdoorParking) {
      const pi = this.decor.indexOf(this.outdoorParking)
      if (pi >= 0) this.decor.splice(pi, 1)
      this.outdoorParking.destroy()
      this.outdoorParking = null
    }
    const parkingG = scene.add.graphics()
    // Between outdoor backdrop (-320) and office shell (-140); must stay above lawn so asphalt wins.
    parkingG.setDepth(-300)
    const PARKING_ASPHALT = 0x3e434c
    const PARKING_LINE = 0xe8e2d8
    const parkingLots = [
      { x: startX + TILE_SIZE * 2.2, count: 5 },
      { x: endX - TILE_SIZE * 11.4, count: 5 },
    ]
    // Draw all slabs, then all strokes, then all cars — avoids Phaser Graphics path/state quirks between lots.
    parkingG.lineStyle(0, 0x000000, 0)
    parkingG.fillStyle(PARKING_ASPHALT, 1)
    for (const lot of parkingLots) {
      parkingG.fillRect(lot.x, parkingY, TILE_SIZE * 12.8, TILE_SIZE * 3.8)
    }
    parkingG.lineStyle(1, PARKING_LINE, 0.95)
    for (const lot of parkingLots) {
      for (let i = 0; i < lot.count; i++) {
        const slotX = lot.x + TILE_SIZE * 0.45 + i * TILE_SIZE * 1.5
        parkingG.strokeRect(slotX, parkingY + TILE_SIZE * 0.5, TILE_SIZE * 1.55, TILE_SIZE * 2.55)
      }
    }
    parkingG.lineStyle(0, 0x000000, 0)
    const slotW = TILE_SIZE * 1.55
    const slotH = TILE_SIZE * 2.55
    const slotPadY = TILE_SIZE * 0.5
    const nightLights = !isDay
    for (const lot of parkingLots) {
      const mirrorForSide = lot.x > gateCenterX - TILE_SIZE
      for (let i = 0; i < lot.count - 1; i += 2) {
        const slotX = lot.x + TILE_SIZE * 0.45 + i * TILE_SIZE * 1.5
        const cx = slotX + slotW / 2
        const cy = parkingY + slotPadY + slotH / 2
        const warm = i % 4 === 0
        this.paintStylizedParkingCar(parkingG, cx, cy, mirrorForSide, warm, nightLights)
      }
    }

    if (!opts?.skipLabels) {
      this.addPixelLabel(scene, gateCenterX, lobbyY + TILE_SIZE * 0.6, PRODUCT_NAME.toUpperCase(), '#4e6b68', '#eef4ec', '10px')
      const lawnLogoBaseY = courtyardY - TILE_SIZE * 0.24
      const letterW = TILE_SIZE * 0.88
      const letterGap = TILE_SIZE * 0.28
      const word = PRODUCT_NAME.toUpperCase()
      const totalLogoWidth = word.length * letterW + (word.length - 1) * letterGap
      let letterX = logoX - totalLogoWidth / 2
      for (const char of word) {
        const shadow = scene.add.text(letterX + TILE_SIZE * 0.12, lawnLogoBaseY + TILE_SIZE * 0.14, char, {
          fontSize: '26px',
          fontFamily: 'monospace',
          color: '#4e6b46',
          resolution: 2,
        })
        shadow.setOrigin(0, 0.5)
        shadow.setDepth(-95)
        this.decor.push(shadow)

        const face = scene.add.text(letterX, lawnLogoBaseY, char, {
          fontSize: '26px',
          fontFamily: 'monospace',
          color: '#f6f6ee',
          stroke: '#8f8c7a',
          strokeThickness: 1,
          resolution: 2,
        })
        face.setOrigin(0, 0.5)
        face.setDepth(-94)
        this.decor.push(face)

        letterX += letterW + letterGap
      }
      this.addPixelLabel(scene, gateCenterX, dockY + TILE_SIZE * 0.95, 'LAKE DOCK', '#1d3d4f', '#d7eef1', '9px')
    }

    this.decor.push(backdrop, parkingG)
    this.outdoorBackdrop = backdrop
    this.outdoorParking = parkingG
  }

  /** Redraw outdoor environment when local time crosses the day/night boundary (keeps lawn text objects). */
  refreshOutdoorDayNight(scene: Phaser.Scene, isDay: boolean) {
    const offices = getOffices()
    const totalWidth = ((offices[offices.length - 1]?.offsetCol ?? 0) + OFFICE_COLS) * TILE_SIZE
    const totalHeight = OFFICE_ROWS * TILE_SIZE
    if (this.outdoorParking) {
      const pi = this.decor.indexOf(this.outdoorParking)
      if (pi >= 0) this.decor.splice(pi, 1)
      this.outdoorParking.destroy()
      this.outdoorParking = null
    }
    if (this.outdoorBackdrop) {
      const i = this.decor.indexOf(this.outdoorBackdrop)
      if (i >= 0) this.decor.splice(i, 1)
      this.outdoorBackdrop.destroy()
      this.outdoorBackdrop = null
    }
    this.buildOutdoorBackdrop(scene, totalWidth, totalHeight, offices, isDay, { skipLabels: true })
  }

  private buildBoundaryDecor(scene: Phaser.Scene, offices: OfficeConfig[], isDay: boolean) {
    const base = scene.add.graphics()
    base.setDepth(-140)
    const detail = scene.add.graphics()
    detail.setDepth(-95)

    const totalWidth = ((offices[offices.length - 1]?.offsetCol ?? 0) + OFFICE_COLS) * TILE_SIZE
    const totalHeight = OFFICE_ROWS * TILE_SIZE
    const officeWidth = OFFICE_COLS * TILE_SIZE
    const lobbyHeight = TILE_SIZE * 2
    const wainscotY = TILE_SIZE * 20
    this.buildOutdoorBackdrop(scene, totalWidth, totalHeight, offices, isDay)

    // Base shell for the whole floor plate.
    base.fillStyle(0x26211c, 1)
    base.fillRect(-TILE_SIZE, 0, totalWidth + TILE_SIZE * 2, totalHeight)
    base.fillStyle(0x312922, 1)
    base.fillRect(0, 0, totalWidth, totalHeight)
    // Unified perimeter so the three offices read as one complete floor plate.
    base.fillStyle(0x5c4c3f, 1)
    base.fillRect(0, 0, totalWidth, 8)
    base.fillRect(0, totalHeight - 8, totalWidth, 8)
    base.fillRect(0, 0, 8, totalHeight)
    base.fillRect(totalWidth - 8, 0, 8, totalHeight)
    base.fillStyle(0x241e19, 1)
    base.fillRect(8, 8, totalWidth - 16, 3)
    base.fillRect(8, totalHeight - 11, totalWidth - 16, 3)

    // Top lobby band.
    base.fillStyle(0x1a1714, 1)
    base.fillRect(0, 0, totalWidth, lobbyHeight)
    base.fillStyle(0x4b4035, 1)
    base.fillRect(0, lobbyHeight - 5, totalWidth, 5)

    // Global floor base + subtle horizontal zoning.
    base.fillStyle(0x40362d, 1)
    base.fillRect(0, lobbyHeight, totalWidth, totalHeight - lobbyHeight)
    base.fillStyle(0x4a3e34, 1)
    base.fillRect(0, TILE_SIZE * 8, totalWidth, TILE_SIZE * 4)
    base.fillStyle(0x3a3028, 1)
    base.fillRect(0, wainscotY, totalWidth, totalHeight - wainscotY)

    // Pixel-floor grid to make the scene feel like a cohesive map instead of empty fill.
    base.lineStyle(1, 0x5a4b3f, 0.32)
    for (let x = 0; x <= totalWidth; x += TILE_SIZE) {
      base.beginPath()
      base.moveTo(x, lobbyHeight)
      base.lineTo(x, totalHeight)
      base.strokePath()
    }
    for (let y = lobbyHeight; y <= totalHeight; y += TILE_SIZE) {
      base.beginPath()
      base.moveTo(0, y)
      base.lineTo(totalWidth, y)
      base.strokePath()
    }

    // Ceiling lights along the lobby.
    for (let x = TILE_SIZE * 3; x < totalWidth; x += TILE_SIZE * 6) {
      base.fillStyle(0xe0ba6f, 0.85)
      base.fillRect(x, TILE_SIZE * 0.7, TILE_SIZE * 1.5, 4)
      base.fillStyle(0x8e6f42, 1)
      base.fillRect(x + TILE_SIZE * 0.2, TILE_SIZE * 0.55, TILE_SIZE * 1.1, 2)
    }

    this.addPixelLabel(scene, totalWidth / 2, TILE_SIZE * 0.95, 'MAIN HALL', '#2a221b', '#f3dcc0', '11px')

    for (const office of offices) {
      const x = office.offsetCol * TILE_SIZE
      const centerX = x + officeWidth / 2
      const officeIndex = offices.findIndex(o => o.id === office.id)

      // Outer office shell.
      base.fillStyle(0x58483b, 1)
      base.fillRect(x - 10, 0, officeWidth + 20, totalHeight)

      // Inner trim to visually separate the room cutaway from the shared floor plate.
      base.fillStyle(0x231d18, 1)
      base.fillRect(x - 4, 0, officeWidth + 8, totalHeight)

      base.lineStyle(2, 0x7b6858, 0.9)
      base.strokeRect(x - 10, 0, officeWidth + 20, totalHeight)

      base.lineStyle(2, 0xa7927e, 0.34)
      base.strokeRect(x - 4, 0, officeWidth + 8, totalHeight)

      // Lobby lintel + label rail.
      base.fillStyle(0x181513, 1)
      base.fillRect(x - 4, 0, officeWidth + 8, TILE_SIZE)

      base.fillStyle(0x6d5b4c, 1)
      base.fillRect(x + TILE_SIZE * 5.5, TILE_SIZE * 0.55, TILE_SIZE * 9, 5)

      // Corner blocks for a more intentional pixel-architecture look.
      base.fillStyle(0x8a7562, 1)
      base.fillRect(x - 10, 0, 8, 8)
      base.fillRect(x + officeWidth + 2, 0, 8, 8)
      base.fillRect(x - 10, totalHeight - 8, 8, 8)
      base.fillRect(x + officeWidth + 2, totalHeight - 8, 8, 8)

      // Lower wall trim to visually anchor each office to the floor.
      base.fillStyle(0x312820, 1)
      base.fillRect(x - 4, wainscotY, officeWidth + 8, 6)

      // Fill the upper side voids with small support zones using the imported tileset.
      const leftNookX = x + TILE_SIZE * 0.7
      const rightNookX = x + TILE_SIZE * 14.65
      const nookY = TILE_SIZE * 1.05
      const nookW = TILE_SIZE * 4.45
      const nookH = TILE_SIZE * 6.4
      const supportBandY = nookY + nookH + TILE_SIZE * 0.35
      const nookDecorOffsetY = TILE_SIZE * 0.55
      const leftWallpaper = officeIndex % 2 === 0 ? 0x5d5245 : 0x4d5a46
      const rightWallpaper = officeIndex % 2 === 0 ? 0x465459 : 0x5b4f46
      const leftFloor = officeIndex % 2 === 0 ? 0x4f6e88 : 0x6f5b74
      const rightFloor = officeIndex % 2 === 0 ? 0x567867 : 0x8a724f
      const leftRug = officeIndex % 2 === 0 ? 0x6b7fb5 : 0x9a6a7b
      const rightRug = officeIndex % 2 === 0 ? 0x5e8b79 : 0xa78758

      // Stronger structural band between top support rooms and the main office.
      base.fillStyle(0x2b241f, 1)
      base.fillRect(x + TILE_SIZE * 0.58, supportBandY, officeWidth - TILE_SIZE * 1.16, TILE_SIZE * 0.44)
      base.fillStyle(0x75624f, 1)
      base.fillRect(x + TILE_SIZE * 0.92, supportBandY + 2, officeWidth - TILE_SIZE * 1.84, 2)
      base.fillRect(x + TILE_SIZE * 4.3, supportBandY - TILE_SIZE * 0.2, 4, TILE_SIZE * 0.84)
      base.fillRect(x + officeWidth - TILE_SIZE * 4.3, supportBandY - TILE_SIZE * 0.2, 4, TILE_SIZE * 0.84)

      // Frame and floor treatment so these areas read as fitted support rooms, not empty voids.
      detail.fillStyle(0x1d1916, 1)
      detail.fillRect(leftNookX, nookY, nookW, nookH)
      detail.fillRect(rightNookX, nookY, nookW, nookH)
      detail.fillStyle(0x2f2922, 1)
      detail.fillRect(leftNookX + 4, nookY + 4, nookW - 8, nookH - 8)
      detail.fillRect(rightNookX + 4, nookY + 4, nookW - 8, nookH - 8)
      detail.fillStyle(leftWallpaper, 1)
      detail.fillRect(leftNookX + 4, nookY + 4, nookW - 8, TILE_SIZE * 0.7)
      detail.fillStyle(rightWallpaper, 1)
      detail.fillRect(rightNookX + 4, nookY + 4, nookW - 8, TILE_SIZE * 0.7)
      detail.fillStyle(leftFloor, 0.95)
      detail.fillRect(leftNookX + 4, nookY + TILE_SIZE * 1.15, nookW - 8, nookH - TILE_SIZE * 1.45)
      detail.fillStyle(rightFloor, 0.95)
      detail.fillRect(rightNookX + 4, nookY + TILE_SIZE * 1.15, nookW - 8, nookH - TILE_SIZE * 1.45)
      detail.fillStyle(0xe6d2a4, 0.16)
      detail.fillRect(leftNookX + 10, nookY + 10, nookW - 20, 2)
      detail.fillRect(rightNookX + 10, nookY + 10, nookW - 20, 2)
      detail.lineStyle(1, 0x5e5244, 0.28)
      for (let floorX = leftNookX + 12; floorX < leftNookX + nookW - 4; floorX += TILE_SIZE) {
        detail.beginPath()
        detail.moveTo(floorX, nookY + TILE_SIZE * 1.15)
        detail.lineTo(floorX, nookY + nookH - 4)
        detail.strokePath()
      }
      for (let floorX = rightNookX + 12; floorX < rightNookX + nookW - 4; floorX += TILE_SIZE) {
        detail.beginPath()
        detail.moveTo(floorX, nookY + TILE_SIZE * 1.15)
        detail.lineTo(floorX, nookY + nookH - 4)
        detail.strokePath()
      }
      for (let floorY = nookY + TILE_SIZE * 1.45; floorY < nookY + nookH - 4; floorY += TILE_SIZE * 0.82) {
        detail.beginPath()
        detail.moveTo(leftNookX + 4, floorY)
        detail.lineTo(leftNookX + nookW - 4, floorY)
        detail.strokePath()
        detail.beginPath()
        detail.moveTo(rightNookX + 4, floorY)
        detail.lineTo(rightNookX + nookW - 4, floorY)
        detail.strokePath()
      }
      detail.fillStyle(leftRug, 0.94)
      detail.fillRect(leftNookX + TILE_SIZE * 0.45, nookY + TILE_SIZE * 3.95, TILE_SIZE * 2.7, TILE_SIZE * 1.25)
      detail.fillStyle(0xe8ddc7, 0.34)
      detail.strokeRect(leftNookX + TILE_SIZE * 0.45, nookY + TILE_SIZE * 3.95, TILE_SIZE * 2.7, TILE_SIZE * 1.25)
      detail.fillRect(leftNookX + TILE_SIZE * 0.75, nookY + TILE_SIZE * 4.35, TILE_SIZE * 2.1, 3)
      detail.fillStyle(rightRug, 0.94)
      detail.fillRect(rightNookX + TILE_SIZE * 0.45, nookY + TILE_SIZE * 3.95, TILE_SIZE * 2.7, TILE_SIZE * 1.25)
      detail.fillStyle(0xe8ddc7, 0.34)
      detail.strokeRect(rightNookX + TILE_SIZE * 0.45, nookY + TILE_SIZE * 3.95, TILE_SIZE * 2.7, TILE_SIZE * 1.25)
      detail.fillRect(rightNookX + TILE_SIZE * 0.75, nookY + TILE_SIZE * 4.35, TILE_SIZE * 2.1, 3)
      detail.fillStyle(0xe7c97f, 0.85)
      detail.fillRect(leftNookX + TILE_SIZE * 1.45, nookY + 12, TILE_SIZE * 1.55, 4)
      detail.fillRect(rightNookX + TILE_SIZE * 1.45, nookY + 12, TILE_SIZE * 1.55, 4)
      detail.fillStyle(0x5e4d3f, 0.5)
      detail.fillRect(leftNookX + 4, nookY + nookH - 10, nookW - 8, 4)
      detail.fillRect(rightNookX + 4, nookY + nookH - 10, nookW - 8, 4)
      detail.fillStyle(0x7a6651, 1)
      detail.fillRect(leftNookX + TILE_SIZE * 0.22, nookY + nookH - TILE_SIZE * 0.7, nookW - TILE_SIZE * 0.44, 3)
      detail.fillRect(rightNookX + TILE_SIZE * 0.22, nookY + nookH - TILE_SIZE * 0.7, nookW - TILE_SIZE * 0.44, 3)

      this.addPixelLabel(scene, leftNookX + nookW / 2, nookY + TILE_SIZE * 0.42, officeIndex % 2 === 0 ? 'ARCHIVE' : 'UTILITY', '#4a4035', '#f1dfc2', '8px')
      this.addPixelLabel(scene, rightNookX + nookW / 2, nookY + TILE_SIZE * 0.42, officeIndex % 2 === 0 ? 'PANTRY' : 'SUPPLY', '#4a4035', '#f1dfc2', '8px')

      if (officeIndex % 2 === 0) {
        this.addTilesetSprite(scene, leftNookX + 12, nookY + 14 + nookDecorOffsetY, this.tilesetFrames.shelfA)
        this.addTilesetSprite(scene, leftNookX + 44, nookY + 14 + nookDecorOffsetY, this.tilesetFrames.shelfB)
        this.addTilesetSprite(scene, leftNookX + 12, nookY + 48 + nookDecorOffsetY, this.tilesetFrames.shelfA)
        this.addTilesetSprite(scene, leftNookX + 14, nookY + 50 + nookDecorOffsetY, this.tilesetFrames.boxC)
        this.addTilesetSprite(scene, leftNookX + 26, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.boxA)
        this.addTilesetSprite(scene, leftNookX + 58, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.boxB)
        this.addTilesetSprite(scene, leftNookX + 60, nookY + 48 + nookDecorOffsetY, this.tilesetFrames.plantA)

        this.addTilesetSprite(scene, rightNookX + 22, nookY + 18 + nookDecorOffsetY, this.tilesetFrames.waterCooler)
        this.addTilesetSprite(scene, rightNookX + 56, nookY + 18 + nookDecorOffsetY, this.tilesetFrames.plantA)
        this.addTilesetSprite(scene, rightNookX + 20, nookY + 50 + nookDecorOffsetY, this.tilesetFrames.shelfA)
        this.addTilesetSprite(scene, rightNookX + 18, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.fridge)
        this.addTilesetSprite(scene, rightNookX + 54, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.boxA)
      } else {
        this.addTilesetSprite(scene, leftNookX + 16, nookY + 18 + nookDecorOffsetY, this.tilesetFrames.waterCoolerAlt)
        this.addTilesetSprite(scene, leftNookX + 52, nookY + 18 + nookDecorOffsetY, this.tilesetFrames.plantB)
        this.addTilesetSprite(scene, leftNookX + 16, nookY + 52 + nookDecorOffsetY, this.tilesetFrames.shelfB)
        this.addTilesetSprite(scene, leftNookX + 24, nookY + 84 + nookDecorOffsetY, this.tilesetFrames.boxC)
        this.addTilesetSprite(scene, leftNookX + 58, nookY + 84 + nookDecorOffsetY, this.tilesetFrames.boxA)

        this.addTilesetSprite(scene, rightNookX + 10, nookY + 14 + nookDecorOffsetY, this.tilesetFrames.shelfB)
        this.addTilesetSprite(scene, rightNookX + 42, nookY + 14 + nookDecorOffsetY, this.tilesetFrames.shelfA)
        this.addTilesetSprite(scene, rightNookX + 10, nookY + 50 + nookDecorOffsetY, this.tilesetFrames.waterCooler)
        this.addTilesetSprite(scene, rightNookX + 26, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.vending)
        this.addTilesetSprite(scene, rightNookX + 58, nookY + 82 + nookDecorOffsetY, this.tilesetFrames.plantB)
      }

    }

    for (let i = 0; i < offices.length - 1; i++) {
      const gapX = (offices[i].offsetCol + OFFICE_COLS) * TILE_SIZE
      const gapWidth = GAP_COLS * TILE_SIZE
      const centerX = gapX + gapWidth / 2
      const isSharedGap = i === 0
      const accent = isSharedGap ? 0x5f7d63 : 0x6a6f8f
      const zoneLabel = isSharedGap ? 'SHARE' : 'MEET'

      // Structural wall strip.
      base.fillStyle(0x201b17, 1)
      base.fillRect(gapX, 0, gapWidth, totalHeight)

      // Main corridor shaft.
      base.fillStyle(0x3a3028, 1)
      base.fillRect(gapX + 5, lobbyHeight, gapWidth - 10, totalHeight - lobbyHeight)
      base.fillStyle(0x4f4338, 1)
      for (let y = lobbyHeight + 6; y < totalHeight - 6; y += 14) {
        base.fillRect(gapX + 8, y, gapWidth - 16, 2)
      }
      // Structural core / service shaft treatment inside each vertical connection strip.
      base.fillStyle(0x27211c, 1)
      base.fillRect(centerX - TILE_SIZE * 0.52, TILE_SIZE * 2.4, TILE_SIZE * 1.04, totalHeight - TILE_SIZE * 4.8)
      base.fillStyle(0x65584c, 1)
      base.fillRect(centerX - 2, TILE_SIZE * 2.7, 4, totalHeight - TILE_SIZE * 5.4)
      base.fillStyle(0x8a7866, 0.88)
      base.fillRect(centerX - TILE_SIZE * 0.68, TILE_SIZE * 3.15, TILE_SIZE * 0.3, TILE_SIZE * 1.25)
      base.fillRect(centerX + TILE_SIZE * 0.38, TILE_SIZE * 3.15, TILE_SIZE * 0.3, TILE_SIZE * 1.25)
      base.fillRect(centerX - TILE_SIZE * 0.68, TILE_SIZE * 15.6, TILE_SIZE * 0.3, TILE_SIZE * 1.25)
      base.fillRect(centerX + TILE_SIZE * 0.38, TILE_SIZE * 15.6, TILE_SIZE * 0.3, TILE_SIZE * 1.25)

      // Corridor center line.
      base.lineStyle(2, 0x7d6a56, 0.8)
      base.beginPath()
      base.moveTo(centerX, lobbyHeight + 8)
      base.lineTo(centerX, totalHeight - 8)
      base.strokePath()

      // Shared area / meeting area node in each connection strip.
      base.fillStyle(accent, 0.95)
      base.fillRect(gapX + 7, TILE_SIZE * 11.5, gapWidth - 14, TILE_SIZE * 2.2)
      base.lineStyle(1, 0xd8d1b7, 0.18)
      base.strokeRect(gapX + 7, TILE_SIZE * 11.5, gapWidth - 14, TILE_SIZE * 2.2)

      if (isSharedGap) {
        // Shared zone: sofa, table, coffee stand, plant.
        detail.fillStyle(0x845f4f, 1)
        detail.fillRect(centerX - 12, TILE_SIZE * 11.95, 24, 7)
        detail.fillStyle(0xd8c292, 1)
        detail.fillRect(centerX - 5, TILE_SIZE * 12.2, 10, 4)
        detail.fillStyle(0x9cab77, 1)
        detail.fillRect(centerX - 15, TILE_SIZE * 13.1, 5, 6)
        detail.fillStyle(0xa37b57, 1)
        detail.fillRect(centerX + 8, TILE_SIZE * 13.0, 7, 7)
        detail.fillStyle(0xe6d8bb, 0.85)
        detail.fillRect(centerX + 10, TILE_SIZE * 13.25, 3, 3)
      } else {
        // Meeting zone: table, four seats, presentation board.
        detail.fillStyle(0x8f7657, 1)
        detail.fillRect(centerX - 8, TILE_SIZE * 12.1, 16, 9)
        detail.fillStyle(0x4c5976, 1)
        detail.fillRect(centerX - 13, TILE_SIZE * 12.55, 4, 4)
        detail.fillRect(centerX + 9, TILE_SIZE * 12.55, 4, 4)
        detail.fillRect(centerX - 2, TILE_SIZE * 11.7, 4, 4)
        detail.fillRect(centerX - 2, TILE_SIZE * 13.95, 4, 4)
        detail.fillStyle(0xd8e3ea, 0.85)
        detail.fillRect(centerX - 14, TILE_SIZE * 11.65, 6, 3)
      }

      // Corridor lights.
      base.fillStyle(0xe0ba6f, 0.9)
      base.fillRect(centerX - 5, TILE_SIZE * 4.5, 10, 3)
      base.fillRect(centerX - 5, TILE_SIZE * 18.5, 10, 3)

      this.addPixelLabel(scene, centerX, TILE_SIZE * 8.75, zoneLabel, '#2f2822', '#f1dcc0', '8px')
    }

    // Outer edges read as a full stitched floor plan.
    base.fillStyle(0x191614, 1)
    base.fillRect(0, totalHeight - 6, totalWidth, 6)
    base.fillRect(0, 0, 6, totalHeight)
    base.fillRect(totalWidth - 6, 0, 6, totalHeight)

    // Simple wayfinding markers along the main hall.
    this.addPixelLabel(scene, TILE_SIZE * 7, TILE_SIZE * 1.0, '< WEST', '#2d241c', '#e8d5b6', '8px')
    this.addPixelLabel(scene, totalWidth - TILE_SIZE * 7, TILE_SIZE * 1.0, 'EAST >', '#2d241c', '#e8d5b6', '8px')

    this.decor.push(base, detail)
  }

  buildMap(scene: Phaser.Scene, isDay = isLocalDaytime()): MapData {
    this.clearSceneDecor()
    const offices = getOffices()
    const totalWidth = ((offices[offices.length - 1]?.offsetCol ?? 0) + OFFICE_COLS) * TILE_SIZE
    const totalHeight = OFFICE_ROWS * TILE_SIZE
    const waterfrontMetrics = this.getOutdoorMetrics(totalWidth, totalHeight)
    this.buildBoundaryDecor(scene, offices, isDay)

    for (const office of offices) {
      const centerX = (office.offsetCol + OFFICE_COLS / 2) * TILE_SIZE
      const bg = scene.add.image(office.offsetCol * TILE_SIZE, 0, 'office-bg')
      bg.setOrigin(0, 0)
      bg.setDepth(-100)
      this.bgSprites.push(bg)
      this.addForegroundOccluders(scene, office)

      const label = scene.add.text(
        centerX,
        6,
        office.name,
        { fontSize: '14px', fontFamily: 'monospace', color: '#ffffff', stroke: '#000000', strokeThickness: 3, align: 'center' },
      )
      label.setOrigin(0.5, 0)
      label.setDepth(99999)
      label.setData('officeId', office.id)
      this.nameLabels.push(label)
    }

    const grid = buildCompositeGrid(offices)
    const wallBodies = OfficeMapBuilder.buildWallBodies(scene, grid)

    console.log('[OfficeMapBuilder] Composite map ready —', offices.length, 'offices')
    return {
      wallBodies,
      collisionGrid: grid,
      waterfront: {
        dockCenterX: waterfrontMetrics.gateCenterX,
        dockY: waterfrontMetrics.dockY,
        waterTopY: waterfrontMetrics.waterTopY,
        waterBottomY: waterfrontMetrics.bottomY,
      },
    }
  }

  getBackgroundSprites(): Phaser.GameObjects.Image[] {
    return this.bgSprites
  }

  getNameLabels(): Phaser.GameObjects.Text[] {
    return this.nameLabels
  }

  updateLabel(officeId: string, name: string) {
    const label = this.nameLabels.find(l => l.getData('officeId') === officeId)
    if (label) label.setText(name)
  }

  static buildWallBodies(
    scene: Phaser.Scene,
    grid: number[][],
  ): Phaser.Physics.Arcade.StaticGroup {
    const rows = grid.length
    const cols = rows > 0 ? grid[0].length : 0
    const group = scene.physics.add.staticGroup()
    for (let r = 0; r < rows; r++) {
      let start = -1
      for (let c = 0; c <= cols; c++) {
        const isWall = c < cols && grid[r][c] === 1
        if (isWall && start < 0) {
          start = c
        } else if (!isWall && start >= 0) {
          const len = c - start
          const body = scene.add.rectangle(
            start * TILE_SIZE + (len * TILE_SIZE) / 2,
            r * TILE_SIZE + TILE_SIZE / 2,
            len * TILE_SIZE,
            TILE_SIZE,
          )
          body.setVisible(false)
          group.add(body)
          start = -1
        }
      }
    }
    return group
  }

  static buildWallBodiesForRegion(
    scene: Phaser.Scene,
    grid: number[][],
    existingGroup: Phaser.Physics.Arcade.StaticGroup,
    offsetCol: number,
  ) {
    const rows = grid.length
    const cols = rows > 0 ? grid[0].length : 0
    for (let r = 0; r < rows; r++) {
      let start = -1
      for (let c = 0; c <= cols; c++) {
        const isWall = c < cols && grid[r][c] === 1
        if (isWall && start < 0) {
          start = c
        } else if (!isWall && start >= 0) {
          const len = c - start
          const body = scene.add.rectangle(
            (offsetCol + start) * TILE_SIZE + (len * TILE_SIZE) / 2,
            r * TILE_SIZE + TILE_SIZE / 2,
            len * TILE_SIZE,
            TILE_SIZE,
          )
          body.setVisible(false)
          existingGroup.add(body)
          start = -1
        }
      }
    }
  }
}
