const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');

const desktop = path.resolve(__dirname, '..');
const output = path.resolve(desktop, '../.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
// Prove direct opening also works without node_modules or any dev server.
const standalone = fs.mkdtempSync(path.join(output, 'browser-'));
fs.mkdirSync(path.join(standalone, 'renderer'));
for (const filename of ['index.html', 'style.css', 'startup.js', 'app.js']) {
  fs.copyFileSync(path.join(desktop, 'renderer', filename), path.join(standalone, 'renderer', filename));
}
fs.cpSync(path.join(desktop, 'assets'), path.join(standalone, 'assets'), { recursive: true });

(async () => {
  for (const channel of ['msedge', 'chrome']) {
    const browser = await chromium.launch({ channel, headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 1320, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      await page.goto(pathToFileURL(path.join(desktop, 'renderer/index.html')).href + '?lang=zh');
      await page.waitForSelector('.session-item');
      assert.match(await page.locator('.preview-banner').innerText(), /浏览器预览.*示例数据/);
      assert.equal(await page.locator('.loading').count(), 0);
      assert.equal(await page.locator('.window-buttons').isVisible(), false);
      assert.equal(await page.evaluate(() => typeof window.desk), 'undefined');
      await page.locator('aside [data-session="call_01"]').click();
      if(await page.locator('.dock-heading').getAttribute('aria-expanded')==='false')await page.locator('.dock-heading').click();
      await page.locator('[data-worker="call_05"]').click();
      assert.match(await page.locator('.worker-expanded').innerText(), /认证接口文档已更新/);
      await page.locator('[data-detail="call_05"]').click();
      await page.waitForSelector('.worker-scroll');
      assert.match(await page.locator('.worker-scroll').innerText(), /次工具调用/);
      await page.locator('.process-turn>details').first().evaluate(el=>el.open=true);
      // Tool output sits one level deeper, on its own step row (Codex-style).
      await page.locator('.terminal-output').first().evaluate(el=>{for(let d=el.closest('details');d;d=d.parentElement.closest('details'))d.open=true;});
      assert.match(await page.locator('.terminal-output').first().innerText(), /verify_token/);
      assert.equal(await page.locator('.diff .addition').count() > 0, true);
      await page.locator('[data-action="worker-back"]').click();
      await page.locator('[data-worker="call_05"]').click();
      await page.locator('aside [data-action="overview"]').click();
      assert.equal(await page.locator('.decision-card').count(), 2);
      await page.locator('[data-session="call_09"]').first().click();
      assert.match(await page.locator('.markdown').innerText(), /Q3 数据已整理完成/);
      await page.locator('.preview-banner [data-action="settings"]').click();
      assert.match(await page.locator('#settings').innerText(), /AutoSwarm Desk.exe/);
      assert.equal(await page.locator('#settings [data-action="choose-home"]').count(), 0);
      await page.locator('[data-close="settings"]').first().click();
      // P2: direct new conversation page and stop (demo only simulates both).
      await page.locator('aside .new-session').click();
      assert.equal(await page.locator('.home-title h2').innerText(), '开始一项新工作');
      await page.locator('[data-menu="model"]').click();
      await page.locator('[data-pick-model="opus"]').click();
      await page.locator('[data-pick-effort="high"]').click();
      assert.match(await page.locator('#session-model-chip').innerText(), /Opus\s*High/);
      await page.locator('[data-menu="agent"]').click();
      await page.locator('[data-pick-agent="codex"]').click();
      assert.match(await page.locator('#session-model-chip').innerText(), /默认模型/, 'native model and effort values do not carry across agents');
      await page.locator('[data-menu="model"]').click();
      assert.equal(await page.locator('[data-pick-effort="ultra"]').count(), 0, 'efforts come from the chosen model');
      await page.locator('[data-pick-model="gpt-6-luna"]').click();
      assert.deepEqual(await page.locator('[data-pick-effort]').evaluateAll(b => b.map(x => x.dataset.pickEffort)), ['', 'low', 'medium', 'high']);
      await page.keyboard.press('Escape');
      await page.locator('#session-input').fill('演示新会话');
      await page.locator('#session-input').press('Enter');
      assert.match(await page.locator('#toast').innerText(), /界面演示/);
      await page.locator('aside [data-session="call_01"]').click();
      await page.locator('.chat-composer .stop-button').click();
      assert.match(await page.locator('#detail').innerText(), /子任务/);
      await page.locator('#detail [data-confirm-stop]').click();
      assert.equal(await page.locator('.chat-composer .stop-button').count(), 0);
      assert.match(await page.locator('.dock-description').innerText(), /已停止/);
      await page.reload();
      await page.waitForSelector('.session-item');
      await page.screenshot({ path: path.join(output, `browser-${channel}.png`) });
      await page.setViewportSize({ width: 640, height: 720 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.goto(pathToFileURL(path.join(standalone, 'renderer/index.html')).href + '?lang=zh');
      await page.waitForSelector('.session-item');
      assert.match(await page.locator('h1').innerText(), /私人助理/);
      assert.equal(await page.locator('aside .session-item').count(),4);
      assert.equal(await page.locator('aside [data-session="call_meta"],aside [data-session="call_02"]').count(),0);
      assert.deepEqual(errors, []);
      console.log(`PASS ${channel}: direct file URL, interactive preview, reload, desktop guidance, narrow layout, standalone without dependencies.`);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
