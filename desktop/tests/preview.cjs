// Side preview panel (0.9.4) with a deterministic native peer (no model calls): links in a reply open
// in browser-like tabs — Markdown rendered (its own links navigate the same tab), a local page in the
// locked-down embedded browser (kept alive while another tab is in front), text at a line, JSON, PDF,
// a web address — the same file reuses its tab, a missing file only warns, tabs close with × or a
// middle click, and the panel collapses and expands without losing its tabs.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'preview-')), home = path.join(temp, 'records'), workspace = path.join(temp, '工作 目录'), user = path.join(temp, 'user');
for (const dir of [home, workspace, user]) fs.mkdirSync(dir);
fs.writeFileSync(path.join(workspace, 'notes.md'), '# Notes\n\nSee [the page](page.html) and `data.txt`.\n');
fs.writeFileSync(path.join(workspace, 'page.html'), '<!doctype html><title>Probe page</title><h1>Hello from a local page</h1>');
fs.writeFileSync(path.join(workspace, 'data.txt'), 'line one\nline two\nline three\nline four\n');
fs.writeFileSync(path.join(workspace, 'data.json'), JSON.stringify({ probe: true, items: [1, 2, 3] }, null, 2));
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': { command: [python, path.join(root, 'tests/fixtures/provider.py'), 'claude-code'] } } }));
const env = { ...process.env, AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), AUTOSWARM_DESK_PYTHON: python, USERPROFILE: user, HOME: user };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'AUTOSWARM_DESK_LANGUAGE']) delete env[key];
const REPLY = 'Links:\n\n- [absolute](<' + path.join(workspace, 'data.txt') + '>)\n- [notes](notes.md)\n- [page](page.html)\n- [data](data.txt:3)\n- [json](data.json)\n- [doc](doc.pdf)\n- [gone](gone.md)\n- [web](https://example.invalid/page)\n- file `data.txt`\n\nend';
let app;
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    await until(async () => (await page.evaluate(() => window.desk.initial()))?.assistantSettings?.ready, 'Desk control');
    // A real PDF, printed by Electron itself.
    const pdf = await app.evaluate(async ({ BrowserWindow }) => {
      const printer = new BrowserWindow({ show: false });
      await printer.loadURL('data:text/html,<h1 style="font:64px sans-serif">PDF probe</h1>');
      const bytes = await printer.webContents.printToPDF({}); printer.destroy();
      return Buffer.from(bytes).toString('base64');
    });
    fs.writeFileSync(path.join(workspace, 'doc.pdf'), Buffer.from(pdf, 'base64'));
    await app.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, workspace);
    await page.locator('aside .new-session').click();
    await page.locator('#session-agent').click(); await page.locator('[data-pick-agent="claude-code"]').click();
    await page.locator('[data-menu="workspace"]').click(); await page.locator('[data-action="session-workspace"]').click();
    await page.locator('#session-input').fill(REPLY); await page.locator('#session-input').press('Enter');
    await page.waitForSelector('.turn-reply a[href="notes.md"]', { timeout: 30000 });
    const pane = page.locator('#preview'), here = pane.locator('.preview-page.is-active');
    const title = () => pane.locator('.preview-title strong').innerText(), tabs = () => pane.locator('.preview-tab').count();
    const link = href => page.locator(`.turn-reply a[href="${href}"]`);
    // × shows on the active tab and on hover, as in a browser.
    const closeTab = async tab => { await tab.hover(); await tab.locator('.preview-tab-close').click(); };
    const guest = (suffix, code) => app.evaluate(({ webContents }, [suffix, code]) => webContents.getAllWebContents().find(w => w.getType() === 'webview' && w.getURL().endsWith(suffix))?.executeJavaScript(code), [suffix, code]);

    // 1. Markdown in a first tab; its own link navigates that tab, and back returns.
    const appBefore = await page.locator('#app').boundingBox();
    await link('notes.md').click();
    await here.locator('.preview-doc h1').waitFor();
    assert.equal(await title(), 'notes.md');
    const appWith = await page.locator('#app').boundingBox(), paneBox = await pane.boundingBox();
    assert.ok(appWith.width < appBefore.width - 200 && Math.abs(appWith.x + appWith.width - paneBox.x) < 2, 'The panel sits beside the conversation');
    await here.locator('.preview-doc a[href="page.html"]').click();
    await until(async () => await title() === 'Probe page', 'the page, in the same tab');
    assert.equal(await tabs(), 1, 'A link inside a tab navigates that tab');
    await pane.locator('[data-preview="back"]').click();
    await until(async () => await title() === 'notes.md', 'back to the Markdown file');

    // 2. Each kind in its own tab; the same file reuses its tab.
    await link('page.html').click();
    await until(async () => await title() === 'Probe page' && await guest('page.html', '1'), 'the page in a second tab');
    assert.deepEqual([...new Set(await app.evaluate(async ({ webContents }) => Promise.all(webContents.getAllWebContents().filter(w => w.getType() === 'webview').map(w => w.executeJavaScript('typeof require + "/" + typeof process')))))], ['undefined/undefined'], 'Embedded pages get no Node');
    await guest('page.html', 'window.kept = 42');
    await link('data.txt:3').click();
    await here.locator('.preview-code .ln.is-target').waitFor();
    assert.equal(await here.locator('.ln.is-target').getAttribute('data-n'), '3');
    await page.locator('.turn-reply a', { hasText: 'absolute' }).click();  // A Windows path with spaces: the same file.
    await until(async () => await title() === 'data.txt' && !(await here.locator('.ln.is-target').count()), 'the same file, without the line');
    await page.locator('.turn-reply code[data-path="data.txt"]').click();
    await link('data.json').click();
    await until(async () => /"probe": true/.test(await here.locator('.preview-code').innerText()), 'the JSON tab');
    await link('doc.pdf').click();
    await until(async () => (await here.locator('iframe.preview-frame').getAttribute('src') || '').endsWith('doc.pdf'), 'the PDF tab');
    await until(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.mainFrame.framesInSubtree.some(f => f.url.endsWith('doc.pdf'))), 'the PDF to load');
    assert.ok(await page.evaluate(() => { const f = document.querySelector('#preview .preview-page.is-active .preview-frame'); return f.getBoundingClientRect().height >= document.querySelector('.preview-body').getBoundingClientRect().height - 1; }), 'The PDF fills the panel');
    await link('https://example.invalid/page').click();
    await until(async () => (await here.locator('webview').getAttribute('src')) === 'https://example.invalid/page', 'the web address tab');
    assert.equal(await pane.locator('[data-preview="external"]').count(), 1, 'Web pages offer the system browser');
    assert.deepEqual(await pane.locator('.preview-tab-title').allInnerTexts(), ['notes.md', 'Probe page', 'data.txt', 'data.json', 'doc.pdf', 'example.invalid']);
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(output, 'preview-tabs.png') });

    // 3. A tab in the background keeps its page: nothing reloads when it comes back to the front.
    await pane.locator('.preview-tab', { hasText: 'Probe page' }).click();
    assert.equal(await title(), 'Probe page');
    assert.equal(await guest('page.html', 'window.kept'), 42, 'The page was not reloaded');
    await page.screenshot({ path: path.join(output, 'preview-html.png') });

    // 4. A missing file warns and changes nothing.
    await link('gone.md').click();
    await until(async () => (await page.locator('#toast.visible').count()) && /gone\.md/.test(await page.locator('#toast').innerText()), 'the missing-file warning');
    assert.equal(await tabs(), 6);

    // 5. Close with × and with a middle click; the neighbour comes to the front.
    await closeTab(pane.locator('.preview-tab', { hasText: 'doc.pdf' }));
    assert.equal(await tabs(), 5);
    await pane.locator('.preview-tab', { hasText: 'data.json' }).click({ button: 'middle' });
    assert.equal(await tabs(), 4);
    assert.deepEqual(await pane.locator('.preview-tab-title').allInnerTexts(), ['notes.md', 'Probe page', 'data.txt', 'example.invalid']);

    // 6. Collapse from the panel, expand from the page header; the tabs are all still there.
    await pane.locator('[data-preview="collapse"]').click();
    assert.equal(await pane.isHidden(), true);
    assert.equal(await page.locator('.preview-toggle').getAttribute('aria-pressed'), 'false');
    await page.locator('.preview-toggle').click();
    assert.equal(await pane.isVisible(), true);
    assert.equal(await tabs(), 4);
    assert.equal(await guest('page.html', 'window.kept'), 42, 'Collapsing keeps the pages too');

    // 7. Resize by the left edge; closing the last tab hides the panel and its toggle.
    const handle = await pane.locator('[data-preview-resize]').boundingBox(), width = (await pane.boundingBox()).width;
    await page.mouse.move(handle.x + 3, handle.y + 200); await page.mouse.down(); await page.mouse.move(handle.x - 117, handle.y + 200, { steps: 6 }); await page.mouse.up();
    assert.ok((await pane.boundingBox()).width > width + 80, 'Dragging the edge widens the panel');
    while (await tabs()) await closeTab(pane.locator('.preview-tab').first());
    assert.equal(await pane.isHidden(), true);
    assert.equal(await page.locator('.preview-toggle').count(), 0);
    console.log('PASS: tabs per link (Markdown, page, text at a line, JSON, PDF, web), same file reuses its tab, links inside a tab navigate it, background tabs keep their page, missing file warns, close by × and middle click, collapse/expand keeps tabs, resize, last tab hides the panel.');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally { if (app) await app.close(); }
})();
