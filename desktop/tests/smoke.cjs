const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { spawn, spawnSync } = require('node:child_process');
const { python } = require('./helpers.cjs');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.tmp-test/desk-ui');
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, 'run-'));
const env = { ...process.env, AUTOSWARM_DESK_PYTHON: python, AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA: path.join(temp, 'preferences'), USERPROFILE: path.join(temp, 'user'), HOME: path.join(temp, 'user') };
fs.mkdirSync(path.join(temp, 'user'), { recursive: true });  // Desk installs the AutoSwarm Skill into the user home: keep it scratch.
delete env.ELECTRON_RUN_AS_NODE;
delete env.AUTOSWARM_HOME;
let application, lockProcess;
const failures = [];
async function launch(args) {
  application = await electron.launch({ args: [path.join(root, 'desktop'), ...args], env,
    ...(process.env.DESK_EXECUTABLE ? { executablePath: process.env.DESK_EXECUTABLE } : {}) });
  const page = await application.firstWindow();
  page.on('pageerror', error => failures.push(error.message));
  await page.waitForSelector('.session-item', { timeout: 30000 });
  return page;
}
function write(relative, value) {
  const target = path.join(temp, 'records 中文', relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target + '.tmp', JSON.stringify(value));
  fs.renameSync(target + '.tmp', target);
}
(async () => {
  try {
    let page = await launch(['--demo']);
    await page.locator('aside [data-session="call_01"]').click();
      if(await page.locator('.dock-heading').getAttribute('aria-expanded')==='false')await page.locator('.dock-heading').click();
    await page.locator('[data-worker="call_05"]').click();
    assert.match(await page.locator('.worker-expanded').innerText(), /认证接口文档已更新/);
    await page.locator('[data-detail="call_05"]').click();
    await page.waitForSelector('.worker-scroll');
    assert.match(await page.locator('.worker-scroll').innerText(), /次工具调用/);
    await page.screenshot({ path: path.join(output, 'worker-p1.png') });
    await page.locator('[data-action="worker-back"]').click();
    await page.locator('[data-worker="call_05"]').click();
    await page.screenshot({ path: path.join(output, 'session.png') });
    await page.locator('aside [data-action="overview"]').click();
    assert.equal(await page.locator('.decision-card').count(), 2);
    await page.screenshot({ path: path.join(output, 'overview.png') });
    await page.locator('[data-session="call_09"]').first().click();
    assert.match(await page.locator('.markdown').innerText(), /Q3 数据已整理完成/);
    await page.screenshot({ path: path.join(output, 'completed.png') });
    await page.locator('[data-action="settings"]').first().click();
    await page.waitForSelector('#settings[open]');
    await page.locator('[data-close="settings"]').click();
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(640, 720));
    await page.locator('aside [data-session="call_01"]').click();
      if(await page.locator('.dock-heading').getAttribute('aria-expanded')==='false')await page.locator('.dock-heading').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, 'narrow.png') });
    await application.close(); application = null;

    const home = path.join(temp, 'records 中文');
    const init = spawnSync(python,['-m','autoswarm','--home',home,'desk','init'],{env:{...env,PYTHONPATH:path.join(root,'src')},encoding:'utf8',windowsHide:true});
    assert.equal(init.status,0,init.stderr);
    const controller = JSON.parse(init.stdout).data.controller_id;
    write('calls/call_aa/task.json', { call_id: 'call_aa', agent_id: 'call_aa', provider: 'codex', prompt: '真实目录连接验收', workspace: root, created_at: '2026-11-01T09:00:00Z' });
    write('agents/call_aa/agent.json', { agent_id: 'call_aa', call_id: 'call_aa', name: '只读验收', owner_id: controller });
    write('calls/call_aa/handle.json', { phase: 'execution', runner_lock:path.join(home,'calls/call_aa/runner.lock') });
    const script = 'from pathlib import Path; from autoswarm.platform import session_lease; import sys; lock=session_lease(Path(sys.argv[1])); print("ready",flush=True); sys.stdin.readline()';
    lockProcess = spawn(python, ['-u', '-c', script, path.join(home, 'calls/call_aa/runner.lock')], { env: { ...env, PYTHONPATH: path.join(root, 'src') }, windowsHide: true });
    await new Promise((resolve, reject) => { lockProcess.stdout.once('data', resolve); lockProcess.once('error', reject); });
    page = await launch(['--home=' + home]);
    await page.locator('aside [data-session="call_aa"]').waitFor();
    await page.locator('aside [data-session="call_aa"]').click();
    await page.waitForSelector('.working-line .state.running');
    write('calls/call_aa/events.jsonl', null);
    const eventPath = path.join(home, 'calls/call_aa/events.jsonl');
    fs.writeFileSync(eventPath, JSON.stringify({method:'item/started',params:{item:{id:'tool-live',type:'commandExecution',command:'读取测试文件',status:'inProgress'}}})+'\n');
    await page.waitForFunction(() => document.querySelector('.process-stream')?.textContent.includes('读取测试文件'));
    fs.writeFileSync(eventPath, Array.from({length:161},(_,i)=>JSON.stringify({method:'item/started',params:{item:{id:'tool-'+i,type:'commandExecution',command:'读取测试文件 '+i,status:'inProgress'}}})).join('\n')+'\n');
    await page.waitForFunction(()=>document.querySelectorAll('.process-entry').length === 80);
    // A running turn shows its steps in the open; consecutive tool calls fold into one batch row.
    await page.locator('.live-steps .step-batch').first().evaluate(el=>el.open=true);
    assert.match(await page.locator('.process-label strong').first().textContent(), /读取测试文件 81$/);
    await page.locator('.conversation-scroll').evaluate(el=>{el.scrollTop=1;el.dispatchEvent(new WheelEvent('wheel',{bubbles:true,deltaY:-100}));el.dispatchEvent(new Event('scroll'));});
    await page.waitForFunction(()=>document.querySelector('.process-label strong')?.textContent === '读取测试文件 1');
    assert.equal(await page.locator('.process-entry').count(),80);
    await page.waitForTimeout(150); // Allow the scroll-anchor guard to release.
    await page.locator('.conversation-scroll').evaluate(el=>{el.scrollTop=el.scrollHeight;el.dispatchEvent(new WheelEvent('wheel',{bubbles:true,deltaY:100}));});
    await page.waitForFunction(()=>document.querySelector('.process-label strong')?.textContent === '读取测试文件 81');
    assert.equal(await page.locator('.process-entry').count(),80);
    const questionId='approval_'+'d'.repeat(32);
    write(`calls/call_aa/approvals/${questionId}/request.json`,{kind:'interaction_required',user_required:true,response_format:'claude-question',native_request:{input:{questions:[{question:'如何处理？'}]}}});
    await page.locator('.native-question-form textarea').fill('保留我的输入');
    write('calls/call_aa/handle.json',{phase:'startup',runner_lock:path.join(home,'calls/call_aa/runner.lock')});
    await page.waitForFunction(()=>document.querySelector('.working-line')?.textContent.includes('正在启动'));
    assert.equal(await page.locator('.native-question-form textarea').inputValue(),'保留我的输入');
    await page.locator('.native-question-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('.native-question-form'));
    const answer=JSON.parse(fs.readFileSync(path.join(home,`calls/call_aa/approvals/${questionId}/decision.json`),'utf8'));
    assert.equal(answer.actor,'user');assert.equal(answer.response.answers['如何处理？'],'保留我的输入');
    write('calls/call_aa/approvals/approval_aa/request.json', { kind: 'approval_required', title: '测试批准请求', user_required: true });
    await page.waitForSelector('.decision-card');
    write('calls/call_aa/approvals/approval_aa/decision.json', { actor: 'user', decision: 'allow' });
    await page.waitForFunction(() => !document.querySelector('.decision-card'));
    write('calls/call_aa/result.json', { outcome: 'completed', content: '## 最终结果\n\n**连接测试通过**\n<script>window.pwned=true</script><img src=x onerror="window.pwned=true">' });
    await page.waitForSelector('.markdown');
    assert.match(await page.locator('.markdown').innerText(), /连接测试通过/);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.equal(await page.locator('.markdown img').count(), 0);
    await application.close(); application = null;
    delete env.AUTOSWARM_DESK_PYTHON;
    page = await launch([]);
    await page.locator('aside [data-session="call_aa"]').waitFor();
    await page.locator('aside [data-session="call_aa"]').click();
    await page.waitForSelector('.markdown');
    await page.waitForFunction(() => document.querySelector('.process-stream')?.textContent.includes('读取测试文件'));
    assert.match(await page.locator('.markdown').innerText(), /连接测试通过/);
    assert.equal(fs.existsSync(path.join(home, 'calls/call_aa/received.json')), false);
    assert.deepEqual(failures, []);
    console.log('PASS: demo, nested workers, overview, completion, notices, responsive layout, real watch, approvals, sanitized Markdown, restart.');
  } finally {
    if (application) await application.close();
    if (lockProcess) { lockProcess.stdin.end('\n'); lockProcess.kill(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
