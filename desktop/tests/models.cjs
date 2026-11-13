// Model lists stay current: a fake ACP agent reads its models from a file the test changes. The menu
// shows the last list at once; Refresh reads it again; a restarted Desk reads it again in the background.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { python, until } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..'), output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'models-')), home = path.join(temp, 'records'), user = path.join(temp, 'user');
for (const dir of [home, user]) fs.mkdirSync(dir);
const list = path.join(temp, 'models.json'), agent = path.join(temp, 'fake_acp.py');
const models = names => fs.writeFileSync(list, JSON.stringify(names));
fs.writeFileSync(agent, [
  'import json, sys',
  'names = json.load(open(sys.argv[1], encoding="utf-8"))',
  'for line in sys.stdin:',
  '    value = json.loads(line)',
  '    method, rid = value.get("method"), value.get("id")',
  '    if method == "initialize":',
  '        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}',
  '    elif method == "session/new":',
  '        result = {"sessionId": "s", "configOptions": [{"id": "model", "category": "model", "currentValue": names[0], "options": [{"value": n, "name": n} for n in names]}]}',
  '    else:',
  '        continue',
  '    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)',
].join('\n'));
fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ providers: { kimi: { command: [python, agent, list] } } }));
const bundled = process.env.DESK_EXECUTABLE && path.join(path.dirname(process.env.DESK_EXECUTABLE), 'resources/python/runtime/python.exe');
const env = { ...process.env, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), USERPROFILE: user, HOME: user };
for (const key of ['ELECTRON_RUN_AS_NODE', 'AUTOSWARM_HOME', 'CODEX_HOME', 'AUTOSWARM_DESK_PYTHON']) delete env[key];
if (!bundled) env.AUTOSWARM_DESK_PYTHON = python;
let app;
async function open() {
  app = await electron.launch({ args: [path.join(root, 'desktop'), '--home=' + home], env, ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
  const page = await app.firstWindow();
  await page.waitForFunction(() => !!window.desk);
  await until(async () => (await page.evaluate(() => window.desk.initial()))?.assistantSettings?.ready, 'Desk control');
  await page.locator('aside .new-session').click();
  await page.locator('#session-agent').click();
  await page.locator('[data-pick-agent="kimi"]').click();
  return page;
}
const shown = page => page.locator('.model-menu [data-pick-model]').evaluateAll(rows => rows.map(r => r.dataset.pickModel).filter(Boolean));
async function menu(page) {
  if (!await page.locator('.model-menu').count()) await page.locator('#session-model-chip').click();
  await page.waitForSelector('.model-menu', { timeout: 30000 });
}
(async () => {
  try {
    models(['model-a']);
    let page = await open();
    await menu(page);
    assert.deepEqual(await shown(page), ['model-a']);
    assert.match(await page.locator('.model-menu .menu-refresh').innerText(), /读取 · 刷新/);
    // A model configured later: Refresh reads it, the open menu shows it.
    models(['model-a', 'model-b']);
    await page.locator('.model-menu .menu-refresh').click();
    await until(async () => (await shown(page)).includes('model-b'), 'model-b after Refresh');
    assert.match(await page.locator('.model-menu .menu-refresh').innerText(), /刚刚读取/);
    await page.screenshot({ path: path.join(output, 'models-refresh.png') });
    await app.close(); app = null;
    // After a restart the saved list shows at once; the background read brings the newer one in.
    models(['model-a', 'model-b', 'model-c']);
    page = await open();
    await menu(page);
    const first = await shown(page);
    assert.ok(first.includes('model-b'), 'the saved list is shown at once');
    await until(async () => (await shown(page)).includes('model-c'), 'model-c read in the background', 60000);
    console.log(`PASS: last list at once (${first.join(', ')}), Refresh reads a newly configured model, a restarted Desk reads the list again in the background.`);
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    await app?.close().catch(() => {});
  }
})();
