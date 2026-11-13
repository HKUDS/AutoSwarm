const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path'), fs = require('node:fs');
const { python } = require('./helpers.cjs');
const root=path.resolve(__dirname,'../..'), output=path.join(root,'.tmp-test/desk-ui');
fs.mkdirSync(output,{recursive:true});
const temp=fs.mkdtempSync(path.join(output,'assistant-')), home=path.join(temp,'records'), workspace=path.join(temp,'workspace');
fs.mkdirSync(home);fs.mkdirSync(workspace);
fs.writeFileSync(path.join(home,'config.json'),JSON.stringify({providers:{codex:{command:[python,path.join(root,'tests/fixtures/provider.py'),'codex']},'claude-code':{command:[python,path.join(root,'tests/fixtures/provider.py'),'claude-code']}}}));
const env={...process.env,AUTOSWARM_DESK_LANGUAGE: 'zh', AUTOSWARM_DESK_USER_DATA:path.join(temp,'preferences'),AUTOSWARM_DESK_PYTHON:python, USERPROFILE: path.join(temp, 'user'), HOME: path.join(temp, 'user') };
fs.mkdirSync(path.join(temp, 'user'), { recursive: true });  // Desk installs the AutoSwarm Skill into the user home: keep it scratch.
delete env.ELECTRON_RUN_AS_NODE;delete env.AUTOSWARM_HOME;
let app;
async function waitState(page, predicate) {
  const deadline=Date.now()+30000;
  await page.waitForFunction(()=>!!window.desk);
  while(Date.now()<deadline){
    const state=await page.evaluate(()=>window.desk.initial());
    if(predicate(state))return state;
    await page.waitForTimeout(50);
  }
  throw Error('Timed out waiting for desktop state');
}
async function open(){
  app=await electron.launch({args:[path.join(root,'desktop'),'--home='+home],env,...(process.env.DESK_EXECUTABLE?{executablePath:process.env.DESK_EXECUTABLE}:{})});
  const page=await app.firstWindow();
  await waitState(page,state=>state?.assistantSettings?.ready);
  return page;
}
function write(relative,value){const p=path.join(home,relative);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p+'.tmp',JSON.stringify(value));fs.renameSync(p+'.tmp',p);}
(async()=>{
  try{
    let page=await open();
    assert.equal(await page.locator('h1').innerText(),'私人助理');
    await app.evaluate(({dialog},workspace)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[workspace]});},workspace);
    await page.locator('[data-action="settings"]').first().click();
    await page.locator('[data-action="choose-workspace"]').click();
    await waitState(page,state=>state?.assistantSettings.workspace===workspace);
    await page.locator('#assistant-agent').click();
    await page.locator('[data-pick-agent="codex"][data-ctx="assistant"]').click();
    await page.locator('#assistant-settings-form [type=submit]').click();
    await waitState(page,state=>state?.assistantSettings.provider==='codex');
    await page.locator('[data-close=settings]').click();
    await page.locator('#assistant-input').fill('你好，META_FIRST');
    await page.locator('#assistant-form .send-button').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.markdown')].some(e=>e.textContent.includes('META_FIRST')),{},{timeout:30000}).catch(async error=>{console.log('Send failure:',await page.locator('#toast,.error-text').allTextContents());throw error;});
    const first=await page.evaluate(async()=> (await window.desk.initial()).data.desk);
    assert.ok(first.assistant_agent_id);
    assert.equal(await page.locator('aside .session-item').count(),0);
    await page.waitForFunction(()=>!document.querySelector('#assistant-form .send-button').disabled);
    await page.locator('#assistant-input').fill('继续，META_SECOND');
    await page.locator('#assistant-form .send-button').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.markdown')].some(e=>e.textContent.includes('META_SECOND')));
    const second=await page.evaluate(async()=> (await window.desk.initial()).data.desk);
    assert.equal(second.assistant_agent_id,first.assistant_agent_id);
    assert.notEqual(second.assistant_call_id,first.assistant_call_id);
    const main='call_'+'a'.repeat(32),worker='call_'+'b'.repeat(32);
    for(const [id,owner,name] of [[main,first.assistant_agent_id,'真正的主会话'],[worker,main,'只在子任务区的 Worker']]){
      write(`agents/${id}/agent.json`,{agent_id:id,call_id:id,owner_id:owner,name});
      write(`calls/${id}/task.json`,{call_id:id,agent_id:id,controller_id:owner,provider:'codex',prompt:name,workspace,created_at:new Date().toISOString()});
      write(`calls/${id}/result.json`,{outcome:'completed',content:'测试结果',native_session_id:'fixture-session'});
    }
    await page.waitForSelector(`aside [data-session="${main}"]`);
    assert.equal(await page.locator('aside .session-item').count(),1);
    assert.equal(await page.locator(`aside [data-session="${worker}"]`).count(),0);
    await page.locator(`aside [data-session="${main}"]`).click();
    await page.locator('.dock-heading').click();
    assert.equal(await page.locator(`[data-worker="${worker}"]`).count(),1);
    await page.locator('#chat-input').fill('MAIN_DIRECT_FROM_UI');
    await page.locator('#chat-form [type=submit]').click();
    await waitState(page,state=>Object.values(state.data.calls).some(c=>c.agent_id===main && c.result?.content.includes('MAIN_DIRECT_FROM_UI')));
    const mainCall=await page.evaluate(async main=>(await window.desk.initial()).data.agents[main].call_id,main);
    assert.notEqual(mainCall,main);
    await page.locator(`[data-worker="${worker}"]`).click();
    await page.locator(`[data-detail="${worker}"]`).click();
    assert.equal(await page.locator('.worker-sidebar [aria-current=page]').count(),1);
    await page.locator('#chat-input').fill('WORKER_DIRECT_FROM_UI');
    await page.locator('#chat-form [type=submit]').click();
    await waitState(page,state=>Object.values(state.data.calls).some(c=>c.agent_id===worker && c.result?.content.includes('WORKER_DIRECT_FROM_UI')));
    const workerOwner=await page.evaluate(async worker=>(await window.desk.initial()).data.agents[worker].owner_id,worker);
    assert.equal(workerOwner,main);
    await page.locator('[data-action=worker-back]').click();
    assert.equal(await page.locator('aside .session-item').count(),1);
    await page.locator('aside [data-action=overview]').click();
    await page.screenshot({path:path.join(output,'assistant-functional.png')});
    await app.close();app=null;
    fs.unlinkSync(path.join(temp,'preferences/preferences.json'));
    page=await open();
    const restored=await page.evaluate(async()=>(await window.desk.initial()).assistantSettings);
    assert.equal(restored.provider,'codex');assert.equal(restored.workspace,workspace);
    assert.equal(restored.binding.assistant_agent_id,first.assistant_agent_id);
    await page.locator('#assistant-input').fill('恢复后继续，META_RESTORED');
    await page.locator('#assistant-form .send-button').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.markdown')].some(e=>e.textContent.includes('META_RESTORED')));
    // The assistant changes agent; each agent keeps its own assistant conversation.
    await page.waitForFunction(()=>!document.querySelector('#assistant-form .send-button').disabled);
    await page.locator('aside [data-action="settings"]').click();
    await page.waitForSelector('#settings[open]');
    assert.equal(await page.locator('#assistant-agent').isDisabled(),false,'The agent can be changed after a conversation exists');
    await page.locator('#assistant-agent').click();
    await page.locator('[data-pick-agent="claude-code"][data-ctx="assistant"]').click();
    await waitState(page,state=>state?.assistantSettings.provider==='claude-code' && state.data.desk.assistant_agent_id===null);
    await page.locator('[data-close=settings]').click();
    await page.locator('#assistant-input').fill('换到 Claude，META_SWITCHED');
    await page.locator('#assistant-form .send-button').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.markdown')].some(e=>e.textContent.includes('META_SWITCHED')),{},{timeout:30000});
    const switched=(await waitState(page,state=>state?.data.desk.assistants?.length===2)).data.desk;
    assert.notEqual(switched.assistant_agent_id,first.assistant_agent_id);
    const told=JSON.parse(fs.readFileSync(path.join(home,'agents',switched.assistant_agent_id,'agent.json'),'utf8')).rules;
    assert.ok(told.includes(first.assistant_agent_id) && told.includes('desk transcript') && told.includes('你好，META_FIRST'),'The new agent is told about the earlier conversation');
    assert.equal(await page.locator('aside .session-item').count(),1,'Neither assistant conversation is a sidebar session');
    await page.locator('aside [data-action="settings"]').click();
    await page.waitForSelector('#settings[open]');
    assert.equal(await page.locator('.assistant-row').count(),2);
    assert.equal(await page.locator('.assistant-row.is-current [data-provider], .assistant-row.is-current').first().getAttribute('data-assistant-switch'),'claude-code');
    await page.locator('#settings').screenshot({path:path.join(output,'assistant-conversations.png')});
    await page.locator('.assistant-row[data-assistant-switch="codex"]').click();
    await waitState(page,state=>state?.assistantSettings.provider==='codex' && state.data.desk.assistant_agent_id===first.assistant_agent_id);
    await page.locator('[data-close=settings]').click();
    await page.locator('#assistant-input').fill('回到 Codex，META_BACK');
    await page.locator('#assistant-form .send-button').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.markdown')].some(e=>e.textContent.includes('META_BACK')),{},{timeout:30000});
    assert.equal((await waitState(page,state=>state?.data.desk)).data.desk.assistant_agent_id,first.assistant_agent_id,'Switching back continues the same conversation');
    console.log('PASS: Meta send, same-agent resume, main-only sidebar, nested Worker, direct main and Worker messages, same native sessions, preferences-loss recovery, changing agent keeps each assistant conversation.');
  }finally{if(app)await app.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
