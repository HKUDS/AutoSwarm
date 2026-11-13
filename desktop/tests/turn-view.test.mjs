import test from 'node:test';
import assert from 'node:assert/strict';
import { workSummary, stepList, liveSteps, durationText, latestActivity } from '../renderer/turn-view.mjs';
import { processView } from '../renderer/stream-ui.mjs';
import { setLanguage } from '../renderer/i18n.mjs';
setLanguage('zh');  // These assertions read Desk's Chinese source text.

const tool = (title, extra = {}) => ({ kind: 'tool', tool: 'execute', title, status: 'completed', ...extra });

test('a turn folds into one row with Codex-style counts and duration', () => {
  const items = [{ kind: 'message', text: '先检查可用性。' }, tool('autoswarm --version'), tool('autoswarm run', { status: 'failed' }), { kind: 'thought', text: '比较版本' }];
  const html = workSummary(items, { head: `已处理 ${durationText(2195000)}`, key: 'k' });
  assert.match(html, /^<details class="work-summary" data-key="k" >/);
  assert.match(html, /已处理 36 分 35 秒/);
  assert.match(html, /2 次工具调用 · 1 条进展/);
  assert.match(html, /1 个失败/);
});

test('consecutive tools batch into one line; commentary stays prose between batches', () => {
  const html = stepList([tool('a'), tool('b', { tool: 'read' }), { kind: 'message', text: '**中途**说明' }, tool('c')], 'p', text => `<b>${text}</b>`);
  assert.equal((html.match(/class="step-batch"/g) || []).length, 1, 'only the run of two tools is batched');
  assert.match(html, /运行 1 条命令，读取 1 个文件/);
  assert.match(html, /<b>\*\*中途\*\*说明<\/b>/, 'commentary goes through the prose renderer');
  assert.equal((html.match(/class="process-entry step /g) || []).length, 3);
});

test('step rows keep the plain title in .process-label strong and output behind a row', () => {
  const html = stepList([tool('读取测试文件 1', { output: 'ok' })], 'p');
  assert.match(html, /<summary class="process-label">.*<strong>读取测试文件 1<\/strong>/);
  assert.match(html, /<pre class="terminal-output">ok<\/pre>/);
});

test('latest activity prefers the newest tool title or sentence', () => {
  assert.equal(latestActivity([{ kind: 'message', text: '旧的话' }, tool('pytest -q')]), 'pytest -q');
  assert.equal(latestActivity([tool('a'), { kind: 'message', text: '\n  新的一句\n第二行' }]), '新的一句');
});

test('stream turns fold intermediate messages and keep the ended turn reply open', () => {
  const events = [{ kind: 'message', seq: '0:0', text: '中间说明' }, { kind: 'tool', id: 't', seq: '1:0', title: 'ls', tool: 'execute', status: 'completed' },
    { kind: 'message', seq: '2:0', text: '最终答复' }, { kind: 'turn', seq: '3:0' }];
  const html = processView({ id: 'c' }, { events }, false, 0, { markdown: text => `<p>${text}</p>` });
  const [fold, rest] = html.split('</details>');
  assert.match(fold, /中间说明/);
  assert.doesNotMatch(fold.replace(/<div class="work-steps">[\s\S]*/, ''), /最终答复/);
  assert.match(html, /<div class="markdown turn-reply"><p>最终答复<\/p><\/div>/);
  assert.ok(rest !== undefined);
});

test('a stopped turn says so and counts the whole call, not just its last event', () => {
  const events = [{ kind: 'tool', id: 't', seq: '0:0', title: 'ping -n 45 127.0.0.1', tool: 'execute', status: 'in_progress', at: '2026-11-02T08:40:13Z' }];
  const call = { id: 'c', task: { created_at: '2026-11-02T08:40:00Z' }, handle: { started_at: '2026-11-02T08:40:00Z' }, result: { outcome: 'cancelled', finished_at: '2026-11-02T08:40:23Z' } };
  const html = processView(call, { events });
  assert.match(html, /已停止 · 用时 23 秒/);
});

test('a running turn shows its words and folds everything between them into one row', () => {
  const items = [{ kind: 'message', text: '先查资料。' }, tool('rg auth'), { kind: 'thought', text: '比较' }, tool('cat a.py'), tool('pytest', { status: 'in_progress' }),
    { kind: 'message', text: '再改代码。' }, tool('apply patch')];
  const html = liveSteps(items, 'live', text => text);
  assert.equal((html.match(/class="process-entry narrative step-note/g) || []).length, 2, 'Both sentences stay open');
  const batches = html.match(/<details class="step-batch"[\s\S]*?<\/summary>/g) || [];
  assert.equal(batches.length, 1, 'Three tools and a thought between the sentences fold into one row');
  assert.match(batches[0], /运行 3 条命令 · 思考 1 次/);
  assert.match(batches[0], /step-status running/, 'A running tool shows on the folded row');
  assert.ok(!/<details[^>]* open/.test(html), 'Nothing is unfolded');
  assert.ok(html.indexOf('再改代码') < html.lastIndexOf('apply patch'), 'A lone tool after the last sentence is its own row');
});

