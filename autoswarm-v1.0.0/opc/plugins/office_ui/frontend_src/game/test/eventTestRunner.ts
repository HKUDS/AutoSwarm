/**
 * Agent event test runner — multi-office edition.
 * Attach to window so it can be called from browser console:
 *   window.__runEventTests()
 */
import type { GameBridge } from '../GameBridge'
import type { VisualEvent } from '../../types/visual'
import { ZONES } from '../map/InteractionZones'
import { getOffices } from '../map/OfficeStore'

let _eventId = 0
function makeEvent(type: string, agentId: string, data: Record<string, unknown> = {}): VisualEvent {
  return {
    event_id: `test-${++_eventId}`,
    type,
    agent_id: agentId,
    data,
    timestamp: Date.now() / 1000,
  }
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms))
}

interface TestResult {
  name: string
  pass: boolean
  detail: string
}

export async function runAllTests(bridge: GameBridge): Promise<TestResult[]> {
  const results: TestResult[] = []
  const scene = bridge.getScene()
  if (!scene) {
    results.push({ name: 'scene-ready', pass: false, detail: 'OfficeScene not initialized' })
    return results
  }

  console.log('%c[EventTest] Starting full event test suite (multi-office)...', 'color: #6366f1; font-weight: bold')

  for (const id of [...scene.agents.keys()]) scene.removeAgent(id)
  await sleep(100)

  const offices = getOffices()

  // ── Test 1: Office data ──
  results.push({
    name: 'offices-loaded',
    pass: offices.length >= 3,
    detail: `${offices.length} offices loaded: ${offices.map(o => o.name).join(', ')}`,
  })

  // ── Test 2: Agents assigned to different offices ──
  const agentIds = ['agent-A', 'agent-B', 'agent-C', 'agent-D', 'agent-E', 'agent-F']
  for (const id of agentIds) {
    bridge.pushEvent(makeEvent('agent_active', id))
  }
  await sleep(300)

  const assignedSeats = new Set<string>()
  let deskOverlap = false
  for (const id of agentIds) {
    const agent = scene.getAgent(id)
    if (!agent) continue
    if (agent.seatId) {
      if (assignedSeats.has(agent.seatId)) deskOverlap = true
      assignedSeats.add(agent.seatId)
    }
  }
  results.push({
    name: 'desk-assignment-unique',
    pass: !deskOverlap && assignedSeats.size === agentIds.length,
    detail: `Assigned ${assignedSeats.size} unique seats to ${agentIds.length} agents. IDs: [${[...assignedSeats].join(', ')}]`,
  })

  // ── Test 3: All agents have officeId set ──
  const allHaveOffice = agentIds.every(id => {
    const agent = scene.getAgent(id)
    return agent?.officeId != null
  })
  results.push({
    name: 'agents-have-officeId',
    pass: allHaveOffice,
    detail: agentIds.map(id => `${id}=${scene.getAgent(id)?.officeId}`).join(', '),
  })

  // ── Test 4: tool_start → active ──
  bridge.pushEvent(makeEvent('tool_start', 'agent-A', { tool_name: 'shell' }))
  await sleep(200)
  const agA = scene.getAgent('agent-A')!
  results.push({
    name: 'tool_start-state',
    pass: agA.isActive === true && agA.currentTool === 'shell',
    detail: `isActive=${agA.isActive}, currentTool=${agA.currentTool}`,
  })

  // ── Test 5: tool_done → celebrate ──
  bridge.pushEvent(makeEvent('tool_done', 'agent-A', { tool_name: 'shell' }))
  await sleep(100)
  results.push({
    name: 'tool_done-celebrate',
    pass: agA.agentState === 'celebrate',
    detail: `state=${agA.agentState}`,
  })

  for (let i = 0; i < 20; i++) {
    if (agA.agentState !== 'celebrate') break
    await sleep(500)
  }
  results.push({
    name: 'celebrate-to-idle',
    pass: agA.agentState !== 'celebrate',
    detail: `state=${agA.agentState}`,
  })

  // ── Test 6: waiting → break room ──
  bridge.pushEvent(makeEvent('waiting', 'agent-B'))
  await sleep(200)
  const agB = scene.getAgent('agent-B')!
  results.push({
    name: 'waiting-state',
    pass: agB.isActive === false && (agB.agentState === 'walk' || agB.agentState === 'idle' || agB.agentState === 'coffee'),
    detail: `isActive=${agB.isActive}, state=${agB.agentState}`,
  })

  // ── Test 7: reflect_start → meeting room ──
  bridge.pushEvent(makeEvent('reflect_start', 'agent-C'))
  await sleep(200)
  const agC = scene.getAgent('agent-C')!
  results.push({
    name: 'reflect_start-state',
    pass: agC.currentTool === 'Reflect' && (agC.agentState === 'walk' || agC.agentState === 'reflect'),
    detail: `currentTool=${agC.currentTool}, state=${agC.agentState}`,
  })

  bridge.pushEvent(makeEvent('reflect_done', 'agent-C'))
  await sleep(100)
  results.push({
    name: 'reflect_done-celebrate',
    pass: agC.agentState === 'celebrate',
    detail: `state=${agC.agentState}`,
  })

  // ── Test 8: collab in meeting room (same office) ──
  const collabAgents = ['agent-A', 'agent-B', 'agent-C', 'agent-D']
  for (const id of collabAgents) {
    bridge.pushEvent(makeEvent('collab_started', id))
  }
  await sleep(4000)

  const meetingPositions = new Map<string, string>()
  let meetingOverlap = false
  for (const id of collabAgents) {
    const agent = scene.getAgent(id)
    if (!agent) continue
    const pos = agent.getTilePos()
    const key = `${pos.x},${pos.y}`
    if (meetingPositions.has(key)) meetingOverlap = true
    meetingPositions.set(key, id)
  }
  results.push({
    name: 'collab-no-overlap',
    pass: !meetingOverlap,
    detail: `${collabAgents.length} agents, ${meetingPositions.size} unique positions`,
  })

  for (const id of collabAgents) bridge.pushEvent(makeEvent('collab_ended', id))
  await sleep(100)
  const allCelebrate = collabAgents.every(id => scene.getAgent(id)?.agentState === 'celebrate')
  results.push({
    name: 'collab_ended-celebrate',
    pass: allCelebrate,
    detail: collabAgents.map(id => `${id}=${scene.getAgent(id)?.agentState}`).join(', '),
  })
  await sleep(3000)

  // ── Test 9: practice ──
  bridge.pushEvent(makeEvent('practice_start', 'agent-E', { target_domain: 'TypeScript' }))
  await sleep(200)
  const agE = scene.getAgent('agent-E')!
  results.push({
    name: 'practice_start-state',
    pass: agE.currentTool === 'Practice' && (agE.agentState === 'walk' || agE.agentState === 'practice'),
    detail: `currentTool=${agE.currentTool}, state=${agE.agentState}`,
  })

  bridge.pushEvent(makeEvent('practice_done', 'agent-E'))
  await sleep(100)
  results.push({
    name: 'practice_done-celebrate',
    pass: agE.agentState === 'celebrate',
    detail: `state=${agE.agentState}`,
  })
  await sleep(3000)

  // ── Test 10: task_delegated ──
  bridge.pushEvent(makeEvent('task_delegated', 'agent-A', { target: 'agent-B' }))
  await sleep(100)
  results.push({
    name: 'task_delegated-chat',
    pass: agA.agentState === 'chat',
    detail: `state=${agA.agentState}`,
  })

  bridge.pushEvent(makeEvent('delegation_done', 'agent-A', { target: 'agent-B' }))
  await sleep(300)
  results.push({
    name: 'delegation_done-return',
    pass: ['idle', 'walk', 'type'].includes(agA.agentState),
    detail: `state=${agA.agentState}`,
  })

  // ── Test 11: message ──
  const agF = scene.getAgent('agent-F')!
  bridge.pushEvent(makeEvent('message_in', 'agent-F', { content_preview: 'Hello test' }))
  await sleep(200)
  results.push({
    name: 'message_in-active',
    pass: agF.isActive === true,
    detail: `isActive=${agF.isActive}, state=${agF.agentState}`,
  })

  bridge.pushEvent(makeEvent('message_out', 'agent-F', { content_preview: 'Reply test' }))
  await sleep(100)
  results.push({
    name: 'message_out-bubble',
    pass: agF.bubbleText?.includes('Reply') === true,
    detail: `bubble="${agF.bubbleText}"`,
  })

  // ── Test 12: subagent ──
  bridge.pushEvent(makeEvent('subagent_spawn', 'subagent-test-1', { parent_agent_id: 'agent-A' }))
  await sleep(200)
  const sub1 = scene.getAgent('subagent-test-1')
  results.push({
    name: 'subagent_spawn-created',
    pass: sub1 != null && sub1.isSubagent === true,
    detail: `created=${!!sub1}, isSubagent=${sub1?.isSubagent}`,
  })

  bridge.pushEvent(makeEvent('subagent_done', 'subagent-test-1'))
  await sleep(5000)
  results.push({
    name: 'subagent_done-removed',
    pass: scene.getAgent('subagent-test-1') == null,
    detail: `still exists=${!!scene.getAgent('subagent-test-1')}`,
  })

  // ── Test 13: task_routed ──
  bridge.pushEvent(makeEvent('task_routed', 'agent-E', { method: 'auto' }))
  await sleep(200)
  results.push({
    name: 'task_routed-active',
    pass: agE.isActive === true,
    detail: `isActive=${agE.isActive}, state=${agE.agentState}`,
  })

  // ── Test 14: agent_removed ──
  bridge.pushEvent(makeEvent('agent_removed', 'agent-F'))
  await sleep(5000)
  results.push({
    name: 'agent_removed-destroyed',
    pass: scene.getAgent('agent-F') == null,
    detail: `still exists=${!!scene.getAgent('agent-F')}`,
  })

  // ── Test 15: crystallize ──
  bridge.pushEvent(makeEvent('mycelium_crystallize', 'agent-A', {
    corroborating_agents: ['agent-B', 'agent-C'],
    content_preview: 'Shared knowledge',
  }))
  await sleep(4000)

  const crystalPositions = new Map<string, string>()
  let crystalOverlap = false
  for (const id of ['agent-A', 'agent-B', 'agent-C']) {
    const agent = scene.getAgent(id)
    if (!agent) continue
    const pos = agent.getTilePos()
    const key = `${pos.x},${pos.y}`
    if (crystalPositions.has(key)) crystalOverlap = true
    crystalPositions.set(key, id)
  }
  results.push({
    name: 'crystal-no-overlap',
    pass: !crystalOverlap,
    detail: `3 agents, ${crystalPositions.size} unique positions`,
  })

  // ── Test 16: desk seat uniqueness ──
  const deskSeatMap = new Map<string, string>()
  let deskConflict = false
  for (const agent of scene.agents.values()) {
    if (!agent.seatId) continue
    if (deskSeatMap.has(agent.seatId)) deskConflict = true
    deskSeatMap.set(agent.seatId, agent.agentId)
  }
  results.push({
    name: 'desk-seat-no-conflict',
    pass: !deskConflict,
    detail: `${deskSeatMap.size} desk seats assigned, conflict=${deskConflict}`,
  })

  // ── Test 17: reassign agent across offices ──
  const agD = scene.getAgent('agent-D')
  if (agD) {
    const oldOfficeId = agD.officeId
    const targetOffice = offices.find(o => o.id !== oldOfficeId) ?? offices[1]
    bridge.assignAgentToOffice('agent-D', targetOffice.id)
    await sleep(300)
    const newOffice = agD.officeId
    results.push({
      name: 'reassign-office',
      pass: newOffice === targetOffice.id && newOffice !== oldOfficeId,
      detail: `${oldOfficeId} → ${newOffice} (expected ${targetOffice.id})`,
    })
  } else {
    results.push({ name: 'reassign-office', pass: false, detail: 'agent-D not found' })
  }

  // ── Test 18: cross-office seat uniqueness after reassign ──
  const seatMap2 = new Map<string, string>()
  let conflict2 = false
  for (const agent of scene.agents.values()) {
    if (!agent.seatId) continue
    if (seatMap2.has(agent.seatId)) conflict2 = true
    seatMap2.set(agent.seatId, agent.agentId)
  }
  results.push({
    name: 'cross-office-seat-unique',
    pass: !conflict2,
    detail: `${seatMap2.size} seats, conflict=${conflict2}`,
  })

  // ── Summary ──
  const passed = results.filter(r => r.pass).length
  const failed = results.filter(r => !r.pass).length

  console.log('')
  console.log('%c[EventTest] ═══════════════════════════════════════', 'color: #6366f1; font-weight: bold')
  console.log(`%c[EventTest] Results: ${passed} passed, ${failed} failed, ${results.length} total`, `color: ${failed > 0 ? '#f87171' : '#34d399'}; font-weight: bold`)
  console.log('%c[EventTest] ═══════════════════════════════════════', 'color: #6366f1; font-weight: bold')

  for (const r of results) {
    const icon = r.pass ? '\u2705' : '\u274C'
    const color = r.pass ? 'color: #34d399' : 'color: #f87171; font-weight: bold'
    console.log(`%c  ${icon} ${r.name}: ${r.detail}`, color)
  }

  return results
}

export function registerTestRunner(bridge: GameBridge) {
  (window as any).__runEventTests = () => runAllTests(bridge)
  ;(window as any).__bridge = bridge
  console.log(
    '%c[EventTest] Test runner ready. Run: window.__runEventTests()',
    'color: #fbbf24; font-weight: bold',
  )
}
