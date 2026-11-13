// AutoSwarm call process, as Codex shows it: while a turn runs, the agent's interim
// text is open and its tools and thinking are folded rows; once the turn ends, all of it
// folds into one work row and only the final reply (plus decisions and errors) stays open.
import { turns, windowTurns } from './streams.mjs';
import { esc, workSummary, liveSteps, durationText } from './turn-view.mjs';
import { eventTime, elapsed } from './worker-view.mjs';
import { providers } from './model.mjs';
import { t } from './i18n.mjs';

function callDuration(call) {
  const measured = elapsed(call);
  if (measured === '—') return '';
  const [a, b, c] = measured.split(':').map(Number);
  return durationText((c === undefined ? a * 60 + b : a * 3600 + b * 60 + c) * 1000);
}
function groupDuration(group, call, isLast) {
  // A turn cut short (stopped, failed) lasted until its call ended, not until its last event.
  if (isLast && call.result && !group.ended) return callDuration(call);
  const times = group.items.map(eventTime).filter(value => value !== null);
  if (times.length >= 2 && Math.max(...times) > Math.min(...times)) return durationText(Math.max(...times) - Math.min(...times));
  return isLast && call.result ? callDuration(call) : '';
}

export function processView(call, stream, full = false, offset = 0, options = {}) {
  // The agent's own session record is gone (cleaned up by the agent) and no copy was kept.
  // The watcher reports this one case (native_calls.py); it is worded here so it follows the language.
  if (stream?.missing && !stream.available) return `<div class="stream-fallback is-missing">${esc(t('未找到 {agent} 自己的会话记录（可能已被它清理），最终回复仍保留。', { agent: providers[call.task?.provider] || call.task?.provider || 'Agent' }))}</div>`;
  if (!stream || stream.lazy) return stream?.available ? `<div class="stream-fallback">${t('正在读取对话…')}</div>` : '';
  const groups = turns(stream.events || []);
  if (!groups.length) return '';
  const canonical = groups.findLastIndex(group => group.items.some(item => item.final));
  const visible = windowTurns(groups, offset);
  const earlier = stream.before > 0 || groups.reduce((n, group) => n + group.items.length, 0) > offset + 80;
  const reply = options.markdown || (text => `<p>${esc(text)}</p>`);
  return `<section class="process-stream">${earlier ? `<div class="history-edge" data-history="${esc(call.id)}">${t('向上滚动载入更早的过程')}</div>` : ''}${visible.map(group => {
    const original = groups[group.index];
    // The canonical final message is replaced by result.json, which is authoritative.
    const replaced = group.index === canonical && call.result?.content;
    const final = original.ended ? [...group.items].reverse().find(item => item.kind === 'message' && item.final) : null;
    const errors = group.items.filter(item => item.kind === 'error');
    const folded = group.items.filter(item => item !== final && item.kind !== 'error');
    const duration = groupDuration(original, call, group.index === groups.length - 1);
    const key = `${options.keyPrefix || 'process'}-${call.id}-${group.index}`;
    if (!original.ended && !call.result) return `<div class="process-turn is-live">${folded.length ? `<div class="live-steps">${liveSteps(folded, key, options.markdown)}</div>` : ''}${errors.map(error => `<div class="error-text">${esc(error.text || error.title || t('错误'))}</div>`).join('')}</div>`;
    const head = original.ended ? (duration ? t('已处理 {duration}', { duration }) : t('已处理')) : call.result ? `${call.result.outcome === 'cancelled' ? t('已停止') : t('调用已结束')}${duration ? ' · ' + t('用时 {duration}', { duration }) : ''}` : t('进行中');
    return `<div class="process-turn">${folded.length ? workSummary(folded, { head, key, open: full, prose: options.markdown }) : ''}${final && !replaced ? `<div class="markdown turn-reply">${reply(final.text || '')}</div>` : ''}${errors.map(error => `<div class="error-text">${esc(error.text || error.title || t('错误'))}</div>`).join('')}</div>`;
  }).join('')}${offset ? `<div class="history-edge" data-newer="${esc(call.id)}">${t('向下滚动查看后续过程')}</div>` : ''}</section>`;
}
