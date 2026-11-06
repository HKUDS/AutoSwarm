// P2 · operating from Desk with deterministic native peers (no model calls):
// new conversation (provider/model/effort/workspace) → approval → completion →
// continue (resume) → deny → stop. Every write goes through the AutoSwarm CLI.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'p2-')), home = path.join(temp, 'records'), workspace = path.join(temp, '工作 目录');
fs.mkdirSync(home); fs.mkdirSync(workspace);
const fixture = provider => ({ command: [python, path.join(root, 'tests/fixtures/provider.py'), provider] });
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': fixture('claude-code'), codex: fixture('codex') } }));
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), AUTOSWARM_DESK_PYTHON: python, USERPROFILE: path.join(temp, 'user'), HOME: path.join(temp, 'user') };
fs.mkdirSync(path.join(temp, 'user'), { recursive: true });  // Desk installs the AutoSwarm Skill into the user home: keep it scratch.
delete env.ELECTRON_RUN_AS_NODE; delete env.AUTOSWARM_HOME;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const task = id => read(path.join(home, 'calls', id, 'task.json'));
const result = id => { try { return read(path.join(home, 'calls', id, 'result.json')); } catch { return null; } };
const shot = (page, name) => page.screenshot({ path: path.join(output, `p2-${name}.png`) });
let app;
async function waitState(page, predicate, what) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const state = await page.evaluate(() => window.desk.initial());
    if (predicate(state)) return state;
    await page.waitForTimeout(50);
  }
  throw Error('Timed out waiting for ' + what);
}
async function start(page, prompt) {
  await page.locator('aside .new-session').click();
  await page.locator('#session-input').fill(prompt);
  await page.locator('#session-input').press('Enter');
  await page.waitForFunction(() => document.querySelector('.page-heading h1')?.textContent && !document.querySelector('#session-form'), {}, { timeout: 30000 })
    .catch(async error => { console.log('Start failure:', await page.locator('#session-form .chat-error, #toast').allTextContents()); throw error; });
  const state = await page.evaluate(() => window.desk.initial());
  const controller = state.data.desk.controller_id;
  const agent = Object.values(state.data.agents).filter(a => a.owner_id === controller && a.call_id && a.name !== 'desk-assistant')
    .find(a => state.data.calls[a.call_id]?.task.prompt === prompt);
  assert.ok(agent, 'the new conversation is recorded under the Desk identity');
  return { agent: agent.agent_id, call: agent.call_id, controller };
}
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    await waitState(page, s => s?.assistantSettings?.ready, 'Desk control');
    await app.evaluate(({ dialog }, workspace) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [workspace] }); }, workspace);

    // 1. New conversation: native choices, workspace, then an approval that needs the user.
    await page.locator('aside .new-session').click();
    assert.equal(await page.locator('.home-title h2').innerText(), '开始一项新工作');
    await waitState(page, s => s.available?.['claude-code']?.available === true, 'installed CLI check');
    // DeepSeek-style chips: working directory and main agent above the composer.
    await page.locator('[data-menu="workspace"]').click();
    await page.locator('.menu-pop [data-action="session-workspace"]').click();
    await waitState(page, s => s.sessionDefaults?.workspace === workspace, 'chosen workspace');
    await page.locator('[data-menu="agent"]').click();
    await page.locator('[data-pick-agent="claude-code"]').click();
    // The fixture agent reports no catalog, so the model menu offers free text.
    await page.locator('[data-menu="model"]').click();
    await page.locator('#session-model').fill('fixture-model', { timeout: 30000 });
    await page.locator('#session-effort').fill('high');
    await page.keyboard.press('Escape');
    assert.match(await page.locator('#session-model-chip').innerText(), /fixture-model\s*high/i);
    await shot(page, 'new-session');
    const marker = path.join(temp, 'approved.txt');
    const first = await start(page, `approval:${marker}`);
    const firstTask = task(first.call);
    assert.equal(firstTask.controller_id, first.controller);
    assert.deepEqual([firstTask.provider, firstTask.model, firstTask.effort, firstTask.workspace, firstTask.delegate_approvals], ['claude-code', 'fixture-model', 'high', workspace, false]);
    assert.match(firstTask.communication.rules, /started directly in AutoSwarm Desk/);
    await page.waitForSelector('.decision-card [data-decision="approve"]', { timeout: 30000 });
    assert.match(await page.locator('.decision-card').innerText(), /需要你决定/);
    assert.equal(await page.locator('.chat-composer .stop-button').count(), 1, 'a running conversation can be stopped from its composer');
    assert.match(await page.locator('.page-heading .header-chips').innerText(), /fixture-model · high/);
    await shot(page, 'approval');
    await page.locator('.decision-card [data-decision="approve"]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.turn-reply')].some(e => e.textContent.includes('approved')), {}, { timeout: 30000 });
    assert.equal(fs.readFileSync(marker, 'utf8'), 'executed');
    const decision = fs.readdirSync(path.join(home, 'calls', first.call, 'approvals')).map(id => read(path.join(home, 'calls', first.call, 'approvals', id, 'decision.json')))[0];
    assert.equal(decision.actor, 'user');
    assert.equal(await page.locator('.chat-composer .stop-button').count(), 0);

    // 2. Continue the same conversation: a resume under the same agent and native session.
    await page.locator('#chat-input').fill('继续，P2_SECOND');
    await page.locator('#chat-input').press('Enter');
    await page.waitForFunction(() => [...document.querySelectorAll('.turn-reply')].some(e => e.textContent.includes('P2_SECOND')), {}, { timeout: 30000 });
    const agent = read(path.join(home, 'agents', first.agent, 'agent.json'));
    assert.notEqual(agent.call_id, first.call);
    assert.equal(task(agent.call_id).native_session_id, 'fixture-session');
    assert.equal(task(agent.call_id).agent_id, first.agent);
    await shot(page, 'continued');

    // 3. Deny a file write: the card says which file and what content; the tool does not run.
    const deniedMarker = path.join(workspace, 'denied.txt');
    await start(page, `write:${deniedMarker}`);
    await page.waitForSelector('.decision-card [data-decision="deny"]', { timeout: 30000 });
    const card = (await page.locator('.decision-card').innerText()).replace(/\s+/g, ' ');
    assert.match(card, /写入文件 denied\.txt/, 'the target is shown relative to the workspace');
    assert.match(card, /FIXTURE_WRITE/, 'the written content is previewed');
    await shot(page, 'write-card');
    await page.locator('.decision-card [data-decision="deny"]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.turn-reply')].some(e => e.textContent.includes('denied')), {}, { timeout: 30000 });
    assert.equal(fs.existsSync(deniedMarker), false);

    // 4. Stop: confirm, then the runner ends the turn as cancelled; the result stays readable.
    const held = await start(page, `gate:${path.join(temp, 'never')}\nP2_HOLD`);
    await page.waitForSelector('.chat-composer .stop-button', { timeout: 30000 });
    await page.locator('.chat-composer .stop-button').click();
    await page.waitForSelector('#detail [data-confirm-stop]');
    assert.match(await page.locator('#detail').innerText(), /会结束它当前这一轮/);
    await shot(page, 'confirm-stop');
    const stopClicked = Date.now();
    await page.locator('#detail [data-confirm-stop]').click();
    await until(() => result(held.call)?.outcome === 'cancelled', 'cancelled result');
    await page.waitForFunction(() => document.querySelector('.turn-note')?.textContent.includes('已停止'), {}, { timeout: 30000 });
    const stopLatency = Date.now() - stopClicked;
    assert.ok(stopLatency < 8000, 'the stop shows within seconds: ' + stopLatency + 'ms');
    assert.equal(await page.locator('.conversation-scroll .error-text').count(), 0, 'a deliberate stop is not shown as a failure');
    assert.equal(await page.locator('.chat-composer .stop-button').count(), 0);
    assert.match(await page.locator(`aside [data-session="${held.agent}"]`).innerText(), /P2_HOLD|gate/);
    await shot(page, 'stopped');
    console.log(`PASS: new session (provider/model/effort/workspace), user approval, completion, resume, deny, stop (shown after ${stopLatency} ms) — all through the AutoSwarm CLI.`);
  } finally { if (app) await app.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
