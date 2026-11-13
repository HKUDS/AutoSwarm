// AutoSwarm switch (0.9.0) with deterministic native peers and a scratch user home (no model calls,
// nothing written to the real ~/.claude or ~/.agents): Desk installs the Skill on launch, the new-session
// switch mentions it in the first turn or hides it, and a failed install is handed to an Agent.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const helpers = require('./helpers.cjs'), { python } = helpers;
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'swarm-')), home = path.join(temp, 'records'), workspace = path.join(temp, '工作 目录'), user = path.join(temp, 'user home');
for (const dir of [home, workspace, user]) fs.mkdirSync(dir);
// Codex's skill folder cannot be created: its install fails, Claude Code's succeeds.
fs.writeFileSync(path.join(user, '.agents'), 'a file where the skills folder should be');
const fixture = provider => ({ command: [python, path.join(root, 'tests/fixtures/provider.py'), provider] });
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { 'claude-code': fixture('claude-code'), codex: fixture('codex') } }));
// A packaged build runs on the Python it ships; a dev build uses the test interpreter.
const bundled = process.env.DESK_EXECUTABLE && path.join(path.dirname(process.env.DESK_EXECUTABLE), 'resources/python/runtime/python.exe');
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), USERPROFILE: user, HOME: user };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'CODEX_HOME', 'AUTOSWARM_DESK_PYTHON']) delete env[key];
if (!bundled) env.AUTOSWARM_DESK_PYTHON = python;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const shot = (page, name) => page.screenshot({ path: path.join(output, `swarm-${name}.png`) });
let app;
const until = (predicate, what, timeout = 60000) => helpers.until(predicate, what, timeout);
(async () => {
  try {
    app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
    const page = await app.firstWindow();
    await page.waitForFunction(() => !!window.desk);
    const frame = () => page.evaluate(() => window.desk.initial());
    await until(async () => (await frame())?.assistantSettings?.ready, 'Desk control');
    await app.evaluate(({ dialog }, workspace) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [workspace] }); }, workspace);
    const conversation = async prompt => {
      await page.locator('#session-input').fill(prompt); await page.locator('#session-input').press('Enter');
      const agent = await until(async () => { const f = await frame(); return Object.values(f.data.agents).find(a => a.call_id && f.data.calls[a.call_id]?.task.prompt === prompt); }, prompt);
      await page.waitForFunction(() => !document.querySelector('#session-form'), {}, { timeout: 30000 });
      return read(path.join(home, 'calls', agent.call_id, 'task.json'));
    };

    // 1. On launch Desk installs the Skill for agents found on this machine (the standard setup).
    const skills = await until(async () => { const s = (await frame()).skills; return s && !s.installing && s.errors && s; }, 'automatic install');
    assert.equal(skills.hosts['claude-code'].state, 'ready');
    assert.equal(skills.hosts.cursor.state, 'ready', 'Cursor reads the Claude Code copy');
    assert.equal(skills.hosts.codex.state, 'missing');
    assert.ok(skills.errors.codex?.message, 'The failed install keeps its error');
    const launcher = JSON.parse(/```json\s*(\[.*?\])\s*```/s.exec(fs.readFileSync(path.join(user, '.claude/skills/autoswarm/SKILL.md'), 'utf8'))[1]);
    assert.equal(path.resolve(launcher[0]).toLowerCase(), path.resolve(bundled || python).toLowerCase(), 'The Skill runs on the Python Desk runs on');
    assert.equal(Object.keys(read(path.join(user, '.claude/settings.json')).hooks).length, 3, 'Same result as installing through an agent');

    // 2. Ready: on by default; off hides the Skill from that conversation through its native args.
    await page.locator('aside .new-session').click();
    await page.locator('#session-agent').click(); await page.locator('[data-pick-agent="claude-code"]').click();
    await page.locator('[data-menu="workspace"]').click(); await page.locator('[data-action="session-workspace"]').click();
    assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-pressed'), 'true');
    const on = await conversation('with autoswarm');
    assert.equal(on.skill.name, 'autoswarm', 'On mentions the Skill in the first turn');
    assert.ok(fs.existsSync(on.skill.path));
    assert.deepEqual(on.provider_args, []);
    await page.locator('aside .new-session').click();
    await page.locator('#session-form .swarm-pill').click();
    assert.equal(await page.locator('#session-form .swarm-pill').getAttribute('aria-pressed'), 'false');
    await shot(page, 'off');
    const off = await conversation('without autoswarm');
    assert.deepEqual(off.provider_args, ['--settings', '{"skillOverrides": {"autoswarm": "off"}}']);
    assert.equal(off.skill, undefined);

    // Permission modes through Desk's IPC: the agent's Desk default, this conversation's choice, a later change.
    assert.equal(on.permission, 'acceptEdits', "Claude Code's Desk default");
    // Let both turns finish first: a frame re-rendering the page between mouse down and up drops a click.
    await until(() => [on, off].every(task => fs.existsSync(path.join(home, 'calls', task.call_id, 'result.json'))), 'the earlier turns');
    await page.evaluate(() => window.desk.permissions('claude-code', 'plan'));
    await page.locator('aside .new-session').click();
    const chip = () => page.locator('#session-form .permission-chip span').innerText();
    await until(async () => await chip() === 'Plan', 'the new default in the composer');
    await page.locator('#session-form .permission-chip').click(); await page.locator('[data-pick-permission="bypassPermissions"]').click();
    assert.equal(await chip(), 'Bypass Permissions');
    const chosen = await conversation('with bypass');
    assert.equal(chosen.permission, 'bypassPermissions', 'This conversation only');
    assert.deepEqual(chosen.command.slice(-2), ['--permission-mode', 'bypassPermissions']);
    const conversationRecord = () => { try { return read(path.join(home, 'agents', chosen.agent_id, 'agent.json')); } catch { return {}; } };
    assert.equal(conversationRecord().permissions['claude-code'], 'plan', 'Desk defaults travel with the conversation');
    await until(() => fs.existsSync(path.join(home, 'calls', chosen.call_id, 'result.json')), 'the bypass turn');
    await page.locator('.header-chips .permission-chip').click(); await page.locator('[data-pick-permission="default"]').click();
    await until(() => conversationRecord().permission === 'default', 'a change from the session header');

    // 3. Not installed: locked; the popover shows the error and hands the install to an Agent.
    await page.locator('aside .new-session').click();
    await page.locator('#session-agent').click(); await page.locator('[data-pick-agent="codex"]').click();
    await page.locator('#session-form .swarm-pill.is-locked').click();
    await page.waitForSelector('.swarm-pop .swarm-error');
    await shot(page, 'failure');
    await page.locator('.swarm-pop [data-action="skill-agent"]').click();
    const handed = await until(async () => { const f = await frame(); return Object.values(f.data.calls).find(c => /setup --host codex/.test(c.task.prompt)); }, 'install conversation');
    assert.equal(handed.task.provider, 'codex', 'The agent being set up installs its own Skill');
    assert.match(handed.task.prompt, /自动安装没有成功[\s\S]*错误：[\s\S]*skills --host codex --verify/);

    // 4. Once the cause is gone, the next launch installs it.
    fs.rmSync(path.join(user, '.agents'));
    await page.evaluate(() => window.desk.reconnect());
    await until(async () => (await frame()).skills?.hosts?.codex?.state === 'ready', 'Codex installed after reconnect');
    assert.ok(fs.existsSync(path.join(user, '.agents/skills/autoswarm/SKILL.md')));
    console.log('PASS: installed on launch, on mentions the Skill and off hides it per new conversation, permission default / per-conversation / header change, failed install handed to an Agent, retried on the next launch.');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally { if (app) await app.close(); }
})();
