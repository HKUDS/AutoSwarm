import dagre from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'

/**
 * Compute dagre layout and return nodes with updated x/y.
 * Top-down orientation (TB): root "owner" at top, leaves at bottom.
 */
export function computeDagreLayout(
  nodes: Node[],
  edges: Edge[],
  opts: { nodeWidth?: number; nodeHeight?: number } = {},
): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: 'TB',
    ranksep: 60,
    nodesep: 36,
    marginx: 24,
    marginy: 24,
  })
  const W = opts.nodeWidth ?? 220
  const H = opts.nodeHeight ?? 80
  nodes.forEach(n => g.setNode(n.id, { width: W, height: H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map(n => {
    const { x, y } = g.node(n.id)
    return { ...n, position: { x: x - W / 2, y: y - H / 2 } }
  })
}
