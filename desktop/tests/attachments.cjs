// Attachments in Desk with deterministic native peers (no model calls): an image pasted and a file picked in the
// new-session composer, removed and added again, sent with the task (copied into the records, the image given to
// Claude Code natively); the conversation shows them; an image dropped on the conversation composer goes with a message.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const helpers = require('./helpers.cjs'), { python } = helpers;
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'attachments-')), home = path.join(temp, 'records'), workspace = path.join(temp, '工作 目录'), user = path.join(temp, 'user home');
for (const dir of [home, workspace, user]) fs.mkdirSync(dir);
const trace = path.join(temp, 'trace.jsonl'), notes = path.join(temp, '会议 notes.txt');
fs.writeFileSync(notes, 'notes');
const fixture = provider => ({ command: [python, path.join(root, 'tests/fixtures/provider.py'), provider, '--selection-trace=' + trace] });
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': fixture('claude-code') } }));
const bundled = process.env.DESK_EXECUTABLE && path.join(path.dirname(process.env.DESK_EXECUTABLE), 'resources/python/runtime/python.exe');
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), USERPROFILE: user, HOME: user };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'CODEX_HOME', 'AUTOSWARM_DESK_PYTHON']) delete env[key];
if (!bundled) env.AUTOSWARM_DESK_PYTHON = python;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const traced = () => fs.existsSync(trace) ? fs.readFileSync(trace, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
const until = (predicate, what, timeout = 60000) => helpers.until(predicate, what, timeout);
// A 2x2 PNG made in the page, delivered as a paste or a drop (a clipboard image has no path on disk).
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==';
const give = (page, selector, type) => page.evaluate(([selector, type, png]) => {
  const bytes = Uint8Array.from(atob(png), c => c.charCodeAt(0)), transfer = new DataTransfer();
  transfer.items.add(new File([bytes], 'screen.png', { type: 'image/png' }));
  const target = document.querySelector(selector);
  if (type === 'paste') target.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  else { target.dispatchEvent(new DragEvent('dragover', { dataTransfer: transfer, bubbles: true, cancelable: true })); target.dispatchEvent(new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true })); }
}, [selector, type, PNG]);
let app;
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    const frame = () => page.evaluate(() => window.desk.initial());
    await until(async () => (await frame())?.assistantSettings?.ready, 'Desk control');
    // The workspace dialog picks a folder; the attach dialog picks the notes file.
    await app.evaluate(({ dialog }, [workspace, notes]) => { dialog.showOpenDialog = async (_, options) => ({ canceled: false, filePaths: [options.properties.includes('openDirectory') ? workspace : notes] }); }, [workspace, notes]);
    await page.locator('aside .new-session').click();
    await page.locator('#session-agent').click(); await page.locator('[data-pick-agent="claude-code"]').click();
    await page.locator('[data-menu="workspace"]').click(); await page.locator('[data-action="session-workspace"]').click();

    // 1. A pasted image and a picked file wait above the text; × removes one, the button adds it again.
    await give(page, '#session-input', 'paste');
    await page.waitForSelector('#session-form .attach-chip img');
    await page.locator('#session-form [data-attach-pick]').click();
    await until(async () => await page.locator('#session-form .attach-chip').count() === 2, 'two attachments');
    assert.deepEqual(await page.locator('#session-form .attach-name').allInnerTexts(), [(await page.locator('#session-form .attach-name').first().innerText()), '会议 notes.txt']);
    await page.locator('#session-form .attach-remove').nth(1).click();
    assert.equal(await page.locator('#session-form .attach-chip').count(), 1);
    await page.locator('#session-form [data-attach-pick]').click();
    await until(async () => await page.locator('#session-form .attach-chip').count() === 2, 'the file again');
    await page.screenshot({ path: path.join(output, 'attachments-composer.png') });

    // 2. Sent with the task: copied into the records, the image given to Claude Code itself, every path named.
    await page.locator('#session-input').fill('这张截图里有什么？'); await page.locator('#session-input').press('Enter');
    const agent = await until(async () => { const f = await frame(); return Object.values(f.data.agents).find(a => a.call_id && f.data.calls[a.call_id]?.task.attachments?.length); }, 'the conversation');
    const task = read(path.join(home, 'calls', agent.call_id, 'task.json'));
    assert.deepEqual(task.attachments.map(item => item.kind), ['image', 'file']);
    assert.ok(task.attachments.every(item => path.dirname(path.dirname(item.path)) === path.join(home, 'attachments')), 'Kept in the records');
    const user1 = await until(() => traced().find(value => value.type === 'user'), 'the first turn');
    assert.equal(user1.message.content[1].type, 'image');
    assert.ok(user1.message.content[0].text.includes(task.attachments[1].path), 'The text names every attachment');
    await page.waitForSelector('.chat-user .attach-chip.sent');
    assert.deepEqual(await page.locator('.chat-user .attach-chip.sent .attach-name').allInnerTexts(), task.attachments.map(item => item.name));

    // 3. An image dropped on the conversation composer goes with the next message.
    await until(() => fs.existsSync(path.join(home, 'calls', agent.call_id, 'result.json')), 'the first turn ends');
    await give(page, '#chat-form', 'drop');
    await page.waitForSelector('#chat-form .attach-chip img');
    await page.screenshot({ path: path.join(output, 'attachments-conversation.png') });
    await page.locator('#chat-input').fill('再看这张'); await page.locator('#chat-input').press('Enter');
    const message = await until(async () => Object.values((await frame()).data.messages).find(m => m.message.kind === 'user_message' && m.message.attachments?.length), 'the message');
    assert.equal(message.message.attachments[0].kind, 'image');
    await until(() => traced().filter(value => value.type === 'user').length === 2, 'the second turn');
    assert.equal(await page.locator('#chat-form .attach-chip').count(), 0, 'Sent attachments leave the composer');
    await page.waitForSelector('.chat-user >> nth=1 >> .attach-chip.sent');
    await page.screenshot({ path: path.join(output, 'attachments-sent.png') });
    console.log('PASS: paste, pick, remove and drop in the composers; attachments kept in the records, the image given natively, every path named, shown in the conversation.');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    if (app) await app.close().catch(() => {});
  }
})();
