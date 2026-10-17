import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const evidence = process.env.SESSION_AGENT_EVIDENCE_JSON
const rows = evidence ? JSON.parse(readFileSync(evidence, 'utf8')) : [
  { task_id: 'adaptive-codex', preferred_agent: 'codex' },
  { task_id: 'fixed-codex', preferred_agent: 'codex' },
  { task_id: 'explicit-native', preferred_agent: 'native' },
]
const server = await createServer({
  root: resolve(dirname(fileURLToPath(import.meta.url)), '..'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'agent-lock-evidence', configureServer(vite) {
    vite.middlewares.use('/agent-lock-evidence.json', (_request, response) => {
      response.setHeader('Content-Type', 'application/json')
      response.end(evidence ? readFileSync(evidence, 'utf8') : '{}')
    })
  } }],
})
let browser
try {
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing Vite address')
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const url = `http://127.0.0.1:${address.port}/tests/session-agent-lock.html`
  for (const coldLoad of [false, true]) {
    if (coldLoad) await page.reload()
    else await page.goto(url)
    await page.waitForFunction(() => !!window.__agentLockFixture)
    for (const row of rows) {
      await page.evaluate(taskId => window.__agentLockFixture!.select(taskId), row.task_id)
      const label = row.preferred_agent === 'native' ? 'OpenOPC Native' : 'Codex'
      const chip = page.locator('.composer-config-group [data-kind="agent"]')
      await page.waitForFunction(expected => document.querySelector('.composer-config-group [data-kind="agent"]')?.textContent?.includes(expected), label)
      assert.match(await chip.getAttribute('aria-label') ?? '', new RegExp(`Agent locked to ${label}`))
      for (let round = 0; round < 3; round++) {
        await page.evaluate(() => window.__agentLockFixture!.refresh())
        await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
        assert.match(await chip.getAttribute('aria-label') ?? '', new RegExp(`Agent locked to ${label}`))
      }
    }
    await page.evaluate(() => window.__agentLockFixture!.switchProject('other-project'))
    await page.getByLabel('Agent locked to OpenOPC Native.', { exact: false }).waitFor()
    await page.evaluate(() => window.__agentLockFixture!.switchProject('project02'))
    await page.getByLabel(`Agent locked to ${rows[0].preferred_agent === 'native' ? 'OpenOPC Native' : 'Codex'}.`, { exact: false }).waitFor()
  }
  assert.deepEqual(errors, [])
  console.log(`Agent lock browser regression passed: ${rows.length} sessions, repeated detail refreshes, project switches and cold reloads.`)
} finally {
  await browser?.close()
  await server.close()
}
