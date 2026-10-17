import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const evidence = process.env.NATIVE_MARKDOWN_EVIDENCE_JSON
const server = await createServer({
  root: resolve(dirname(fileURLToPath(import.meta.url)), '..'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'native-markdown-evidence', configureServer(vite) {
    vite.middlewares.use('/native-markdown-evidence.json', (_request, response) => {
      response.setHeader('Content-Type', 'application/json')
      response.end(evidence ? readFileSync(evidence, 'utf8') : '[]')
    })
  } }],
})
let browser
try {
  await server.listen()
  const address = server.httpServer!.address()
  if (!address || typeof address === 'string') throw new Error('Missing Vite address')
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`http://127.0.0.1:${address.port}/tests/native-markdown.html`)
  await page.locator('.msg-project-update-card').first().waitFor()
  for (const width of [1440, 723]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const title of await page.locator('.msg-project-update-title').allTextContents()) {
      assert.match(title, /^(Report|Review) #1:/)
      assert.equal(title.includes('\n'), false, 'report narrative must not become a plain text title')
      assert.equal(title.includes('<think>'), false)
    }
    for (const details of await page.locator('.msg-thinking').all()) {
      assert.equal(await details.getAttribute('open'), null)
      await details.locator('summary').evaluate(element => (element as HTMLElement).click())
      assert.equal(await details.locator('p, ul, ol').count() > 0, true)
      assert.equal(await details.locator('.msg-thinking-content').evaluate(element => {
        const parent = element.closest('.msg-list')!.getBoundingClientRect()
        const rect = element.getBoundingClientRect()
        return rect.left >= parent.left && rect.right <= parent.right
      }), true, 'expanded reasoning must fit the message column')
      await details.locator('summary').evaluate(element => (element as HTMLElement).click())
    }
    for (const toggle of await page.locator('button.msg-collapse-toggle').all()) {
      if ((await toggle.textContent())?.includes('Show more')) {
        await toggle.evaluate(element => (element as HTMLElement).click())
      }
    }
    assert.ok(await page.locator('.msg-project-update-summary h2').count() > 0)
    assert.ok(await page.locator('.msg-project-update-summary table').count() > 0)
    assert.ok(await page.locator('.msg-project-update-summary p').count() > 1)
    await page.screenshot({ path: `/tmp/openopc-native-markdown-${width}.png`, fullPage: true })
  }
  await page.reload()
  await page.locator('.msg-thinking').first().waitFor()
  assert.equal(await page.locator('.msg-thinking[open]').count(), 0)
  assert.deepEqual(errors, [])
  console.log(`Native Markdown browser regression passed (${evidence ? 'actual historical project05 messages' : 'fixture'}, desktop/mobile/reload).`)
} finally {
  await browser?.close()
  await server.close()
}
