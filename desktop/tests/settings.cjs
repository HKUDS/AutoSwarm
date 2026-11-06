// Settings (0.9.2), in the real Electron demo with a fresh preferences folder: English by default,
// every control inside the dialog at a wide and the narrowest window, Agent permissions as inline
// choices, the language switch (Desk's text only; agent content stays as written), zoom by keys and
// buttons (main window only), and both kept after a restart.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'settings-')), userData = path.join(temp, 'preferences');
const env = { ...process.env, AUTOSWARM_DESK_USER_DATA: userData };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'AUTOSWARM_DESK_LANGUAGE']) delete env[key];
const stored = () => JSON.parse(fs.readFileSync(path.join(userData, 'preferences.json'), 'utf8'));
const CJK = /[一-鿿]/;
async function launch() {
  const application = await electron.launch({ args: [path.join(root, 'desktop'), '--demo'], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
  const page = await application.firstWindow();
  await page.waitForSelector('.session-item', { timeout: 30000 });
  return { application, page };
}
const zoomOf = application => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map(w => ({ url: w.webContents.getURL(), zoom: w.webContents.getZoomFactor() })));
async function openSettings(page) {
  if (!await page.locator('#settings[open]').count()) await page.locator('[data-action="settings"]').first().click();
  await page.waitForSelector('#settings[open]'); await page.waitForTimeout(250);  // Past the opening animation.
}
/** Nothing in settings is wider than the dialog or sticks out of it sideways. */
async function inside(page, label) {
  const report = await page.evaluate(() => {
    const dialog = document.querySelector('#settings').getBoundingClientRect(), content = document.querySelector('#settings-content');
    const out = [...content.querySelectorAll('button, label, p, code, .settings-row, .permission-row, .segmented, .zoom-control')]
      .filter(el => el.getClientRects().length).map(el => ({ el: el.className || el.tagName, text: el.textContent.trim().slice(0, 30), box: el.getBoundingClientRect() }))
      .filter(({ box }) => box.left < dialog.left - 0.5 || box.right > dialog.right + 0.5);
    return { overflow: content.scrollWidth - content.clientWidth, out: out.map(({ el, text, box }) => `${el} "${text}" ${Math.round(box.left)}-${Math.round(box.right)}`), dialog: [Math.round(dialog.left), Math.round(dialog.right)] };
  });
  assert.ok(report.overflow <= 0, `${label}: settings scroll sideways by ${report.overflow}px`);
  assert.deepEqual(report.out, [], `${label}: outside the dialog ${JSON.stringify(report.dialog)}`);
}
(async () => {
  let { application, page } = await launch();
  try {
    // 1. English by default; agent content is shown as written.
    assert.equal(await page.evaluate(() => document.documentElement.lang), 'en-GB');
    assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(), 'New session');
    assert.ok(await page.locator('aside .session-item', { hasText: '调研 Terminal' }).count(), 'Session titles are agent content and stay in their language');
    await openSettings(page);
    // Assistant conversation titles are the user's own words and stay as written.
    const text = await page.locator('#settings').evaluate(el => { const copy = el.cloneNode(true); copy.querySelectorAll('.assistant-row-text strong').forEach(n => n.remove()); return copy.innerText; });
    assert.ok(!CJK.test(text.replace('中文', '')), `English settings contain Chinese: ${text.match(/.{0,20}[一-鿿].{0,20}/)?.[0]}`);

    // 2. Layout at a wide and the narrowest window, both languages and themes.
    for (const [width, height] of [[1180, 820], [540, 600]]) {
      await application.evaluate(({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setSize(...size), [width, height]);
      await page.waitForTimeout(300);
      for (const lang of ['en', 'zh']) {
        await page.locator(`[data-language="${lang}"]`).click();
        await inside(page, `${width}x${height} ${lang} top`);
        await page.locator('.permission-settings').scrollIntoViewIfNeeded();
        await inside(page, `${width}x${height} ${lang} permissions`);
        for (const option of await page.locator('.permission-option').all()) assert.ok(await option.isVisible(), 'Every permission choice is visible');
        await page.screenshot({ path: path.join(output, `settings-${width}-${lang}.png`) });
      }
    }
    await page.locator('[data-language="en"]').click();
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1180, 820));

    // 3. Agent permissions: a choice is one click and is kept in Desk's preferences.
    await page.locator('[data-default-permission="full-access"][data-provider="codex"]').click();
    assert.equal(stored().permissions.codex, 'full-access');
    assert.equal(await page.locator('[data-default-permission="full-access"]').getAttribute('aria-checked'), 'true');
    assert.match(await page.locator('.permission-row', { hasText: 'Codex' }).locator('.permission-note').innerText(), /No sandbox/);

    // 4. Language: Desk's text follows at once and is stored.
    await page.locator('[data-language="zh"]').click();
    assert.equal(stored().language, 'zh');
    assert.equal(await page.locator('#settings h2').innerText(), '连接与设置');
    assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(), '新会话');
    await page.locator('[data-language="en"]').click();
    assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(), 'New session');

    // 5. Zoom: Ctrl+= / Ctrl+- / Ctrl+0 and the buttons; the tray popup is another page and stays at 100%.
    const main = async () => (await zoomOf(application)).find(w => w.url.endsWith('index.html')).zoom;
    // As the keyboard sends them (Playwright's synthetic keys never reach before-input-event).
    const press = key => application.evaluate(({ BrowserWindow }, keyCode) => BrowserWindow.getAllWindows()[0].webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers: ['control'] }), key);
    await press('=');
    await page.waitForFunction(() => document.querySelector('.zoom-value')?.textContent === '110%');
    assert.equal(await main(), 1.1); assert.equal(stored().zoom, 1.1);
    await press('-'); await press('-');
    await page.waitForFunction(() => document.querySelector('.zoom-value')?.textContent === '90%');
    assert.equal(await main(), 0.9);
    await press('0');
    await page.waitForFunction(() => document.querySelector('.zoom-value')?.textContent === '100%');
    for (const value of ['110%', '125%']) { await page.locator('[data-zoom="1"]').click(); await page.waitForFunction(v => document.querySelector('.zoom-value')?.textContent === v, value); }
    assert.equal(await main(), 1.25);
    const others = (await zoomOf(application)).filter(w => !w.url.endsWith('index.html'));
    assert.ok(others.every(w => w.zoom === 1), `Only the main window zooms: ${JSON.stringify(others)}`);
    await inside(page, 'zoomed 125%');

    // 6. Both are restored on the next launch.
    await page.locator('[data-language="zh"]').click();
    await application.close();
    ({ application, page } = await launch());
    assert.equal(await page.locator('aside .new-session .sidebar-text').innerText(), '新会话');
    assert.equal(await main(), 1.25, 'Zoom is restored');
    await openSettings(page);
    assert.equal(await page.locator('.zoom-value').innerText(), '125%');
    await page.locator('[data-zoom="0"]').click();
    await page.waitForFunction(() => document.querySelector('.zoom-value')?.textContent === '100%');
    console.log('PASS: English by default, settings inside the dialog (1180 and 540 wide, en/zh, zoomed), inline permission choices, language switch and restore, zoom keys/buttons/restore, tray popup unzoomed.');
  } finally { await application.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
