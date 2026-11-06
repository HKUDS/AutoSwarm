import test from 'node:test';
import assert from 'node:assert/strict';
import { summarize, triggers, trayText } from '../electron/attention.mjs';
import i18n from '../electron/i18n.cjs';
import { setLanguage } from '../renderer/i18n.mjs';

// These tests check Desk's Chinese wording (main-process copy and the renderer modules it reuses).
i18n.setLanguage('zh'); setLanguage('zh');

const desk = 'main_' + 'd'.repeat(32);
const id = c => 'call_' + c.repeat(32);
function call(key, { owner = desk, provider = 'codex', state = 'running', outcome, prompt = '任务 ' + key, at = '2026-11-02T10:00:00Z' } = {}) {
  return { id: id(key), agent_id: id(key), state: outcome || state, directory: '',
    task: { call_id: id(key), agent_id: id(key), controller_id: owner, provider, prompt, workspace: 'D:\\w', created_at: at },
    handle: {}, result: outcome ? { outcome, content: outcome === 'completed' ? 'ok' : '', finished_at: at } : null };
}
function snapshot(calls, requests = []) {
  const data = { calls: {}, agents: {}, requests: {}, messages: {}, warnings: [], desk: { controller_id: desk } };
  data.agents[desk] = { agent_id: desk, desk_controller: true, owner_id: null, call_id: null };
  for (const c of calls) { data.calls[c.id] = c; data.agents[c.id] = { agent_id: c.id, call_id: c.id, owner_id: c.task.controller_id }; }
  for (const [key, rid, request] of requests) data.requests[id(key) + '/' + rid] = { call_id: id(key), id: rid, pending: true, delegate_to_parent: false, request };
  return data;
}
const write = { kind: 'approval_required', created_at: '2026-11-02T10:01:00Z', workspace: 'D:\\w', native_request: { tool_name: 'Write', input: { file_path: 'D:\\w\\a.txt', content: 'x' } } };

test('the first summary is only a baseline; a new request notifies once with what it would do', () => {
  const quiet = summarize(snapshot([call('a')]));
  const asking = summarize(snapshot([call('a')], [['a', 'approval_' + '1'.repeat(32), write]]));
  assert.deepEqual(triggers(null, asking), []);
  const [event] = triggers(quiet, asking);
  assert.equal(event.kind, 'waiting');
  assert.equal(event.title, 'Codex 需要你决定');
  assert.match(event.body, /写入文件 a\.txt\n任务 a/);
  assert.deepEqual(event.target, { session: id('a'), request: id('a') + '/approval_' + '1'.repeat(32) });
  assert.deepEqual(triggers(asking, asking), [], 'nothing new, nothing sent');
  assert.equal(trayText(asking).tooltip, 'AutoSwarm Desk · 1 件等你决定 · 1 个运行中');
});

test('many new requests at once collapse into one notification', () => {
  const quiet = summarize(snapshot([call('a'), call('b'), call('c')]));
  const busy = summarize(snapshot([call('a'), call('b'), call('c')], ['a', 'b', 'c'].map(k => [k, 'approval_' + k.repeat(32), write])));
  const events = triggers(quiet, busy);
  assert.equal(events.length, 1);
  assert.equal(events[0].title, '3 件事等你决定');
  assert.equal(events[0].keys.length, 3);
});

test('a session that returns notifies completion; a failed worker notifies failure; a stopped one stays quiet', () => {
  const worker = (state, outcome) => call('w', { owner: id('m'), provider: 'opencode', state, outcome, prompt: '子任务' });
  const before = summarize(snapshot([call('m'), worker('running')]));
  const done = summarize(snapshot([call('m', { outcome: 'completed' }), worker('running', 'completed')]));
  assert.deepEqual(triggers(before, done).map(e => [e.kind, e.title, e.target.session]), [['done', '会话已完成', id('m')]]);
  const failedWorker = summarize(snapshot([call('m'), worker('running', 'failed')]));
  assert.deepEqual(triggers(before, failedWorker).map(e => [e.kind, e.title, e.target.worker]), [['failed', 'OpenCode 失败', id('w')]]);
  const endedWithFailure = summarize(snapshot([call('m', { outcome: 'completed' }), worker('running', 'failed')]));
  assert.deepEqual(triggers(before, endedWithFailure).map(e => e.title), ['会话已结束，有失败'], 'one notification for the whole session');
  const stopped = summarize(snapshot([call('m', { outcome: 'cancelled' }), worker('running', 'cancelled')]));
  assert.deepEqual(triggers(before, stopped), [], 'the user stopped it; no notification');
});

test('a session that starts and finishes between two frames still notifies', () => {
  const before = summarize(snapshot([call('a', { outcome: 'completed' })]));
  const after = summarize(snapshot([call('a', { outcome: 'completed' }), call('b', { outcome: 'completed' }), call('c', { outcome: 'failed' })]));
  assert.deepEqual(triggers(before, after).map(e => [e.kind, e.target.session]), [['done', id('b')], ['failed', id('c')]]);
  assert.deepEqual(triggers(after, after), [], 'and only once');
});
