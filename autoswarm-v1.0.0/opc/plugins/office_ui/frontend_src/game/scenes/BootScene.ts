import Phaser from 'phaser'

const CHAR_FRAME_W = 16
const CHAR_FRAME_H = 32
const CHAR_COUNT = 6

export class BootScene extends Phaser.Scene {
  constructor() {
    super('Boot')
  }

  preload() {
    this.load.image('office-bg', 'assets/office-bg.png')
    this.load.spritesheet('office-tileset-32', 'assets/office-tileset-32.png', {
      frameWidth: 32,
      frameHeight: 32,
    })

    for (let i = 0; i < CHAR_COUNT; i++) {
      this.load.spritesheet(`char_${i}`, `assets/characters/char_${i}.png`, {
        frameWidth: CHAR_FRAME_W,
        frameHeight: CHAR_FRAME_H,
      })
    }

    const bar = this.add.graphics()
    this.load.on('progress', (v: number) => {
      bar.clear()
      bar.fillStyle(0x4a90d9, 1)
      bar.fillRect(this.scale.width / 2 - 100, this.scale.height / 2 - 8, 200 * v, 16)
    })
    this.load.on('complete', () => bar.destroy())
  }

  create() {
    this.createCharacterAnimations()
    console.log('[BootScene] All assets loaded')
    this.scene.start('Office')
  }

  private createCharacterAnimations() {
    const COLS = 7
    const dirs = [
      { name: 'down', row: 0 },
      { name: 'up', row: 1 },
      { name: 'right', row: 2 },
      { name: 'left', row: 2 },
    ]
    for (let p = 0; p < CHAR_COUNT; p++) {
      const key = `char_${p}`
      for (const dir of dirs) {
        const base = dir.row * COLS
        this.anims.create({ key: `${key}_walk_${dir.name}`, frames: [{ key, frame: base }, { key, frame: base + 1 }, { key, frame: base + 2 }, { key, frame: base + 1 }], frameRate: 8, repeat: -1 })
        this.anims.create({ key: `${key}_idle_${dir.name}`, frames: [{ key, frame: base + 1 }], frameRate: 1 })
        this.anims.create({ key: `${key}_type_${dir.name}`, frames: [{ key, frame: base + 3 }, { key, frame: base + 4 }], frameRate: 3, repeat: -1 })
        this.anims.create({ key: `${key}_read_${dir.name}`, frames: [{ key, frame: base + 5 }, { key, frame: base + 6 }], frameRate: 2, repeat: -1 })
        this.anims.create({ key: `${key}_coffee_${dir.name}`, frames: [{ key, frame: base + 1 }, { key, frame: base }], frameRate: 2, repeat: -1 })
        this.anims.create({ key: `${key}_celebrate_${dir.name}`, frames: [{ key, frame: base }, { key, frame: base + 1 }, { key, frame: base + 2 }, { key, frame: base + 1 }], frameRate: 6, repeat: -1 })
      }
    }
  }
}
