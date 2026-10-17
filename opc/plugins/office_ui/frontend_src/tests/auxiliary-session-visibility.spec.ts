import assert from 'node:assert/strict'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const server = await createServer({
  root: resolve(dirname(fileURLToPath(import.meta.url)), '..'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
})
let browser
try {
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing Vite address')
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors: string[] = []
  const requests: Record<string, any>[] = []
  let socket: { send: (message: string) => void } | undefined
  let hidden = false
  page.on('pageerror', error => errors.push(error.message))
  const rows = [
    { task_id: 'visible-root', session_id: 'root-session', title: 'Visible delivery', preferred_agent: 'codex', selected_execution_agent: 'codex' },
    { task_id: 'stale-assessment', session_id: 'aux-session', title: 'Stale internal assessment', preferred_agent: 'native', selected_execution_agent: 'native' },
  ]
  const baseline = (extra = {}) => {
    const visible = rows.filter(row => !hidden || row.task_id !== 'stale-assessment')
    return {
      ok: true, action: 'collab_sync', project_id: 'default', messages: [],
      channels: visible.map(row => ({ channel_id: `session:${row.task_id}`, name: row.title, type: 'session', project_id: 'default' })),
      boards: [{ board_id: 'default', name: 'Default' }],
      columns: [{ column_id: 'todo', board_id: 'default', name: 'Todo', order: 0 }], tasks: [],
      sessions: visible.map(row => ({ ...row, project_id: 'default', channel_id: `session:${row.task_id}`,
        mode: 'primary', exec_mode: 'company', company_profile: 'corporate', status: 'done', message_count: 0 })),
      ...extra,
    }
  }
  const send = (type: string, payload: Record<string, unknown>) => socket!.send(JSON.stringify({ type, payload }))
  await page.routeWebSocket('**/ws', ws => {
    socket = ws
    ws.onMessage(raw => {
      const data = JSON.parse(String(raw))
      requests.push(data)
      if (data.type === 'list_projects') {
        send('ack', { ok: true, action: data.type, projects: [{ id: 'default', name: 'Default' }], active_project_id: 'default' })
      } else if (data.type === 'collab_sync') {
        send('ack', baseline({ view_generation: data.view_generation, switch_seq: data.switch_seq }))
      } else if (data.type === 'switch_project') {
        send('project_switched', { project_id: 'default', switch_seq: data.switch_seq })
        send('collab_sync_push', baseline({ switch_seq: data.switch_seq }))
      } else if (data.type === 'session_detail') {
        send('ack', { ok: true, action: data.type, project_id: 'default', task_id: data.task_id,
          view_generation: data.view_generation, detail_level: data.detail_level,
          messages: [], message_count: 0, has_more: false,
          ...(hidden && data.task_id === 'stale-assessment' ? { session_visible: false } : {}) })
      } else if (data.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong' }))
      }
    })
  })
  await page.goto(`http://127.0.0.1:${address.port}/`)
  await page.getByRole('button', { name: 'Workspace', exact: true }).click()
  try {
    await page.getByText('Stale internal assessment', { exact: true }).first().waitFor()
    hidden = true
    send('ack', { ok: true, action: 'session_detail', project_id: 'default', task_id: 'stale-assessment',
      detail_level: 'full', session_visible: false, messages: [], message_count: 0, has_more: false })
    await page.waitForFunction(() => !document.body.textContent?.includes('Stale internal assessment'))
    assert.ok(await page.getByText('Visible delivery', { exact: true }).count() > 0)
    assert.deepEqual(errors, [])
  } catch (error) {
    console.error({ requests: requests.map(request => request.type), errors, pageText: (await page.locator('body').innerText()).slice(0, 2000) })
    throw error
  }
  console.log('Full App browser regression passed: hidden-session ACK retires stale internal chat; normal delivery stays visible.')
} finally {
  await browser?.close()
  await server.close()
}
