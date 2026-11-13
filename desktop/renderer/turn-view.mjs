// One agent turn, rendered the way the native agents present it (Codex
// "Worked for …", DSH disclosure rows): process items fold into a single
// summary row; commentary reads as prose inside it; consecutive tool calls
// batch into one line; each step expands to its own output. The final reply
// and errors stay outside the fold. Used for AutoSwarm event streams and for
// imported native turns, so both look the same.
import { icon } from './icons.mjs';
import { t } from './i18n.mjs';

export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pretty = value => typeof value === 'string' ? value.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '') : JSON.stringify(value, null, 2);
const plain = value => typeof value === 'string' ? value : value == null ? '' : pretty(value);
const firstLine = value => plain(value).split('\n').map(line => line.trim()).find(Boolean) || '';

const toolGlyph = { read: 'eye', edit: 'edit', delete: 'trash', move: 'edit', search: 'search', execute: 'terminal', think: 'spark', fetch: 'globe', other: 'tool' };
const kindGlyph = { thought: 'spark', plan: 'list', notice: 'info', error: 'alert', usage: 'info' };
const verbs = { read: '读取 {count} 个文件', search: '搜索 {count} 次', execute: '运行 {count} 条命令', edit: '修改 {count} 处文件', fetch: '访问 {count} 个网页',
  delete: '删除 {count} 个文件', move: '移动 {count} 个文件', think: '思考 {count} 次', other: '调用 {count} 次工具' };
const failedStates = new Set(['failed', 'declined', 'error']);
const runningStates = new Set(['pending', 'in_progress', 'inProgress', 'running']);
const toolKind = item => Object.hasOwn(verbs, item.tool) ? item.tool : 'other';

export function durationText(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return t('{count} 秒', { count: seconds });
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  if (minutes < 60) return rest ? t('{minutes} 分 {seconds} 秒', { minutes, seconds: rest }) : t('{count} 分钟', { count: minutes });
  return t('{hours} 小时 {minutes} 分', { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

function status(item) {
  if (failedStates.has(item.status)) return `<small class="step-status failed">${item.status === 'declined' ? t('已拒绝') : t('失败')}</small>`;
  if (item.status === 'cancelled') return `<small class="step-status">${t('已取消')}</small>`;
  if (runningStates.has(item.status)) return '<small class="step-status running"><i class="state-dot"></i></small>';
  if (Number.isInteger(item.exit) && item.exit !== 0) return `<small class="step-status failed">${t('退出码 {code}', { code: item.exit })}</small>`;
  return '';
}

function title(item) {
  if (item.kind === 'tool') return plain(item.title || item.tool || t('工具操作'));
  if (item.kind === 'thought') return t('思考');
  if (item.kind === 'plan') return item.title || t('更新计划');
  if (item.kind === 'error') return t('错误');
  if (item.kind === 'usage') return t('用量');
  return item.title || t('原始事件');
}

function detail(item) {
  const parts = [];
  if (item.text) parts.push(`<p class="process-text">${esc(item.text)}</p>`);
  if (item.input !== undefined && item.input !== null) parts.push(`<details class="step-raw"><summary>${t('输入')}</summary><pre>${esc(pretty(item.input))}</pre></details>`);
  if (item.output !== undefined && item.output !== null && item.output !== '') parts.push(`<pre class="terminal-output">${esc(pretty(item.output))}</pre>`);
  for (const change of item.changes || []) parts.push(`<div class="diff-file">${esc(change.path || change.type || '')}</div><pre class="diff">${String(change.diff || '').split('\n').map(line => `<span class="${line.startsWith('+') ? 'addition' : line.startsWith('-') ? 'deletion' : ''}">${esc(line)}</span>`).join('\n')}</pre>`);
  if (item.entries) parts.push(`<pre>${esc(pretty(item.entries))}</pre>`);
  if (item.raw !== undefined) parts.push(`<details class="step-raw" data-key="raw-${esc(item.seq || item.id || '')}"><summary>${t('原生数据')}</summary><pre>${esc(pretty(item.raw))}</pre></details>`);
  return parts.join('');
}

/** One step: commentary as prose, everything else as a one-line row that expands. */
export function stepEntry(item, key, prose = text => esc(text)) {
  if (item.kind === 'message' || item.kind === 'user') {
    return `<article class="process-entry narrative step-note ${item.kind === 'user' ? 'from-user' : ''}">${item.kind === 'user' ? `<span class="step-note-label">${t('你')}</span>` : ''}<div class="process-text step-prose">${prose(item.text || '')}</div></article>`;
  }
  const kind = item.kind === 'tool' ? toolKind(item) : item.kind;
  const glyph = item.kind === 'tool' ? toolGlyph[kind] : kindGlyph[item.kind] || 'info';
  const preview = item.kind === 'thought' || item.kind === 'plan' || (item.kind === 'notice' && item.text) ? firstLine(item.text) : item.kind === 'error' ? firstLine(item.text || item.title) : '';
  const body = detail(item);
  const label = `<span class="step-icon">${icon(glyph)}</span><strong>${esc(title(item))}</strong>${preview ? `<em>${esc(preview)}</em>` : ''}${status(item)}`;
  const classes = `process-entry step step-${esc(item.kind)} ${item.kind === 'tool' ? `tool-${esc(kind)}` : ''} ${failedStates.has(item.status) ? 'is-failed' : ''}`;
  return body
    ? `<article class="${classes}"><details class="step-row" data-key="${esc(key)}"><summary class="process-label">${label}</summary><div class="step-detail">${body}</div></details></article>`
    : `<article class="${classes}"><div class="process-label">${label}</div></article>`;
}

function batchLabel(tools) {
  const counts = new Map();
  for (const tool of tools) counts.set(toolKind(tool), (counts.get(toolKind(tool)) || 0) + 1);
  return [...counts].map(([kind, count]) => t(verbs[kind], { count })).join(t('，'));
}

/** Consecutive tool calls collapse into one batch line, as Codex groups exploration. */
export function stepList(items, prefix, prose) {
  const out = [];
  let run = [];
  const keyOf = (item, index) => `${prefix}-${item.seq || item.id || index}`;
  const flush = () => {
    if (run.length >= 2) {
      const tools = run.map(entry => entry.item);
      const failed = tools.filter(tool => failedStates.has(tool.status)).length;
      const running = tools.some(tool => runningStates.has(tool.status));
      const dominant = toolKind(tools.at(-1));
      out.push(`<details class="step-batch" data-key="${esc(keyOf(tools[0], run[0].index))}-batch"><summary><span class="step-icon">${icon(toolGlyph[dominant])}</span><span class="step-batch-label">${esc(batchLabel(tools))}</span>${failed ? `<small class="step-status failed">${t('{count} 个失败', { count: failed })}</small>` : ''}${running ? '<small class="step-status running"><i class="state-dot"></i></small>' : ''}</summary><div class="step-batch-body">${run.map(entry => stepEntry(entry.item, keyOf(entry.item, entry.index), prose)).join('')}</div></details>`);
    } else for (const entry of run) out.push(stepEntry(entry.item, keyOf(entry.item, entry.index), prose));
    run = [];
  };
  items.forEach((item, index) => {
    if (item.kind === 'tool') { run.push({ item, index }); return; }
    flush();
    out.push(stepEntry(item, keyOf(item, index), prose));
  });
  flush();
  return out.join('');
}

/**
 * A running turn, as Codex shows it: the agent's words in the open, and everything between
 * them (tool calls, thinking, notices) folded into a single row, however many steps it holds.
 */
export function liveSteps(items, prefix, prose) {
  const out = [];
  let run = [];
  const keyOf = (item, index) => `${prefix}-${item.seq || item.id || index}`;
  const flush = () => {
    if (run.length === 1) out.push(stepEntry(run[0].item, keyOf(run[0].item, run[0].index), prose));
    else if (run.length) {
      const steps = run.map(entry => entry.item), tools = steps.filter(step => step.kind === 'tool');
      const thoughts = steps.filter(step => step.kind === 'thought').length, others = steps.length - tools.length - thoughts;
      const failed = tools.filter(tool => failedStates.has(tool.status)).length, running = tools.some(tool => runningStates.has(tool.status));
      const label = [tools.length ? batchLabel(tools) : '', thoughts ? t('思考 {count} 次', { count: thoughts }) : '', others ? t('{count} 条其他记录', { count: others }) : ''].filter(Boolean).join(' · ');
      out.push(`<details class="step-batch" data-key="${esc(keyOf(steps[0], run[0].index))}-live"><summary><span class="step-icon">${icon(tools.length ? toolGlyph[toolKind(tools.at(-1))] : kindGlyph.thought)}</span><span class="step-batch-label">${esc(label)}</span>${failed ? `<small class="step-status failed">${t('{count} 个失败', { count: failed })}</small>` : ''}${running ? '<small class="step-status running"><i class="state-dot"></i></small>' : ''}</summary><div class="step-batch-body">${run.map(entry => stepEntry(entry.item, keyOf(entry.item, entry.index), prose)).join('')}</div></details>`);
    }
    run = [];
  };
  items.forEach((item, index) => {
    if (item.kind === 'message' || item.kind === 'user' || item.kind === 'error') { flush(); out.push(stepEntry(item, keyOf(item, index), prose)); }
    else run.push({ item, index });
  });
  flush();
  return out.join('');
}

/** "已处理 3 分 12 秒 · 7 次工具调用 · 3 条进展" — the whole turn's process in one row. */
export function workSummary(items, { head, key, open = false, omitted = 0, prose }) {
  const tools = items.filter(item => item.kind === 'tool').length;
  const notes = items.filter(item => item.kind === 'message').length;
  const thoughts = items.filter(item => item.kind === 'thought').length;
  const failed = items.filter(item => item.kind === 'tool' && failedStates.has(item.status)).length;
  const counts = [tools ? t('{count} 次工具调用', { count: tools }) : '', notes ? t('{count} 条进展', { count: notes }) : '', !tools && !notes && thoughts ? t('思考 {count} 次', { count: thoughts }) : ''].filter(Boolean).join(' · ');
  return `<details class="work-summary" data-key="${esc(key)}" ${open ? 'open' : ''}><summary><span class="work-head">${esc(head)}</span>${counts ? `<span class="work-counts"> · ${esc(counts)}</span>` : ''}${failed ? `<span class="work-failed"> · ${t('{count} 个失败', { count: failed })}</span>` : ''}</summary><div class="work-steps">${omitted ? `<p class="step-omitted">${t('更早的 {count} 步未载入，完整记录保存在原生会话中', { count: omitted })}</p>` : ''}${stepList(items, key, prose)}</div></details>`;
}

/** What the agent is doing right now: the latest tool title or sentence. */
export function latestActivity(items) {
  const latest = [...items].reverse().find(item => (item.kind === 'tool' && item.title) || ((item.kind === 'message' || item.kind === 'thought') && item.text));
  return firstLine(latest?.kind === 'tool' ? latest.title : latest?.text || '').slice(0, 160);
}
