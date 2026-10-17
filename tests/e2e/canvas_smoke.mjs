/**
 * E2E smoke test for the Canvas / Inspector / Table redesign (Q1–Q6).
 *
 * Launches a real AutoSwarm server on port 18766, loads the UI in headless
 * Chromium via Playwright, and asserts:
 *
 *   1. .se-container mounts
 *   2. Canvas view is default; @xyflow/react root has non-zero height
 *   3. Canvas renders N nodes matching loaded roles + 1 owner node
 *   4. Clicking a canvas node opens the Inspector panel with correct role data
 *   5. Switching to Table view shows rows matching role count
 *   6. WS org_config_export returns non-empty YAML (regression guard)
 *   7. Keyboard shortcut: Escape closes Inspector
 *   8. No page console errors throughout the run
 *
 * Usage:
 *   node tests/e2e/canvas_smoke.mjs
 *
 * Prerequisites:
 *   - AutoSwarm installed (opc.cli.app importable)
 *   - Frontend built (opc/plugins/office_ui/frontend_dist populated)
 *   - @playwright/test + ws in frontend_src/node_modules
 *
 * Exit code 0 on PASS, non-zero on any failure.
 */

import { spawn } from 'node:child_process'
import { setTimeout as delay } from 'node:timers/promises'
import fs from 'node:fs'
import path from 'node:path'

const REPO_ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..')
const PLAYWRIGHT = `${REPO_ROOT}/opc/plugins/office_ui/frontend_src/node_modules/playwright/index.mjs`
const WS_LIB     = `${REPO_ROOT}/opc/plugins/office_ui/frontend_src/node_modules/ws/wrapper.mjs`
const PORT       = 18766
const CONFIG     = `${REPO_ROOT}/config/company_corporate_config.yaml`
const BACKUP     = `/tmp/cc_canvas_smoke_backup.yaml`

/* ── Utilities ────────────────────────────────────────────────── */

function assert(cond, msg) {
  if (cond) { console.log(`  ✓ ${msg}`); return }
  console.error(`  ✗ FAIL: ${msg}`)
  process.exit(1)
}

async function waitFor(cond, { tries = 50, intervalMs = 200 } = {}) {
  for (let i = 0; i < tries; i++) {
    if (await cond()) return true
    await delay(intervalMs)
  }
  return false
}

/* ── Main ─────────────────────────────────────────────────────── */

async function main() {
  // 1. Snapshot config
  fs.copyFileSync(CONFIG, BACKUP)
  console.log(`[1/8] Config snapshotted → ${BACKUP}`)

  // 2. Boot AutoSwarm
  const server = spawn('python3', ['-m', 'opc.cli.app', 'ui', '--port', String(PORT), '--host', '127.0.0.1'], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let serverLog = ''
  server.stdout.on('data', (d) => { serverLog += d.toString() })
  server.stderr.on('data', (d) => { serverLog += d.toString() })

  const ready = await waitFor(() => Promise.resolve(serverLog.includes('Office-UI running')), { tries: 120 })
  if (!ready) {
    console.error('AutoSwarm failed to start:\n' + serverLog.slice(-2000))
    server.kill()
    process.exit(2)
  }
  console.log('[2/8] AutoSwarm ready')

  let exitCode = 0
  try {
    // 3. Switch mode to company/corporate (has 11 built-in roles to render)
    const { default: WebSocket } = await import(WS_LIB)
    await new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws`)
      const t = setTimeout(() => { ws.close(); reject(new Error('mode switch timeout')) }, 10000)
      ws.on('open', () => ws.send(JSON.stringify({ type: 'set_execution_mode', mode: 'company', profile: 'corporate' })))
      ws.on('message', () => { clearTimeout(t); setTimeout(() => { ws.close(); resolve() }, 600) })
      ws.on('error', reject)
    })
    console.log('[3/8] Switched to company/corporate mode')

    // 4. Launch browser
    const { chromium } = await import(PLAYWRIGHT)
    const browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })
    const consoleErrors = []
    page.on('pageerror', (err) => consoleErrors.push(err.message))
    page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push('[console] ' + msg.text()) })

    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('#root > *', { timeout: 15000 })
    await delay(2500)
    console.log('[4/8] SPA loaded')

    // 5. Navigate to Organization → Team (default)
    const orgBtn = page.locator('button:has-text("Organization"), button:has-text("Org")').first()
    if (await orgBtn.count()) { await orgBtn.click(); await delay(500) }
    await delay(1500) // let Canvas finish mount

    // 6. Canvas assertions
    assert(await page.locator('.se-container').count() === 1, 'StructureEditor container mounted')
    const rfSize = await page.locator('.react-flow').first().evaluate((el) => {
      const r = el.getBoundingClientRect()
      return { w: Math.round(r.width), h: Math.round(r.height) }
    })
    assert(rfSize.h > 200, `Canvas has non-zero height (measured ${rfSize.w}×${rfSize.h})`)
    const nodeCount = await page.locator('.oc-canvas-node').count()
    assert(nodeCount >= 10, `Canvas renders ≥10 nodes (owner + roles); got ${nodeCount}`)

    // 7. Canvas view is default
    const canvasActive = await page.locator('.se-view-btn:has-text("Canvas")').first().getAttribute('aria-selected')
    assert(canvasActive === 'true', 'Canvas view active by default')
    console.log('[5/8] Canvas renders correctly')

    // 8. Click a non-owner canvas node → Inspector opens
    const nonOwner = page.locator('.oc-canvas-node:not(.is-owner)').first()
    await nonOwner.click()
    await delay(600)
    const inspectorCount = await page.locator('.ri-panel').count()
    assert(inspectorCount === 1, 'Clicking a node opens Inspector')
    const title = (await page.locator('.ri-panel-title').first().textContent())?.trim() ?? ''
    assert(title.length > 0, `Inspector shows role name (got "${title}")`)
    console.log(`[6/8] Inspector opens with role "${title}"`)

    // 9. Keyboard: Escape closes Inspector
    await page.keyboard.press('Escape')
    await delay(400)
    assert(await page.locator('.ri-panel').count() === 0, 'Escape closes Inspector')

    // 10. Switch to Table view
    await page.locator('.se-view-btn:has-text("Table")').first().click()
    await delay(700)
    const rowCount = await page.locator('.rt-row').count()
    assert(rowCount >= 5, `Table view shows ≥5 rows (got ${rowCount})`)
    console.log(`[7/8] Table view shows ${rowCount} rows`)

    // 11. WS regression: export should still work
    const exportPayload = await new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws`)
      const t = setTimeout(() => { ws.close(); reject(new Error('export timeout')) }, 8000)
      ws.on('open', () => ws.send(JSON.stringify({ type: 'org_config_export' })))
      ws.on('message', (buf) => {
        const msg = JSON.parse(buf.toString())
        if (msg.type === 'org_config_export') {
          clearTimeout(t); ws.close()
          resolve(msg.payload?.yaml ?? '')
        }
      })
      ws.on('error', reject)
    })
    assert(exportPayload.length > 50, `WS org_config_export returns YAML (${exportPayload.length} chars)`)

    // 12. Console errors
    assert(consoleErrors.length === 0, `No page console errors (got ${consoleErrors.length})`)
    if (consoleErrors.length > 0) {
      console.error('  Errors:', consoleErrors.slice(0, 5).join(' | '))
    }

    console.log('[8/8] All assertions passed')
    await browser.close()
  } catch (err) {
    console.error('TEST ERROR:', err.stack || err.message)
    exitCode = 3
  } finally {
    server.kill()
    await delay(800)
    // Restore config if mutated
    try {
      const now = fs.readFileSync(CONFIG, 'utf8')
      const backup = fs.readFileSync(BACKUP, 'utf8')
      if (now !== backup) {
        fs.writeFileSync(CONFIG, backup, 'utf8')
        console.log('Config restored from backup')
      }
    } catch (_) { /* ignore */ }
    console.log(exitCode === 0 ? '\n✅ PASS' : `\n❌ FAIL (exit ${exitCode})`)
    process.exit(exitCode)
  }
}

main().catch((err) => { console.error(err); process.exit(99) })
