// P4 · trigger rules and process records, with deterministic native
// peers (no model calls): a rules file that replaces, silences and adds
// notifications, hands a failure to the private assistant and sends a message
// to a finished session (once); rule errors and live reload; the process
// record capture mode, and how a turn reads when its record is gone.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'p4-')), home = path.join(temp, 'records'), workspace = path.join(temp, '规则 工作区');
const userData = path.join(temp, 'preferences'), rulesFile = path.join(userData, 'triggers.json');
fs.mkdirSync(home); fs.mkdirSync(workspace); fs.mkdirSync(userData);
const fixture = provider => ({ command: [python, path.join(root, 'tests/fixtures/provider.py'), provider] });
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': fixture('claude-code'), codex: fixture('codex') } }));
const rules = [
  { name: '审批写清会话', on: 'waiting', match: { provider: 'claude-code', kind: 'approval' }, do: 'notify', title: '{provider} 等你批准', text: '{session} · {detail}' },
  { name: '命令行会话不提醒', on: 'done', match: { owner: 'cli' }, do: 'silence' },
  { name: '失败请助理看看', on: 'failed', do: 'assistant', text: '「{session}」里的 {provider} 失败了，请看看原因。' },
  { name: '完成后自查', on: 'done', match: { owner: 'desk' }, do: 'message', text: '请自查「{session}」' },
  { name: '坏规则', on: 'later', do: 'silence' }
];
fs.writeFileSync(rulesFile, JSON.stringify({ rules }, null, 2));
// Fixture agents have no session records of their own, so this run keeps Desk's copy for every agent.
fs.writeFileSync(path.join(userData, 'preferences.json'), JSON.stringify({ captureAll: true }));
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: userData, AUTOSWARM_DESK_PYTHON: python, USERPROFILE: path.join(temp, 'user'), HOME: path.join(temp, 'user') };
fs.mkdirSync(path.join(temp, 'user'), { recursive: true });  // Desk installs the AutoSwarm Skill into the user home: keep it scratch.
delete env.ELECTRON_RUN_AS_NODE; delete env.AUTOSWARM_HOME;
const cliEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('AUTOSWARM_') && k !== 'CODEX_THREAD_ID'));
cliEnv.PYTHONPATH = path.join(root, 'src');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const shot = (page, name) => page.screenshot({ path: path.join(output, `p4-${name}.png`) });
const sleep = ms => new Promise(r => setTimeout(r, ms));
function cli(args, input = '') {
  const result = spawnSync(python, ['-m', 'autoswarm', '--home', home, ...args], { input, env: cliEnv, encoding: 'utf8', windowsHide: true, timeout: 30000 });
  const value = JSON.parse(result.stdout);
  if (!value.ok) throw Error(JSON.stringify(value.error));
  return value.data;
}
const notes = app => app.evaluate(() => globalThis.__notes.map(n => ({ title: n.title, body: n.body })));
const frame = page => page.evaluate(() => window.desk.initial());
const agentCalls = agent => fs.readdirSync(path.join(home, 'calls')).filter(c => fs.existsSync(path.join(home, 'calls', c, 'task.json'))).map(c => read(path.join(home, 'calls', c, 'task.json'))).filter(t => t.agent_id === agent);
let app;
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    await app.evaluate(({ Notification, shell }) => {
      globalThis.__notes = []; globalThis.__opened = [];
      Notification.prototype.show = function () { globalThis.__notes.push(this); };
      shell.openPath = async file => { globalThis.__opened.push(file); return ''; };
    });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    await until(async () => (await frame(page))?.assistantSettings?.ready, 'Desk control');
    const controller = (await frame(page)).data.desk.controller_id;
    // Notifications stay quiet while the user is looking at Desk; minimize it for the notification steps.
    const mainWindow = action => app.evaluate(({ BrowserWindow }, action) => BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('index.html'))[action](), action);

    // 1. The rules file is read at start; a broken rule is reported, the others work.
    const loaded = (await frame(page)).rules;
    assert.equal(loaded.active, 4);
    assert.equal(loaded.errors.length, 1);
    assert.match(loaded.errors[0], /第 5 条「坏规则」：on 应该是/);
    const assistant = cli(['--controller', controller, 'desk', 'assistant', '--agent', 'codex', '--cwd', workspace, '--input', '-'], '你好');
    cli(['wait', assistant.call_id]);
    await until(async () => (await frame(page)).assistantSettings.workspace === workspace, 'assistant workspace from its record');

    // 2. A request: the notify rule replaces the built-in notification.
    await mainWindow('minimize');
    const approved = path.join(workspace, 'approved.txt');
    const session = cli(['--controller', controller, 'desk', 'start', '--agent', 'claude-code', '--cwd', workspace, '--input', '-'], `approval:${approved}\n修改配置`);
    await until(async () => (await notes(app)).some(n => n.title === 'Claude Code 等你批准'), 'rule notification');
    assert.ok(!(await notes(app)).some(n => n.title === 'Claude Code 需要你决定'), 'the built-in one is replaced');
    assert.match((await notes(app)).find(n => n.title === 'Claude Code 等你批准').body, / · /);
    const request = await until(() => { const dir = path.join(home, 'calls', session.call_id, 'approvals'); return fs.existsSync(dir) && fs.readdirSync(dir)[0]; }, 'native request');
    cli(['approve', session.call_id, request, '--by', 'user']);

    // 3. It finishes: the message rule sends one follow-up to that session, and only one.
    await until(() => agentCalls(session.agent_id).length >= 2, 'rule message resumed the session');
    const mail = fs.readdirSync(path.join(home, 'agents', session.agent_id, 'messages')).map(m => read(path.join(home, 'agents', session.agent_id, 'messages', m, 'message.json')));
    assert.equal(mail.length, 1);
    assert.equal(mail[0].kind, 'user_message');
    assert.match(mail[0].content, /^\[Desk 规则「完成后自查」\] 请自查「.+」$/);
    await until(() => agentCalls(session.agent_id).every(t => fs.existsSync(path.join(home, 'calls', t.call_id, 'result.json'))), 'follow-up turn returned');
    await sleep(2500);
    assert.equal(agentCalls(session.agent_id).length, 2, 'the follow-up finishing does not trigger the rule again');
    assert.ok((await notes(app)).some(n => n.title === '会话已完成'), 'message rules do not silence the built-in notification');

    // 4. Command-line sessions: completion is silenced; a failure goes to the assistant.
    const before = (await notes(app)).length;
    const quiet = cli(['run', '--agent', 'codex', '--cwd', workspace, '--no-parent', '--input', '-'], 'hello from the command line')[0] || null;
    const failed = cli(['run', '--agent', 'codex', '--cwd', workspace, '--no-parent', '--input', '-'], 'fail');
    assert.equal(failed[0].outcome, 'failed');
    const woken = await until(() => {
      const agent = read(path.join(home, 'agents', assistant.agent_id, 'agent.json'));
      return agent.call_id !== assistant.call_id && read(path.join(home, 'calls', agent.call_id, 'task.json'));
    }, 'assistant call from the failure rule');
    assert.match(woken.prompt, /^\[Desk 规则「失败请助理看看」\] 「.*」里的 Codex 失败了，请看看原因。$/);
    await sleep(1500);
    const later = (await notes(app)).slice(before).map(n => n.title);
    assert.deepEqual(later, ['会话已结束，有失败'], `only the failure notifies (${quiet ? 'silenced completion' : ''})`);
    await mainWindow('restore');
    await page.locator('[data-action="overview"]').first().click();
    await page.waitForSelector('.system-note');
    assert.match(await page.locator('.system-note').last().innerText(), /Desk 规则「失败请助理看看」/);

    // 5. Settings: rule status, the firing log, and live reload.
    await page.locator('[data-action="settings"]').first().click();
    const section = page.locator('#settings .rules-settings');
    assert.match(await section.locator('> label').innerText(), /触发规则 · 4 条生效/);
    assert.match(await section.locator('.rule-errors').innerText(), /坏规则/);
    await until(async () => /失败请助理看看 · 交给私人助理.*已发送/s.test(await section.locator('.rule-log').innerText()), 'assistant rule logged as sent');
    const log = await section.locator('.rule-log').innerText();
    assert.match(log, /完成后自查 · 发给会话[\s\S]*已发送/);
    assert.match(log, /审批写清会话 · 通知[\s\S]*已通知/);
    assert.match(log, /命令行会话不提醒 · 静音内置通知[\s\S]*已静音/);
    await shot(page, 'settings-rules');
    fs.writeFileSync(rulesFile, JSON.stringify({ rules: rules.slice(0, 4).map(rule => ({ ...rule, enabled: false })) }, null, 2));
    await until(async () => /0 条生效 · 4 条停用/.test(await section.locator('> label').innerText()), 'rules reloaded after save');
    assert.equal(await section.locator('.rule-errors').count(), 0);
    await section.locator('[data-action="open-rules"]').click();
    assert.deepEqual(await app.evaluate(() => globalThis.__opened), [rulesFile]);

    // 6. Process records: Claude Code, Codex and Cursor are read from their own records; Desk keeps a copy only when asked.
    const events = page.locator('#settings .events-settings');
    const heading = events.locator('> label:not(.check-row)');
    assert.match(await heading.innerText(), /全部由 Desk 另存一份/);
    assert.equal(read(path.join(home, 'config.json')).events.capture, true, '"also keep a copy" turns capture on');
    assert.equal(await page.locator('#settings [data-events], #settings [data-action="prune-events"]').count(), 0, 'no deletion settings');
    await shot(page, 'settings-events');
    await events.locator('[data-pref="captureAll"]').uncheck();
    await until(() => read(path.join(home, 'config.json')).events.capture === 'auto', 'copy only for agents without their own records');
    await until(async () => /其他 Agent 由 Desk 保存/.test(await heading.innerText()), 'capture mode shown');
    await page.locator('[data-close="settings"]').click();

    // 7. A turn whose agent record and copy are both gone says so; the later turn shows its copy.
    const first = path.join(home, 'calls', session.call_id);
    fs.rmSync(path.join(first, 'events.jsonl'));
    await app.evaluate(({ BrowserWindow }, id) => BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('index.html')).webContents.send('desk:navigate', { session: id }), session.agent_id);
    await page.waitForSelector('.stream-fallback.is-missing', { timeout: 15000 });
    assert.match(await page.locator('.stream-fallback.is-missing').innerText(), /未找到 Claude Code 自己的会话记录（可能已被它清理），最终回复仍保留/);
    assert.ok(await page.locator('.process-stream').count(), 'the follow-up turn still shows its kept copy');
    assert.ok(await page.locator('.system-note', { hasText: 'Desk 规则「完成后自查」' }).count(), 'the rule message reads as a system note in the session');
    await shot(page, 'missing-turn');

    // 8. A fresh install: "create rules file" writes the template with every example disabled.
    fs.rmSync(rulesFile);
    await until(async () => !(await frame(page)).rules.exists, 'rules file removed');
    await page.locator('[data-action="settings"]').first().click();
    await page.locator('#settings [data-action="open-rules"]', { hasText: '创建规则文件' }).click();
    await until(async () => (await frame(page)).rules.exists, 'template created');
    const created = (await frame(page)).rules;
    assert.deepEqual([created.active, created.disabled, created.errors.length], [0, 4, 0]);
    console.log('PASS: rules file (notify replaces, silence, assistant hand-off, one message per session, errors, live reload, create template), capture mode switch, a turn whose record is gone.');
  } finally { if (app) await app.close().catch(() => {}); }
})().catch(error => { console.error(error); process.exitCode = 1; });
