import Phaser from 'phaser'

export const TILE_SIZE = 32
export const OFFICE_COLS = 20
export const OFFICE_ROWS = 25
export const GAP_COLS = 2
export const OFFICE_COUNT = 3
export const WORLD_COLS = OFFICE_COLS * OFFICE_COUNT + GAP_COLS * (OFFICE_COUNT - 1) // 64
export const WORLD_ROWS = OFFICE_ROWS // 25

export const MAP_COLS = WORLD_COLS
export const MAP_ROWS = WORLD_ROWS
export const OUTDOOR_MARGIN_X = TILE_SIZE * 8
export const OUTDOOR_MARGIN_TOP = TILE_SIZE * 4
export const OUTDOOR_MARGIN_BOTTOM = TILE_SIZE * 20
export const CHAR_SCALE = 1.8
export const CHAR_SCALE_X = CHAR_SCALE
export const CHAR_SCALE_Y = CHAR_SCALE
export const CHAR_SHADOW_WIDTH = 30
export const CHAR_SHADOW_HEIGHT = 8
export const CHAR_SHADOW_Y = -2
export const CHAR_SHADOW_ALPHA = 0.22

/** Daytime if local hour is in [DAYTIME_START_HOUR, DAYTIME_END_HOUR] inclusive. */
export const DAYTIME_START_HOUR = 5
/** 23 → day through 23:59; 0:00–4:59 is night when mode is Auto (no URL/storage override). */
export const DAYTIME_END_HOUR = 23

/** Parse `?day=1` / `#?day=1` / `#/path?day=1` for outdoor preview. */
function readOutdoorOverrideFromUrl(): 'day' | 'night' | null {
  if (typeof window === 'undefined') return null
  const parse = (raw: string): 'day' | 'night' | null => {
    const q = new URLSearchParams(raw)
    if (q.get('day') === '1' || q.get('daytime') === '1') return 'day'
    if (q.get('night') === '1') return 'night'
    return null
  }
  let o = parse(window.location.search || '')
  if (o) return o
  const hash = window.location.hash
  if (!hash) return null
  const qm = hash.indexOf('?')
  if (qm >= 0) {
    o = parse(hash.slice(qm + 1))
    if (o) return o
  }
  const h = hash.replace(/^#/, '')
  if (h.includes('=')) {
    o = parse(h)
    if (o) return o
  }
  return null
}

/**
 * Day vs night for the outdoor skyline. Clock: local 5:00–23:59 = day, 0:00–4:59 = night (Auto mode).
 * Override (browser): URL `?day=1` / `?daytime=1` (also after `#…?`), or `localStorage opc_outdoor_override` = `day`|`night`.
 * Legacy: `opc_outdoor_day` / `opc_outdoor_night` = `1`.
 */
export function isLocalDaytime(now = new Date()): boolean {
  if (typeof window !== 'undefined') {
    try {
      const url = readOutdoorOverrideFromUrl()
      if (url === 'day') return true
      if (url === 'night') return false
      const om = window.localStorage?.getItem('opc_outdoor_override')
      if (om === 'day') return true
      if (om === 'night') return false
      if (window.localStorage?.getItem('opc_outdoor_day') === '1') return true
      if (window.localStorage?.getItem('opc_outdoor_night') === '1') return false
    } catch {
      /* private mode / SSR */
    }
  }
  const h = now.getHours()
  return h >= DAYTIME_START_HOUR && h <= DAYTIME_END_HOUR
}

/** Phaser camera clear color to match sky / lawn edge. */
export const SCENE_CLEAR_DAY = 0xa8d4ec
export const SCENE_CLEAR_NIGHT = 0x31453a

export const WALK_SPEED_URGENT = 200
export const WALK_SPEED_NORMAL = 100
export const WALK_SPEED_RELAXED = 60

export const WANDER_PAUSE_MIN = 2.0
export const WANDER_PAUSE_MAX = 20.0
export const WANDER_MOVES_BEFORE_REST_MIN = 3
export const WANDER_MOVES_BEFORE_REST_MAX = 6
export const SEAT_REST_MIN = 120.0
export const SEAT_REST_MAX = 240.0
export const CELEBRATE_DURATION = 2.5
export const COFFEE_DURATION_MIN = 8.0
export const COFFEE_DURATION_MAX = 15.0
export const CHAT_DURATION_MIN = 5.0
export const CHAT_DURATION_MAX = 10.0
export const STATUS_BUBBLE_DURATION = 5.0
export const INACTIVE_SEAT_TIMER_MIN = 3.0
export const INACTIVE_SEAT_TIMER_RANGE = 2.0

export function createGameConfig(parent: HTMLElement, width?: number, height?: number): Phaser.Types.Core.GameConfig {
  const w = width || parent.clientWidth || window.innerWidth - 380
  const h = height || parent.clientHeight || window.innerHeight - 48
  const skyHex = isLocalDaytime() ? '#a8d4ec' : '#31453a'
  return {
    type: Phaser.CANVAS,
    parent,
    width: w,
    height: h,
    pixelArt: true,
    backgroundColor: skyHex,
    physics: {
      default: 'arcade',
      arcade: {
        gravity: { x: 0, y: 0 },
        debug: false,
      },
    },
    scale: {
      mode: Phaser.Scale.RESIZE,
      autoCenter: Phaser.Scale.NONE,
      parent,
    },
    render: {
      antialias: false,
      pixelArt: true,
    },
  }
}
