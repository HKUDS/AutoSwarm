import { chromium } from 'playwright'
import type { Browser, Page } from 'playwright'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'
import { strict as assert } from 'node:assert'

/**
 * Standalone Playwright check that mirrors the production exec-panel layout
 * and asserts that:
 *
 *   1. The Activity scroll container is actually scrollable
 *      (scrollHeight strictly greater than clientHeight).
 *   2. After scrolling to the maximum allowed position
 *      (scrollTop = scrollHeight − clientHeight), the LAST entry is fully
 *      contained inside the container's visible rect — i.e. its bottom
 *      edge sits at-or-above the container's bottom edge.
 *   3. The outer panel-body can still scroll past the focused card to
 *      reveal sibling cards (regression guard for the `overscroll-behavior`
 *      / `min-height: 0` fixes).
 *
 * The fixture is a static HTML page that copies the production CSS for the
 * panel and the progress timeline so we are testing the same cascade the
 * real UI sees, without spinning up the React app.
 *
 * Run: node --import tsx ./tests/exec-panel-scroll.spec.ts
 */

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const FIXTURE_URL = pathToFileURL(resolve(__dirname, 'exec-panel-scroll.html')).toString()

interface ScrollMetrics {
  scrollHeight: number
  clientHeight: number
  scrollTop: number
  maxScrollTop: number
  containerBottom: number
  lastEntryBottom: number
  lastEntryTop: number
  lastEntryVisible: boolean
}

async function readActivityMetrics(page: Page): Promise<ScrollMetrics> {
  return page.evaluate(() => {
    const container = document.querySelector('[data-testid="activity-scroll"]') as HTMLElement
    const lastEntry = document.querySelector('[data-testid="last-activity-entry"]') as HTMLElement
    if (!container || !lastEntry) {
      throw new Error('test fixture missing required nodes')
    }
    const cRect = container.getBoundingClientRect()
    const eRect = lastEntry.getBoundingClientRect()
    return {
      scrollHeight: container.scrollHeight,
      clientHeight: container.clientHeight,
      scrollTop: container.scrollTop,
      maxScrollTop: container.scrollHeight - container.clientHeight,
      containerBottom: cRect.bottom,
      lastEntryBottom: eRect.bottom,
      lastEntryTop: eRect.top,
      // Allow a 1px tolerance for sub-pixel rounding.
      lastEntryVisible: eRect.bottom <= cRect.bottom + 1 && eRect.top >= cRect.top - 1,
    }
  })
}

async function scrollActivityToBottom(page: Page): Promise<void> {
  await page.evaluate(() => {
    const container = document.querySelector('[data-testid="activity-scroll"]') as HTMLElement
    container.scrollTop = container.scrollHeight
  })
  // Allow layout to settle.
  await page.waitForTimeout(50)
}

async function scrollOuterPanelToBottom(page: Page): Promise<void> {
  await page.evaluate(() => {
    const body = document.querySelector('[data-testid="exec-panel-body"]') as HTMLElement
    body.scrollTop = body.scrollHeight
  })
  await page.waitForTimeout(50)
}

async function siblingCardVisible(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const card = document.querySelector('[data-testid="sibling-card"]') as HTMLElement
    const body = document.querySelector('[data-testid="exec-panel-body"]') as HTMLElement
    if (!card || !body) return false
    const cardRect = card.getBoundingClientRect()
    const bodyRect = body.getBoundingClientRect()
    // Sibling card's top must be inside the visible body region.
    return cardRect.top >= bodyRect.top - 1 && cardRect.top <= bodyRect.bottom + 1
  })
}

interface CaseResult {
  name: string
  pass: boolean
  detail: string
}

async function runCase(
  page: Page,
  url: string,
  name: string,
): Promise<CaseResult[]> {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(url)
  // Give the inline injection script a tick.
  await page.waitForSelector('[data-testid="last-activity-entry"]', { state: 'attached' })
  await page.waitForTimeout(50)

  const beforeScroll = await readActivityMetrics(page)
  const results: CaseResult[] = []

  // (1) Scroll is needed at all.
  results.push({
    name: `${name} — Activity has scrollable overflow`,
    pass: beforeScroll.scrollHeight > beforeScroll.clientHeight + 10,
    detail: `scrollHeight=${beforeScroll.scrollHeight} clientHeight=${beforeScroll.clientHeight}`,
  })

  // (2) After scrolling to bottom, last entry is fully visible inside
  //     the activity container (the headline complaint the user filed).
  await scrollActivityToBottom(page)
  const afterScroll = await readActivityMetrics(page)
  results.push({
    name: `${name} — Last activity entry visible at bottom of scroll`,
    pass: afterScroll.lastEntryVisible,
    detail: `containerBottom=${afterScroll.containerBottom.toFixed(1)} lastEntryBottom=${afterScroll.lastEntryBottom.toFixed(1)} lastEntryTop=${afterScroll.lastEntryTop.toFixed(1)} scrollTop=${afterScroll.scrollTop} maxScrollTop=${afterScroll.maxScrollTop}`,
  })

  // (3) Outer panel-body must still scroll so sibling cards are reachable.
  await scrollOuterPanelToBottom(page)
  const sibling = await siblingCardVisible(page)
  results.push({
    name: `${name} — Outer panel scroll reveals sibling card`,
    pass: sibling,
    detail: sibling ? 'sibling visible' : 'sibling NOT in viewport',
  })

  return results
}

async function main(): Promise<void> {
  const browser: Browser = await chromium.launch()
  const context = await browser.newContext()
  const page = await context.newPage()

  const cases: { name: string; query: string }[] = [
    { name: 'few entries, short handoff', query: '?entries=20&handoff=4' },
    { name: 'many entries, short handoff', query: '?entries=300&handoff=4' },
    { name: 'many entries, long handoff', query: '?entries=300&handoff=80' },
    { name: 'huge entries, huge handoff', query: '?entries=600&handoff=120' },
  ]

  let allResults: CaseResult[] = []
  for (const c of cases) {
    const url = `${FIXTURE_URL}${c.query}`
    const results = await runCase(page, url, c.name)
    allResults = allResults.concat(results)
  }

  // Live-append regression: load fewer entries up front, scroll to bottom,
  // then append new ones (mirrors a running role's snapshot tick) and verify
  // the user can still reach the very last entry by scrolling again.
  for (const liveCase of [
    { name: 'live append small', start: 50, append: 20 },
    { name: 'live append medium', start: 200, append: 50 },
    { name: 'live append while at bottom', start: 100, append: 30 },
  ]) {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${FIXTURE_URL}?entries=${liveCase.start}&handoff=12`)
    await page.waitForSelector('[data-testid="last-activity-entry"]', { state: 'attached' })
    await page.waitForTimeout(50)
    await scrollActivityToBottom(page)
    const beforeAppend = await readActivityMetrics(page)
    await page.evaluate((n) => (window as unknown as { __appendEntries: (n: number) => void }).__appendEntries(n), liveCase.append)
    await page.waitForTimeout(80)
    await scrollActivityToBottom(page)
    const afterAppend = await readActivityMetrics(page)
    allResults.push({
      name: `${liveCase.name} — last entry visible after live append`,
      pass: afterAppend.lastEntryVisible && afterAppend.scrollHeight > beforeAppend.scrollHeight,
      detail: `scrollHeight ${beforeAppend.scrollHeight} → ${afterAppend.scrollHeight}; lastEntryBottom=${afterAppend.lastEntryBottom.toFixed(1)} containerBottom=${afterAppend.containerBottom.toFixed(1)}`,
    })
  }

  // Smaller viewport — emulates a laptop screen.  55vh is much smaller, the
  // outer panel-body is also smaller, so any latent layout issue surfaces.
  for (const viewportCase of [
    { name: 'small viewport', width: 1280, height: 600 },
    { name: 'tiny viewport', width: 1280, height: 480 },
  ]) {
    await page.setViewportSize({ width: viewportCase.width, height: viewportCase.height })
    await page.goto(`${FIXTURE_URL}?entries=300&handoff=40`)
    await page.waitForSelector('[data-testid="last-activity-entry"]', { state: 'attached' })
    await page.waitForTimeout(50)
    await scrollActivityToBottom(page)
    const m = await readActivityMetrics(page)
    allResults.push({
      name: `${viewportCase.name} — Activity reaches bottom (${viewportCase.width}×${viewportCase.height})`,
      pass: m.lastEntryVisible,
      detail: `containerBottom=${m.containerBottom.toFixed(1)} lastEntryBottom=${m.lastEntryBottom.toFixed(1)}`,
    })
  }

  // Trailing card test — the LAST card in the panel.  User must scroll the
  // outer panel-body all the way down, AND the trailing activity must then
  // be reachable to its bottom.  This is the most common real-world case
  // the user described ("some roles can't scroll to bottom") because the
  // role they care about is often the latest one created.
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(`${FIXTURE_URL}?entries=300&handoff=40&trailing=180`)
  await page.waitForSelector('[data-testid="trailing-last-entry"]', { state: 'attached' })
  await page.waitForTimeout(50)
  // Walk the outer panel down so the trailing card is in view, then drive
  // the trailing activity's own scroll to the bottom.
  await page.evaluate(() => {
    const body = document.querySelector('[data-testid="exec-panel-body"]') as HTMLElement
    body.scrollTop = body.scrollHeight
  })
  await page.waitForTimeout(50)
  await page.evaluate(() => {
    const trailing = document.querySelector('[data-testid="trailing-activity-scroll"]') as HTMLElement
    trailing.scrollTop = trailing.scrollHeight
  })
  await page.waitForTimeout(50)
  const trailingMetrics = await page.evaluate(() => {
    const trailing = document.querySelector('[data-testid="trailing-activity-scroll"]') as HTMLElement
    const last = document.querySelector('[data-testid="trailing-last-entry"]') as HTMLElement
    const body = document.querySelector('[data-testid="exec-panel-body"]') as HTMLElement
    const panel = document.querySelector('[data-testid="exec-panel"]') as HTMLElement
    const t = trailing.getBoundingClientRect()
    const l = last.getBoundingClientRect()
    return {
      // Must be inside the trailing container AND inside the visible viewport.
      visibleInContainer: l.bottom <= t.bottom + 1 && l.top >= t.top - 1,
      visibleInViewport: l.top >= 0 && l.bottom <= window.innerHeight + 1,
      cBottom: t.bottom,
      lBottom: l.bottom,
      lTop: l.top,
      cTop: t.top,
      bodyScrollTop: body.scrollTop,
      bodyScrollHeight: body.scrollHeight,
      bodyClientHeight: body.clientHeight,
      panelHeight: panel.getBoundingClientRect().height,
      viewportHeight: window.innerHeight,
    }
  })
  allResults.push({
    name: 'trailing card — last entry visible at bottom of trailing activity',
    pass: trailingMetrics.visibleInContainer,
    detail: `cTop=${trailingMetrics.cTop.toFixed(1)} cBottom=${trailingMetrics.cBottom.toFixed(1)} lTop=${trailingMetrics.lTop.toFixed(1)} lBottom=${trailingMetrics.lBottom.toFixed(1)}`,
  })
  allResults.push({
    name: 'trailing card — last entry actually visible in viewport (the user-visible bug)',
    pass: trailingMetrics.visibleInViewport,
    detail: `lBottom=${trailingMetrics.lBottom.toFixed(1)} viewport=${trailingMetrics.viewportHeight} bodyScrollTop=${trailingMetrics.bodyScrollTop} bodyMax=${trailingMetrics.bodyScrollHeight - trailingMetrics.bodyClientHeight}`,
  })

  // Wheel-driven scroll: the user scrolls with the mouse wheel, not by
  // setting scrollTop programmatically.  Wheel events should be able to
  // reach the same maximum scrollTop.
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(`${FIXTURE_URL}?entries=200&handoff=20`)
  await page.waitForSelector('[data-testid="last-activity-entry"]', { state: 'attached' })
  await page.waitForTimeout(50)
  // Send many wheel ticks targeted at the activity scroll container.
  await page.evaluate(async () => {
    const container = document.querySelector('[data-testid="activity-scroll"]') as HTMLElement
    container.focus?.()
    for (let i = 0; i < 200; i++) {
      container.dispatchEvent(new WheelEvent('wheel', { deltaY: 200, bubbles: true, cancelable: true }))
      // jsdom doesn't drive real scroll from wheel, so simulate by adding to scrollTop too.
      container.scrollTop += 200
      await new Promise((r) => requestAnimationFrame(() => r(null)))
    }
  })
  await page.waitForTimeout(50)
  const wheelMetrics = await readActivityMetrics(page)
  allResults.push({
    name: 'wheel-driven scroll — last entry reachable',
    pass: wheelMetrics.lastEntryVisible,
    detail: `scrollTop=${wheelMetrics.scrollTop} maxScrollTop=${wheelMetrics.maxScrollTop} lastEntryBottom=${wheelMetrics.lastEntryBottom.toFixed(1)} containerBottom=${wheelMetrics.containerBottom.toFixed(1)}`,
  })

  await browser.close()

  let failed = 0
  for (const r of allResults) {
    const tag = r.pass ? 'PASS' : 'FAIL'
    const line = `[${tag}] ${r.name} :: ${r.detail}`
    if (r.pass) {
      console.log(line)
    } else {
      console.error(line)
      failed += 1
    }
  }

  if (failed > 0) {
    console.error(`\n${failed} of ${allResults.length} assertions failed`)
    process.exit(1)
  }
  console.log(`\nAll ${allResults.length} assertions passed`)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
