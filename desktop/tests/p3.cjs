// P3 · desktop integration with deterministic native peers (no model calls):
// several sessions wait at once and are settled from the tray popup alone;
// notifications, badge, jump-to-card, login item, background start,
// single instance, and the optional assistant wake-up.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { spawn, spawnSync } = require('node:child_process');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'p3-')), home = path.join(temp, 'records'), workspace = path.join(temp, '托盘 工作区');
fs.mkdirSync(home); fs.mkdirSync(workspace);
const fixture = provider => ({ command: [python, path.join(root, 'tests/fixtures/provider.py'), provider] });
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': fixture('claude-code'), codex: fixture('codex') } }));
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), AUTOSWARM_DESK_PYTHON: python, USERPROFILE: path.join(temp, 'user'), HOME: path.join(temp, 'user') };
fs.mkdirSync(path.join(temp, 'user'), { recursive: true });  // Desk installs the AutoSwarm Skill into the user home: keep it scratch.
delete env.ELECTRON_RUN_AS_NODE; delete env.AUTOSWARM_HOME;
const cliEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('AUTOSWARM_') && k !== 'CODEX_THREAD_ID'));
cliEnv.PYTHONPATH = path.join(root, 'src');
const launchArgs = extra => ({ args: [path.join(root, 'desktop'), '--home=' + home, ...extra], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const shot = (page, name) => page.screenshot({ path: path.join(output, `p3-${name}.png`) });
function cli(args, input = '', extraEnv = {}) {
  const result = spawnSync(python, ['-m', 'autoswarm', '--home', home, ...args], { input, env: { ...cliEnv, ...extraEnv }, encoding: 'utf8', windowsHide: true, timeout: 30000 });
  const value = JSON.parse(result.stdout);
  if (!value.ok) throw Error(JSON.stringify(value.error));
  return value.data;
}
const notes = app => app.evaluate(() => globalThis.__notes.map(n => ({ title: n.title, body: n.body })));
let app;
async function launch(extra = []) {
  app = await electron.launch(launchArgs(extra));
  await app.evaluate(({ Notification, app: electronApp }) => {
    globalThis.__notes = []; globalThis.__login = [];
    Notification.prototype.show = function () { globalThis.__notes.push(this); };
    electronApp.setLoginItemSettings = settings => { globalThis.__login.push(settings); };
  });
  const page = await app.firstWindow();
  await page.waitForFunction(() => !!window.desk);
  await until(() => page.evaluate(async () => (await window.desk.initial())?.assistantSettings?.ready), 'Desk control');
  return page;
}
(async () => {
  try {
    let page = await launch();
    const controller = (await page.evaluate(() => window.desk.initial())).data.desk.controller_id;

    // The assistant has handed one session out; it is held until released below.
    const gate = path.join(temp, 'release-assistant-session');
    const assistant = cli(['--controller', controller, 'desk', 'assistant', '--agent', 'codex', '--cwd', workspace, '--input', '-'], '你好');
    cli(['wait', assistant.call_id]);
    const handed = cli(['run', '--agent', 'codex', '--cwd', workspace, '--background', '--no-parent', '--input', '-'], `gate:${gate}\nASSISTANT_HANDOFF`, { AUTOSWARM_CALL_ID: assistant.call_id });

    // 1. Closing the window keeps Desk in the tray.
    await page.locator('[data-window="close"]').click();
    await until(() => app.evaluate(({ BrowserWindow }) => !BrowserWindow.getAllWindows().find(w => w.getTitle() === 'AutoSwarm Desk' && w.webContents.getURL().endsWith('index.html')).isVisible()), 'main window hidden');
    assert.ok((await notes(app)).some(n => /仍在托盘中运行/.test(n.title)), 'first close explains the tray');

    // 2. Two sessions need the user at the same time.
    const approved = path.join(workspace, 'approved.txt'), answered = path.join(workspace, 'answered.txt');
    const first = cli(['--controller', controller, 'desk', 'start', '--agent', 'claude-code', '--cwd', workspace, '--input', '-'], `approval:${approved}\n修改配置`);
    const second = cli(['--controller', controller, 'desk', 'start', '--agent', 'claude-code', '--cwd', workspace, '--input', '-'], `question:${answered}\n确认范围`);
    await until(async () => (await notes(app)).filter(n => /需要你决定|在问你/.test(n.title)).length >= 2, 'two waiting notifications');
    const waitingNotes = (await notes(app)).filter(n => /需要你决定|在问你/.test(n.title));
    assert.deepEqual(waitingNotes.map(n => n.title).sort(), ['Claude Code 在问你', 'Claude Code 需要你决定']);
    const tray = await app.evaluate(() => ({ tooltip: globalThis.deskShell.tooltip, waiting: globalThis.deskShell.summary.waiting.length }));
    assert.equal(tray.waiting, 2);
    assert.match(tray.tooltip, /2 件等你决定/);

    // 3. Settle both from the tray popup only.
    await app.evaluate(() => globalThis.deskShell.toggle(true));
    const popup = await until(() => app.windows().find(w => w.url().endsWith('tray.html')), 'tray popup');
    await popup.waitForSelector('.tray-card [data-decide="approve"]');
    await until(async () => (await popup.locator('.tray-card').count()) === 2, 'two tray cards');
    assert.match(await popup.locator('.tray-label.attention').innerText(), /需要你 · 2/);
    assert.match(await popup.locator('.tray-card', { hasText: '在问' }).innerText(), /Choose scope\?/, 'the actual question is shown');
    await shot(popup, 'tray-waiting');
    await popup.locator('.tray-card [data-decide="approve"]').click();
    await popup.locator('.tray-card .question-option', { hasText: '本地' }).click();
    await until(() => fs.existsSync(approved) && fs.existsSync(answered), 'native tools ran after tray decisions');
    assert.equal(fs.readFileSync(approved, 'utf8'), 'executed');
    assert.equal(fs.readFileSync(answered, 'utf8'), 'answered');
    for (const call of [first.call_id, second.call_id]) {
      const dir = path.join(home, 'calls', call, 'approvals');
      assert.equal(read(path.join(dir, fs.readdirSync(dir)[0], 'decision.json')).actor, 'user');
    }
    await popup.waitForSelector('.tray-clear', { timeout: 30000 });
    await shot(popup, 'tray-clear');
    await until(async () => (await notes(app)).filter(n => n.title === '会话已完成').length >= 2, 'completion notifications');
    assert.equal(await app.evaluate(() => globalThis.deskShell.tooltip), 'AutoSwarm Desk · 1 个运行中');

    // 4. A notification click opens the main window on that session.
    await app.evaluate(() => globalThis.__notes.find(n => n.title === '会话已完成').emit('click'));
    await until(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some(w => w.webContents.getURL().endsWith('index.html') && w.isVisible())), 'main window shown');
    await page.waitForFunction(() => !!document.querySelector('.page-heading .header-chips'));
    assert.match(await page.locator('h1').innerText(), /修改配置|确认范围|approval|question/);

    // 5. Settings: login item and the optional assistant wake-up.
    await page.locator('[data-action="settings"]').first().click();
    await page.locator('#settings [data-pref="openAtLogin"]').check();
    await until(() => app.evaluate(() => globalThis.__login.length), 'login item');
    const login = (await app.evaluate(() => globalThis.__login))[0];
    assert.equal(login.openAtLogin, true);
    assert.ok(login.args.includes('--background'));
    await page.locator('#settings [data-pref="wakeAssistant"]').check();
    await until(() => read(path.join(temp, 'preferences/preferences.json')).wakeAssistant === true, 'wake preference');
    await shot(page, 'settings');
    await page.locator('[data-close="settings"]').click();
    fs.writeFileSync(gate, 'go');
    cli(['wait', handed.call_id]);
    const woken = await until(() => {
      const agent = read(path.join(home, 'agents', assistant.agent_id, 'agent.json'));
      return agent.call_id !== assistant.call_id && read(path.join(home, 'calls', agent.call_id, 'task.json'));
    }, 'assistant wake-up call');
    assert.match(woken.prompt, /^\[Desk 自动唤醒\] .*ASSISTANT_HANDOFF|^\[Desk 自动唤醒\] /);
    await page.locator('[data-action="overview"]').first().click();
    await page.waitForSelector('.system-note');
    assert.match(await page.locator('.system-note').innerText(), /Desk 自动唤醒助理/);
    assert.ok(await page.locator('.handoff-card').count() >= 1, 'the assistant turn shows the session it handed out');
    await shot(page, 'assistant-wake');

    // 6. Started at login: tray only. A second launch brings the window forward.
    await app.close();
    page = await launch(['--background']);
    const visible = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter(w => w.webContents.getURL().endsWith('index.html')).map(w => w.isVisible())[0]);
    assert.equal(await visible(), false, 'a login start opens no window');
    assert.ok(await app.evaluate(() => !!globalThis.deskShell.tray), 'the tray icon is there');
    const executable = process.env.DESK_EXECUTABLE || require('electron');
    const again = spawn(executable, launchArgs([]).args, { env, stdio: 'ignore' });
    const code = await new Promise(resolve => again.on('exit', resolve));
    assert.equal(code, 0, 'the second instance exits');
    await until(visible, 'second launch shows the running window');
    console.log('PASS: tray-only handling of concurrent requests, notifications (waiting, done, tray hint), badge tooltip, jump to session, login item, assistant wake-up with handoff card, background start, single instance.');
  } finally { if (app) await app.close().catch(() => {}); }
})().catch(error => { console.error(error); process.exitCode = 1; });
