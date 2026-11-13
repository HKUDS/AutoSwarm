// Appearance preference: toggled in the renderer, stored by the main process,
// restored on the next launch (including the native window background).
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'theme-'));
const userData = path.join(temp, 'preferences');
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: userData };
delete env.ELECTRON_RUN_AS_NODE;
delete env.AUTOSWARM_HOME;
async function launch() {
  const application = await electron.launch({ args: [path.join(root, 'desktop'), '--demo'], env,
    ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
  const page = await application.firstWindow();
  await page.waitForSelector('.session-item', { timeout: 30000 });
  return { application, page };
}
const background = application => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBackgroundColor().toLowerCase());
(async () => {
  let { application, page } = await launch();
  try {
    await page.locator('[data-action="theme"]').first().click();
    const chosen = await page.evaluate(() => document.documentElement.dataset.theme);
    const stored = () => JSON.parse(fs.readFileSync(path.join(userData, 'preferences.json'), 'utf8')).theme;
    assert.equal(stored(), chosen, 'Toggle writes the explicit appearance to preferences.json');
    assert.equal(await background(application), chosen === 'dark' ? '#151517' : '#ffffff');
    await application.close();
    ({ application, page } = await launch());
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), chosen, 'Appearance is restored after restart');
    assert.equal(await background(application), chosen === 'dark' ? '#151517' : '#ffffff', 'Window background matches before first paint');
    await page.locator('[data-action="settings"]').first().click();
    await page.locator('[data-theme-choice="system"]').click();
    assert.equal(stored(), 'system');
    assert.equal(await page.locator('[data-theme-choice="system"]').getAttribute('aria-checked'), 'true');
    await page.screenshot({ path: path.join(output, 'theme-settings.png') });
    await page.locator('[data-close="settings"]').click();
    // Native Codex turn: one header per turn, process folded, reply open, copy via the main process.
    await page.locator('aside .session-item', { hasText: '调研 Terminal 轨迹数据覆盖' }).click();
    await page.waitForSelector('.work-summary');
    assert.equal(await page.locator('.conversation-scroll .message-author').count(), 0, 'Turns carry no avatar row, like DeepSeek');
    assert.equal(await page.locator('.conversation-scroll .agent-message').count(), 1, 'One agent block per turn, not per progress message');
    assert.equal(await page.locator('.work-summary[open]').count(), 0, 'Process is folded by default');
    assert.match((await page.locator('.work-summary > summary').innerText()).replace(/\s+/g, ' '), /已处理 36 分 35 秒 · 13 次工具调用 · 5 条进展 · 1 个失败/);
    // Another program can hold the Windows clipboard open; then nothing can write it.
    const usable = await application.evaluate(({ clipboard }) => { const probe = 'desk-probe-' + Date.now(); clipboard.writeText(probe); return clipboard.readText() === probe; });
    await page.locator('.turn-reply + .message-actions [data-copy]').click();
    if (usable) {
      const copied = await application.evaluate(({ clipboard }) => clipboard.readText());
      assert.match(copied, /^\*\*autoswarm 当前可用\*\*/, 'Copy yields the original Markdown of the reply');
    } else console.warn('SKIP copy check: the system clipboard is held by another program.');
    console.log(`PASS: appearance toggle (${chosen}), stored preference, restart restore, native background, follow system, native turn folding${usable ? ', copy' : ''}.`);
  } finally { await application.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
