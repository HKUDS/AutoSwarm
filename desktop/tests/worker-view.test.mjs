import test from 'node:test';
import assert from 'node:assert/strict';
import { elapsed, workerPreview, workerStatus } from '../renderer/worker-view.mjs';
import { setLanguage } from '../renderer/i18n.mjs';
setLanguage('zh');  // These assertions read Desk's Chinese source text.

function node(values = {}) {
  return { id: 'call_worker', provider: 'codex', state: 'running', requests: [], calls: [], latest: { id: 'call_worker', state: 'running', task: { prompt: '完整任务说明', created_at: '2026-11-01T10:00:00Z' } }, ...values };
}

test('tool updates merge into accurate per-turn capsule counts with details folded', () => {
  const events = [
    {kind:'tool',id:'a',title:'read a.py',tool:'read',status:'in_progress',seq:'0:0'},
    {kind:'tool',id:'a',status:'completed',output:'LARGE OUTPUT MUST NOT BE IN PREVIEW',seq:'20:0'},
    {kind:'tool',id:'b',title:'edit a.py',tool:'edit',status:'completed',seq:'40:0'},
    {kind:'thought',text:'PRIVATE PROCESS DETAIL',seq:'60:0'},
    {kind:'message',text:'已完成签发和校验。',seq:'80:0'},
    {kind:'turn',seq:'100:0'}
  ];
  const html=workerPreview(node(),{events});
  assert.match(html,/2 次工具调用/);
  assert.match(html,/读 1 · 改 1/);
  assert.match(html,/已完成签发和校验/);
  assert.doesNotMatch(html,/LARGE OUTPUT|PRIVATE PROCESS DETAIL|原生数据/);
  assert.doesNotMatch(html,/<details[^>]*\sopen/);
});

test('result.json is authoritative and replaces the last streamed final', () => {
  const call = {id:'call_worker',task:{prompt:'task'},result:{content:'正式最终结果',finished_at:'2026-11-01T10:02:00Z'}};
  const html = workerPreview(node({state:'completed',latest:call}),{events:[{kind:'tool',id:'tool',tool:'read',title:'read',seq:'0:0'},{kind:'message',text:'旧流回复',seq:'1:0'},{kind:'turn',seq:'2:0'}]});
  assert.match(html,/正式最终结果/); assert.match(html,/1 次工具调用/);
  assert.doesNotMatch(html,/旧流回复|主控已核对|运行中/);
});

test('only the last three semantic rows are expanded, with long original text retained', () => {
  const content='很长的回复。'.repeat(80);
  const events=Array.from({length:5},(_,i)=>({kind:'message',id:String(i),text:i===4?content:`message-${i}`,seq:`${i}:0`}));
  const html=workerPreview(node(),{events});
  assert.doesNotMatch(html,/message-0|message-1/);
  assert.match(html,/message-2/); assert.match(html,/message-3/);
  assert.match(html,/class="compact-original"/); assert.ok(html.includes(content));
});

test('running tool-only streams still have a collapsed inspection entry', () => {
  const html=workerPreview(node(),{events:[{kind:'tool',id:'x',tool:'execute',title:'pytest',status:'in_progress',seq:'0:0'}]});
  assert.match(html,/1 次工具调用/);assert.match(html,/pytest/);assert.match(html,/compact-current/);
  assert.doesNotMatch(html,/<details[^>]*\sopen/);
});

test('human approvals and questions have distinct labels without inventing review state', () => {
  const approval={pending:true,needsUser:true,request:{kind:'approval_required'}};
  const question={pending:true,needsUser:true,request:{kind:'interaction_required'}};
  assert.deepEqual(workerStatus(node({state:'waiting',requests:[approval]})),{label:'等你批准',kind:'approval',activity:'见上方'});
  assert.equal(workerStatus(node({state:'waiting',requests:[question]})).label,'等你回答');
  assert.equal(workerStatus(node({state:'waiting',requests:[approval,question]})).label,'等你决定');
  assert.equal(workerStatus(node({state:'completed'})).label,'已返回');
});

test('duration uses actual timestamps, clamps negative durations and avoids estimating ended calls', () => {
  const call={task:{created_at:'2026-11-01T10:00:00Z'},handle:{started_at:'2026-11-01T10:00:05Z'}};
  assert.equal(elapsed(call,Date.parse('2026-11-01T10:08:45Z')),'8:40');
  assert.equal(elapsed({...call,result:{finished_at:'2026-11-01T11:02:08Z'}},0),'1:02:03');
  assert.equal(elapsed({...call,result:{outcome:'completed'}},Date.now()),'—');
  assert.equal(elapsed({...call,state:'interrupted'},Date.now()),'—');
  assert.equal(elapsed(call,0),'0:00');
});

test('messages remain scoped to this worker and do not expose raw sender IDs', () => {
  const html=workerPreview(node(),{events:[]},[
    {id:'one',fromName:'OpenCode',message:{from:'call_sender',to:'call_worker',content:'请确认 exp 字段',created_at:'2026-11-01T10:00:00Z'}},
    {id:'two',message:{from:'elsewhere',to:'unrelated',content:'NOT THIS WORKER'}}
  ]);
  assert.match(html,/compact-peer/); assert.match(html,/OpenCode 发来/); assert.match(html,/请确认 exp 字段/);
  assert.doesNotMatch(html,/call_sender|NOT THIS WORKER/);
});

test('untrusted message, tool title, error and original prompt are escaped', () => {
  const html=workerPreview(node({latest:{task:{prompt:'<script>bad()</script>'},result:{error:'<img src=x onerror=bad()>'}}}),{events:[{kind:'tool',id:'x',title:'<svg onload=bad()>',status:'failed',seq:'0:0'}]});
  assert.doesNotMatch(html,/<script>|<img|<svg/);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});
