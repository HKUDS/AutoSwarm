import * as EasyStar from 'easystarjs'

export class PathfindingManager {
  private easystar: EasyStar.js
  private grid: number[][]
  private gridCols: number
  private gridRows: number

  constructor(collisionGrid: number[][]) {
    this.grid = collisionGrid.map(row => [...row])
    this.gridRows = this.grid.length
    this.gridCols = this.gridRows > 0 ? this.grid[0].length : 0
    this.easystar = new EasyStar.js()
    this.easystar.setGrid(this.grid)
    this.easystar.setAcceptableTiles([0])
    this.easystar.setIterationsPerCalculation(800)
  }

  findPath(
    from: { x: number; y: number },
    to: { x: number; y: number },
  ): Promise<{ x: number; y: number }[]> {
    return new Promise((resolve) => {
      if (
        from.x < 0 || from.x >= this.gridCols ||
        from.y < 0 || from.y >= this.gridRows ||
        to.x < 0 || to.x >= this.gridCols ||
        to.y < 0 || to.y >= this.gridRows
      ) {
        resolve([])
        return
      }

      if (this.grid[to.y]?.[to.x] === 1) {
        const alt = this.findNearestWalkable(to.x, to.y)
        if (!alt) { resolve([]); return }
        to = alt
      }

      if (this.grid[from.y]?.[from.x] === 1) {
        const alt = this.findNearestWalkable(from.x, from.y)
        if (!alt) { resolve([]); return }
        from = alt
      }

      this.easystar.findPath(from.x, from.y, to.x, to.y, (path) => {
        resolve(path ?? [])
      })
      this.easystar.calculate()
    })
  }

  blockTile(x: number, y: number) {
    if (y >= 0 && y < this.grid.length && x >= 0 && x < this.grid[0].length) {
      this.grid[y][x] = 1
      this.easystar.setGrid(this.grid)
    }
  }

  unblockTile(x: number, y: number) {
    if (y >= 0 && y < this.grid.length && x >= 0 && x < this.grid[0].length) {
      this.grid[y][x] = 0
      this.easystar.setGrid(this.grid)
    }
  }

  isWalkable(x: number, y: number): boolean {
    if (y < 0 || y >= this.grid.length || x < 0 || x < 0 || x >= this.grid[0].length) return false
    return this.grid[y][x] === 0
  }

  getWalkableTiles(): { x: number; y: number }[] {
    const tiles: { x: number; y: number }[] = []
    for (let r = 0; r < this.grid.length; r++) {
      for (let c = 0; c < this.grid[r].length; c++) {
        if (this.grid[r][c] === 0) {
          tiles.push({ x: c, y: r })
        }
      }
    }
    return tiles
  }

  private findNearestWalkable(x: number, y: number): { x: number; y: number } | null {
    for (let radius = 1; radius <= 5; radius++) {
      for (let dy = -radius; dy <= radius; dy++) {
        for (let dx = -radius; dx <= radius; dx++) {
          if (Math.abs(dx) !== radius && Math.abs(dy) !== radius) continue
          const nx = x + dx
          const ny = y + dy
          if (this.isWalkable(nx, ny)) return { x: nx, y: ny }
        }
      }
    }
    return null
  }
}
