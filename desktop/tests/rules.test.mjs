import test from 'node:test';
import assert from 'node:assert/strict';
import { summarize, occurrences, notifications } from '../electron/attention.mjs';
import { parseRules, matches, render, plan, context, template, rulePrefix } from '../electron/rules.mjs';
import i18n from '../electron/i18n.cjs';
import { setLanguage } from '../renderer/i18n.mjs';

// These tests check Desk's Chinese wording (main-process copy and the renderer modules it reuses).
i18n.setLanguage('zh'); setLanguage('zh');

const desk = 'main_' + 'd'.repeat(32);
const id = c => 'call_' + c.repeat(32);
function call(key, { owner = desk, provider = 'codex', outcome, prompt = '任务 ' + key, workspace = 'D:\\Work\\api' } = {}) {
  return { id: id(key), agent_id: id(key), state: outcome || 'running', directory: '',
    task: { call_id: id(key), agent_id: id(key), controller_id: owner, provider, prompt, workspace, created_at: '2026-11-02T10:00:00Z' },
    handle: {}, result: outcome ? { outcome, content: 'ok' } : null };
}
function snapshot(calls, requests = []) {
  const data = { calls: {}, agents: {}, requests: {}, messages: {}, warnings: [], desk: { controller_id: desk } };
  data.agents[desk] = { agent_id: desk, desk_controller: true, owner_id: null, call_id: null };
  for (const c of calls) { data.calls[c.id] = c; data.agents[c.id] = { agent_id: c.id, call_id: c.id, owner_id: c.task.controller_id }; }
  for (const [key, rid, request] of requests) data.requests[id(key) + '/' + rid] = { call_id: id(key), id: rid, pending: true, delegate_to_parent: false, request };
  return data;
}
const command = { kind: 'approval_required', created_at: '2026-11-02T10:01:00Z', native_request: { command: 'npm test' } };
const question = { kind: 'interaction_required', response_format: 'codex-question', created_at: '2026-11-02T10:01:00Z', native_request: { questions: [{ id: 'q', question: '继续吗？' }] } };
const rules = value => { const parsed = parseRules(JSON.stringify({ rules: value })); assert.deepEqual(parsed.errors, []); return parsed.rules; };

test('the rules file reports each broken rule and keeps the rest', () => {
  assert.match(parseRules('{ not json').errors[0], /不是有效的 JSON/);
  assert.deepEqual(parseRules('{}'), { rules: [], errors: [], disabled: 0 });
  const parsed = parseRules(JSON.stringify({ rules: [
    { name: 'ok', on: 'done', do: 'silence' },
    { name: 'bad on', on: 'finished', do: 'silence' },
    { name: 'bad do', on: 'done', do: 'email' },
    { name: 'bad key', on: 'done', match: { model: 'x' }, do: 'silence' },
    { name: 'bad owner', on: 'done', match: { owner: 'boss' }, do: 'silence' },
    { name: 'no text', on: 'failed', do: 'assistant' },
    { name: 'bad limit', on: 'failed', do: 'assistant', text: 'x', limit: 0 },
    { name: 'paused', enabled: false, on: 'done', do: 'silence' },
    'nonsense'
  ] }));
  assert.deepEqual(parsed.rules.map(r => r.name), ['ok']);
  assert.equal(parsed.disabled, 1);
  assert.equal(parsed.errors.length, 7);
  assert.match(parsed.errors[0], /第 2 条「bad on」：on 应该是 waiting \/ done \/ failed/);
  assert.match(parsed.errors[2], /match 不认识「model」/);
  assert.match(parsed.errors[4], /do 为 assistant 时需要 text/);
  assert.match(parseRules(JSON.stringify([{ name: 'a', on: 'done', do: 'silence' }, { name: 'a', on: 'failed', do: 'silence' }])).errors[0], /规则名「a」重复/);
});

test('the template Desk creates is valid and every example starts disabled', () => {
  const parsed = parseRules(template());
  assert.deepEqual([parsed.rules.length, parsed.errors.length, parsed.disabled], [0, 0, 4]);
  const enabled = JSON.parse(template());
  enabled.rules.forEach(rule => { rule.enabled = true; });
  assert.deepEqual(parseRules(JSON.stringify(enabled)).errors, []);
});

test('rules match provider id or name, session text, workspace subfolders, origin and request kind', () => {
  const ctx = { event: 'waiting', provider: 'claude-code', session: '修复登录 bug', workspace: 'D:\\Work\\API\\src', owner: 'desk', kind: 'approval' };
  const rule = match => rules([{ on: ['waiting', 'done'], match, do: 'silence' }])[0];
  assert.ok(matches(rule({ provider: 'Claude Code' }), ctx));
  assert.ok(matches(rule({ provider: ['codex', 'claude-code'] }), ctx));
  assert.ok(matches(rule({ session: '登录' }), ctx));
  assert.ok(matches(rule({ workspace: 'd:/work/api' }), ctx), 'a parent folder, any slash, any case');
  assert.ok(!matches(rule({ workspace: 'D:\\Work\\AP' }), ctx), 'not a mere prefix of the folder name');
  assert.ok(matches(rule({ owner: 'desk', kind: 'approval' }), ctx));
  assert.ok(!matches(rule({ kind: 'question' }), ctx));
  assert.ok(!matches(rule({}), { ...ctx, event: 'failed' }));
  assert.equal(render('{provider} · {event} · {session} · {unknown}', ctx, { name: 'r' }), 'Claude Code · 等你决定 · 修复登录 bug · {unknown}');
});

test('rules run on what just happened: notify replaces, silence quiets, costly actions fire once per session', () => {
  const quiet = summarize(snapshot([call('a'), call('b', { provider: 'claude-code' })]));
  const asking = summarize(snapshot([call('a'), call('b', { provider: 'claude-code' })], [['a', 'approval_' + '1'.repeat(32), command], ['b', 'approval_' + '2'.repeat(32), question]]));
  const happened = occurrences(quiet, asking);
  assert.deepEqual(happened.map(entry => [entry.on, context(entry).kind, context(entry).owner, context(entry).workspace]), [['waiting', 'approval', 'desk', 'D:\\Work\\api'], ['waiting', 'question', 'desk', 'D:\\Work\\api']]);
  const set = rules([
    { name: 'codex 审批', on: 'waiting', match: { provider: 'codex' }, do: 'notify', title: '{provider} 等你批准', text: '{session}: {detail}' },
    { name: '问题静音', on: 'waiting', match: { kind: 'question' }, do: 'silence' }
  ]);
  const { actions, quiet: silenced } = plan(set, happened);
  assert.deepEqual(actions.map(a => [a.do, a.title, a.text]), [['notify', 'Codex 等你批准', '任务 a: npm test'], ['silence', undefined, undefined]]);
  assert.deepEqual(notifications(happened.filter(entry => !silenced.has(entry))), [], 'no built-in notification left');

  const done = summarize(snapshot([call('a', { outcome: 'completed' }), call('b', { provider: 'claude-code' })], [['b', 'approval_' + '2'.repeat(32), question]]));
  const finished = occurrences(asking, done);
  const costly = rules([{ name: '完成后自查', on: 'done', do: 'message', text: '请检查「{session}」' }, { name: '告诉助理', on: 'done', do: 'assistant', text: '{session} 完成了', limit: 2 }]);
  const counts = new Map();
  const first = plan(costly, finished, counts).actions;
  assert.deepEqual(first.map(a => [a.do, a.skipped, a.text]), [['message', undefined, rulePrefix('完成后自查') + '请检查「任务 a」'], ['assistant', undefined, rulePrefix('告诉助理') + '任务 a 完成了']]);
  assert.equal(first[0].ctx.sessionId, id('a'));
  const again = plan(costly, finished, counts).actions;
  assert.deepEqual(again.map(a => a.skipped), ['limit', undefined], 'message fires once; the assistant rule allows two');
  assert.deepEqual(plan(costly, finished, counts).actions.map(a => a.skipped), ['limit', 'limit']);
});

test('a message rule never targets an external conversation', () => {
  const entry = { on: 'done', scope: 'session', key: 'session:x', provider: 'codex', session: { id: 'external:x', name: '外部', external: true, origin: 'external', workspace: '' }, target: {} };
  const [action] = plan(rules([{ on: 'done', do: 'message', text: 'hi' }]), [entry]).actions;
  assert.equal(action.skipped, 'no-session');
});
