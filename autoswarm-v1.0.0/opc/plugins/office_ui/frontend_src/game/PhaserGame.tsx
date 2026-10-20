import { useEffect, useRef } from 'react'
import Phaser from 'phaser'
import { createGameConfig } from './config'
import type { GameBridge } from './GameBridge'
import { BootScene } from './scenes/BootScene'
import { OfficeScene } from './scenes/OfficeScene'

interface Props {
  bridge: GameBridge
  /** False while the office page is hidden — puts the render loop to sleep.
      Bridge calls still apply synchronously to scene state, so nothing is
      lost; only the per-frame update/render work stops. */
  active?: boolean
}

export function PhaserGame({ bridge, active = true }: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const gameRef = useRef<Phaser.Game | null>(null)
  const activeRef = useRef(active)
  activeRef.current = active

  // display:none does NOT stop requestAnimationFrame — without this, the
  // whole game loop (physics, tweens, full canvas redraw) keeps burning CPU
  // while the user is on the Workspace/Org pages.
  useEffect(() => {
    const game = gameRef.current
    if (!game || !game.loop) return
    // TimeStep.running toggles false on sleep() while `started` stays true.
    if (active) {
      if (game.loop.started && !game.loop.running) {
        game.loop.wake()
        // Re-measure after display:none → visible; in RESIZE mode Phaser's
        // parent-size poll may still hold the stale hidden bounds.
        game.scale.getParentBounds()
        game.scale.refresh()
      }
    } else if (game.loop.running) {
      game.loop.sleep()
    }
  }, [active])

  useEffect(() => {
    if (!wrapperRef.current || !containerRef.current) return

    const wrapper = wrapperRef.current
    const container = containerRef.current

    const createGame = (w: number, h: number) => {
      console.log('[PhaserGame] Creating Phaser game', w, '×', h)
      const config = createGameConfig(container, w, h)
      config.scene = [BootScene, OfficeScene]
      const game = new Phaser.Game(config)
      game.registry.set('bridge', bridge)
      // The game is created lazily on first layout; if the page was switched
      // away before boot finished, park the loop immediately.
      game.events.once(Phaser.Core.Events.READY, () => {
        if (!activeRef.current && game.loop.running) game.loop.sleep()
      })
      gameRef.current = game
    }

    if (typeof ResizeObserver === 'undefined') {
      createGame(wrapper.clientWidth || window.innerWidth - 380, wrapper.clientHeight || window.innerHeight - 48)
      return () => {
        gameRef.current?.destroy(true)
        gameRef.current = null
      }
    }

    // The office page can start hidden (display:none → 0×0). Never create or
    // resize the game at zero size; wait for the first real layout instead.
    const observer = new ResizeObserver((entries) => {
      const rect = entries[entries.length - 1].contentRect
      const w = Math.floor(rect.width)
      const h = Math.floor(rect.height)
      if (w < 1 || h < 1) return // hidden — keep last known size
      if (!gameRef.current) {
        createGame(w, h)
      } else {
        // In RESIZE scale mode the game follows parentSize, which Phaser only
        // re-measures on its 500ms poll — and scale.resize() gets clobbered by
        // that stale value. Re-measure the parent, then refresh.
        const scale = gameRef.current.scale
        scale.getParentBounds()
        scale.refresh()
      }
    })
    observer.observe(wrapper)

    return () => {
      observer.disconnect()
      gameRef.current?.destroy(true)
      gameRef.current = null
    }
  }, [bridge]) // bridge is a stable ref, effect runs once

  return (
    // Wrapper fills the CSS grid cell
    <div ref={wrapperRef} style={{ width: '100%', height: '100%' }}>
      {/* Phaser mounts its canvas inside this div; it must track the wrapper
          so Phaser's own parent-bounds polling reads the true size. */}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  )
}
