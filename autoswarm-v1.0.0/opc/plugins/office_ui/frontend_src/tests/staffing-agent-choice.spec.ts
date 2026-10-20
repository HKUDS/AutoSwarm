import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const evidenceFile = process.env.STAFFING_EVIDENCE_JSON
const server = await createServer({
  root, logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{
    name: 'staffing-evidence-fixture',
    configureServer(server) {
      server.middlewares.use('/staffing-evidence.json', (_request, response) => {
        response.setHeader('Content-Type', 'application/json')
        response.end(evidenceFile ? readFileSync(evidenceFile) : '{}')
      })
    },
  }],
})
let browser
try {
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing Vite address')
  browser = await chromium.launch()
  const page = await browser.newPage()
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const base = `http://127.0.0.1:${address.port}/tests/staffing-agent-choice.html`
  for (const recruitment of [false, true]) {
    await page.goto(base + (recruitment ? '?recruitment' : ''))
    const prefix = recruitment ? 'recruit-agent' : 'staffing-agent'
    const select = (role: string) => page.locator(`#${prefix}-choice-regression-${role}`)
    await select('cto').waitFor()
    assert.equal(await select('cto').isEnabled(), true, 'configured boundary must remain editable')
    assert.equal(await select('engineer').isDisabled(), true, 'active Team owns descendants')
    await select('cto').selectOption('native')
    assert.equal(await select('engineer').isEnabled(), true)
    assert.equal(await select('engineer').inputValue(), 'codex', 'restore dormant individual choice')
    await select('engineer').selectOption('jiuwen')
    await select('cto').selectOption('jiuwenswarm')
    assert.equal(await select('engineer').isDisabled(), true)
    await select('cto').selectOption('native')
    assert.equal(await select('engineer').inputValue(), 'jiuwen')
    await page.getByRole('button', { name: recruitment ? 'Approve' : 'Approve Selections', exact: true }).click()
    const reply = JSON.parse(await page.locator('#submitted').innerText())
    assert.equal(reply.recruitment_role_agents.cto, 'native')
    assert.equal(reply.recruitment_role_agents.engineer, 'jiuwen')
    assert.equal(await select('cto').isDisabled(), true, 'submitted decisions remain immutable')
  }
  if (evidenceFile) {
    await page.goto(base + '?evidence')
    await page.locator('.ckpt-staffing-card').first().waitFor()
    assert.equal(await page.locator('.ckpt-staffing-card').count(), 11)
    for (const role of ['cto', 'cmo', 'coo']) {
      const select = page.locator(`select[id$="-${role}"]`)
      assert.equal(await select.isEnabled(), true)
      await select.selectOption('native')
    }
    assert.equal(await page.locator('.ckpt-agent-picker select:disabled').count(), 0)
    assert.equal(await page.locator('.ckpt-staffing-search').count(), 11)
    await page.getByRole('button', { name: 'Approve Selections', exact: true }).click()
    const reply = JSON.parse(await page.locator('#submitted').innerText())
    for (const role of ['cto', 'cmo', 'coo']) assert.equal(reply.recruitment_role_agents[role], 'native')
    console.log('Actual test0001 pending checkpoint: 11 roles restored, all selectors enabled')
  }
  assert.deepEqual(errors, [])
  console.log('staffing-agent-choice.spec.ts: OK (manual and recruitment switching, coverage, submitted choices)')
} finally {
  await browser?.close()
  await server.close()
}
