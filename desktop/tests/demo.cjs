// Demo space: it can be left from the header, its banner and settings, back to the user's own
// records (here a scratch record directory); settings are opened from the sidebar only.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'demo-')), home = path.join(temp, 'records');
fs.mkdirSync(home);
const bundled = process.env.DESK_EXECUTABLE && path.join(path.dirname(process.env.DESK_EXECUTABLE), 'resources/python/runtime/python.exe');
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences') };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'CODEX_HOME', 'AUTOSWARM_DESK_PYTHON']) delete env[key];
if (!bundled) env.AUTOSWARM_DESK_PYTHON = python;
let app;
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--demo', '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    const frame = () => page.evaluate(() => window.desk.initial());
    await until(async () => (await frame())?.demo === true && await page.locator('.session-item').count(), 'the demo');
    assert.equal(await page.locator('.header-settings').count(), 0, 'No settings button in the header');
    assert.equal(await page.locator('aside [data-action="settings"]').count(), 1, 'Settings open from the sidebar');
    assert.ok(await page.locator('.demo-banner [data-action="exit-demo"]').isVisible(), 'The demo says how to leave it');
    await page.screenshot({ path: path.join(output, 'demo-banner.png') });
    // From the header.
    await page.locator('.page-header [data-action="exit-demo"]').click();
    await until(async () => { const f = await frame(); return f && !f.demo && f.data?.home && path.resolve(f.data.home) === path.resolve(home) && f.assistantSettings?.ready; }, 'own records after leaving from the header');
    assert.equal(await page.locator('.demo-banner, .page-header [data-action="exit-demo"]').count(), 0, 'Nothing of the demo is left');
    // Into the demo again, and out through settings.
    await page.locator('aside [data-action="settings"]').click();
    await page.waitForSelector('#settings[open]');
    await page.locator('#settings [data-action="demo"]').click();
    await until(async () => (await frame())?.demo === true, 'the demo again');
    await page.locator('aside [data-action="settings"]').click();
    await page.waitForSelector('#settings[open]');
    assert.match(await page.locator('#settings [data-action="exit-demo"]').innerText(), /退出演示/);
    await page.locator('#settings [data-action="exit-demo"]').click();
    await until(async () => { const f = await frame(); return f && !f.demo && f.assistantSettings?.ready; }, 'own records after leaving from settings');
    assert.equal(await page.locator('#settings[open]').count(), 0, 'Settings close on the way out');
    console.log('PASS: demo left from the header and from settings back to the chosen record directory; settings only in the sidebar.');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    await app?.close().catch(() => {});
  }
})();
