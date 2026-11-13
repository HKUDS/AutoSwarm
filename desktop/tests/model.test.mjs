import test from 'node:test';
import assert from 'node:assert/strict';
import { project } from '../renderer/model.mjs';
import { createDemo } from '../electron/sample.mjs';

test('external main uses the exact native title and provider, not a source label', () => {
  const data = createDemo(); delete data.desk;
  data.agents.main_demo = { parent:{host:'codex',session_id:'native-main'} };
  data.native_conversations = {main_demo:{title:'设计 AutoSwarm 多 Agent UI',workspace:'D:/AutoSwarm',messages:[]}};
  const session = project(data).sessions[0];
  assert.equal(session.name,'设计 AutoSwarm 多 Agent UI');
  assert.equal(session.provider,'codex');
  assert.equal(session.target,'main_demo');
});

test('a worker invoking AutoSwarm inside its native session stays under the main', () => {
  const data = createDemo();
  data.calls.call_02.result={native_session_id:'native-worker'};
  data.agents.main_nested={parent:{host:'codex',session_id:'native-worker'}};
  data.agents.call_07.owner_id='main_nested';
  const view=project(data);
  assert.equal(view.nodes.get('call_07').parent.id,'call_02');
  assert.equal(view.sessions.length,3);
});

test('an anonymous human controller does not create a fake external agent', () => {
  const data=createDemo();data.agents.main_human={call_id:null,parent:null};data.agents.call_08.owner_id='main_human';
  const session=project(data).sessions.find(s=>s.id==='call_08');
  assert.ok(session);assert.equal(session.external,undefined);
});

test('nested agents, pending requests and final results remain in their sessions', () => {
  const view = project(createDemo());
  assert.equal(view.sessions.length, 3);
  const root = view.nodes.get('call_01');
  assert.equal(root.members.length, 7);
  assert.equal(root.children.length, 5);
  assert.equal(view.nodes.get('call_07').parent.id, 'call_02');
  assert.equal(root.counts.waiting, 2);
  assert.equal(root.counts.completed, 1);
});
test('resume retains the agent and children even if ownership references an older call', () => {
  const data = createDemo();
  data.calls.call_10 = structuredClone(data.calls.call_01);
  data.calls.call_10.id = 'call_10';
  data.calls.call_10.task.previous_call = 'call_01';
  data.calls.call_10.task.created_at = '2026-11-02';
  data.agents.call_01.call_id = 'call_10';
  const view = project(data);
  assert.equal(view.sessions.length, 3);
  assert.equal(view.nodes.get('call_01').calls.length, 2);
  assert.equal(view.nodes.get('call_01').latest.id, 'call_10');
  assert.equal(view.nodes.get('call_01').children.length, 5);
});
test('parent delegation changes to human attention when parent becomes idle', () => {
  const data = createDemo();
  data.requests['call_03/approval_01'].request.user_required = false;
  assert.equal(project(data).nodes.get('call_03').state, 'delegated');
  data.calls.call_01.state = 'completed';
  assert.equal(project(data).nodes.get('call_03').state, 'waiting');
});
test('external siblings share one native host session', () => {
  const data = createDemo();
  delete data.desk;
  data.agents.main_demo = { parent: { host: 'codex', session_id: 'native-session' } };
  const view = project(data);
  assert.equal(view.sessions.length, 1);
  assert.equal(view.sessions[0].external, true);
  assert.equal(view.sessions[0].members.length, 10);
});
test('cycles and unknown owners do not lose calls or hang the projection', () => {
  const data = createDemo();
  data.agents.call_01.owner_id = 'call_02';
  data.agents.call_08.owner_id = 'missing';
  const view = project(data);
  assert.equal(view.nodes.size, 10);
  assert.equal(new Set(view.sessions.flatMap(s => s.members.map(n => n.id))).size, 9);
});
test('assistant is separate, only main conversations appear in the sidebar', () => {
  const data=createDemo(), view=project(data);
  assert.equal(view.assistant.id,'call_meta');
  assert.deepEqual(view.sessions.map(s=>s.id).sort(),['call_01','call_08','call_09']);
  assert.equal(view.sessions.some(s=>s.id==='call_02'||s.id==='call_07'),false);
});
test('a missing parent never promotes a worker into a main conversation', () => {
  const data=createDemo();
  data.agents.call_07.owner_id='call_deleted';
  const view=project(data);
  assert.equal(view.sessions.some(s=>s.id==='call_07'),false);
  assert.ok(view.sessions.find(s=>s.missingOwner).members.some(n=>n.id==='call_07'));
});
test('assistant identity can recover from the authoritative record after lost launch reply', () => {
  const data=createDemo();
  data.desk.assistant_agent_id=null;
  data.agents.call_meta.name='desk-assistant';
  const view=project(data);
  assert.equal(view.assistant.id,'call_meta');
  assert.equal(view.sessions.some(s=>s.id==='call_meta'),false);
});

test('each agent has its own assistant conversation; none is a sidebar session, theirs all are', () => {
  const data = createDemo();
  data.agents.call_meta.name = 'desk-assistant';
  // A second assistant conversation, on Codex, which started call_08.
  data.calls.call_meta2 = { ...data.calls.call_meta, id: 'call_meta2', agent_id: 'call_meta2', task: { ...data.calls.call_meta.task, call_id: 'call_meta2', agent_id: 'call_meta2', provider: 'codex', created_at: '2099-01-01T00:00:00Z' } };
  data.agents.call_meta2 = { ...data.agents.call_meta, agent_id: 'call_meta2', call_id: 'call_meta2', name: 'desk-assistant.codex', title: 'Release notes' };
  data.agents.call_08.owner_id = 'call_meta2'; data.calls.call_08.task.controller_id = 'call_meta2';
  data.desk = { ...data.desk, assistant_agent_id: 'call_meta2', assistants: [{ agent_id: 'call_meta', provider: 'claude-code' }, { agent_id: 'call_meta2', provider: 'codex' }] };
  const view = project(data);
  assert.equal(view.assistant.id, 'call_meta2', 'the chosen agent\'s conversation is shown');
  assert.deepEqual(view.assistants.map(n => n.id), ['call_meta2', 'call_meta'], 'newest first');
  assert.equal(view.sessions.some(s => s.id === 'call_meta' || s.id === 'call_meta2'), false);
  assert.ok(view.sessions.some(s => s.id === 'call_08') && view.sessions.some(s => s.id === 'call_01'), 'sessions either assistant started stay in the sidebar');
  // A chosen agent without a conversation yet shows none, rather than another agent's.
  data.desk = { ...data.desk, assistant_agent_id: null };
  assert.equal(project(data).assistant, undefined);
});
