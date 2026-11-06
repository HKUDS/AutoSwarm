import { activity, turns } from './streams.mjs';
import { providers } from './model.mjs';
import { t, locale } from './i18n.mjs';

const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));
const toolNames = { read: '读', edit: '改', delete: '删', move: '移', search: '搜', execute: '跑', think: '思考', fetch: '获取', other: '其他' };
const stateNames = { pending: '等待中', in_progress: '运行中', inProgress: '运行中', completed: '完成', failed: '失败', declined: '已拒绝', cancelled: '已取消' };

function text(value) {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  if (typeof value === 'object') {
    for (const key of ['message', 'text', 'command', 'name', 'path']) if (typeof value[key] === 'string') return value[key];
    try { return JSON.stringify(value); } catch { return ''; }
  }
  return String(value);
}

function oneLine(value) {
  return text(value).replace(/\s+/g, ' ').trim();
}

function date(value) {
  if (value == null || value === '') return null;
  const number = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

export function eventTime(item) {
  const raw = item?.raw || {};
  for (const value of [item?.at, item?.timestamp, raw.emittedAtMs, raw.timestamp,
    raw.created_at, raw.params?.item?.recordedAt, raw.properties?.part?.time?.end,
    raw.properties?.part?.time?.start]) {
    const at = date(value);
    if (at !== null) return at;
  }
  return null;
}

function timeLabel(at, fallback) {
  if (at === null) return `<span class="compact-time">${esc(fallback ?? t('本轮'))}</span>`;
  const value = new Date(at);
  const label = value.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', hour12: false });
  return `<time class="compact-time" datetime="${esc(value.toISOString())}" title="${esc(value.toLocaleString(locale()))}">${esc(label)}</time>`;
}

/** Only measured duration, never an estimated completion or invented timestamp. */
export function elapsed(call, now = Date.now()) {
  const start = date(call?.handle?.started_at) ?? date(call?.task?.created_at);
  if (start === null) return '—';
  const end = call?.result ? date(call.result.finished_at) : call?.state === 'interrupted' ? null : date(now);
  if (end === null) return '—';
  const seconds = Math.floor(Math.max(0, end - start) / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  const remainder = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

/** Labels express recorded status; a completed worker is not automatically reviewed. */
export function workerStatus(node, stream = node?.latest?.stream) {
  const requests = (node?.requests || []).filter(request => request.pending);
  const human = requests.filter(request => request.needsUser);
  if (node?.state === 'waiting' || human.length) {
    const actionable = human.length ? human : requests;
    const question = actionable.some(entry => entry.request?.kind === 'interaction_required');
    const permission = actionable.some(entry => entry.request?.kind === 'approval_required');
    if (question && !permission) return { label: t('等你回答'), kind: 'question', activity: t('见上方') };
    if (permission && !question) return { label: t('等你批准'), kind: 'approval', activity: t('见上方') };
    return { label: t('等你决定'), kind: 'waiting', activity: actionable.length > 1 ? t('{count} 项请求 · 见上方', { count: actionable.length }) : t('见上方') };
  }
  if (node?.state === 'delegated') return { label: t('上级代批中'), kind: 'delegated', activity: t('等待主控处理') };
  const result = node?.latest?.result;
  switch (node?.state) {
    case 'completed': return { label: t('已返回'), kind: 'completed', activity: node.idle ? t('可继续对话') : t('结果已就绪') };
    case 'failed': return { label: t('失败'), kind: 'failed', activity: oneLine(result?.error) || t('查看失败原因') };
    case 'cancelled': return { label: t('已停止'), kind: 'cancelled', activity: t('本轮已停止') };
    case 'interrupted': return { label: t('已中断'), kind: 'interrupted', activity: t('未收到最终结果') };
    default: return { label: t('运行中'), kind: 'running', activity: oneLine(activity(stream)) || (node?.latest?.handle?.phase === 'startup' ? t('正在启动') : t('正在执行任务')) };
  }
}

function inline(value) {
  // Minimal inline formatting is applied only AFTER HTML escaping. Arbitrary
  // Markdown HTML, image URLs and links are never inserted into this preview.
  return esc(value.replace(/^#{1,6}\s+/gm, ''))
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
}

function prose(value, key) {
  const original = text(value);
  const disclosure = original.length > 90 || original.split('\n').length > 2;
  return `<span class="compact-text ${disclosure ? 'is-long' : ''}">${inline(original)}</span>${disclosure ? `<details class="compact-original" data-key="${esc(key)}"><summary>${t('展开全文')}</summary><div class="compact-original-text">${esc(original)}</div></details>` : ''}`;
}

function toolCapsule(tools, key) {
  if (!tools.length) return '';
  const counts = new Map();
  for (const tool of tools) {
    const kind = Object.hasOwn(toolNames, tool.tool) ? tool.tool : 'other';
    counts.set(kind, (counts.get(kind) || 0) + 1);
  }
  const breakdown = [...counts].map(([kind, count]) => `${t(toolNames[kind])} ${count}`).join(' · ');
  return `<details class="tool-capsule" data-key="${esc(key)}"><summary><span>${t('{count} 次工具调用', { count: tools.length })}</span><span class="tool-capsule-counts"> · ${esc(breakdown)}</span></summary><ul class="compact-tool-list">${tools.map(tool => `<li class="compact-tool-item"><span class="compact-tool-kind">${esc(t(toolNames[tool.tool] || '其他'))}</span><span class="compact-tool-title" title="${esc(text(tool.title || tool.tool || t('工具操作')))}">${esc(oneLine(tool.title || tool.tool || t('工具操作')))}</span><span class="compact-tool-state ${tool.status === 'failed' || tool.status === 'declined' ? 'failed' : ''}">${esc(stateNames[tool.status] ? t(stateNames[tool.status]) : tool.status || '')}</span></li>`).join('')}</ul></details>`;
}

function messageRows(node, groups) {
  const call = node.latest || {};
  const canonicalGroup = call.result ? groups.findLastIndex(group => group.items.some(item => item.kind === 'message' && item.final)) : -1;
  const rows = [];
  groups.forEach((group, index) => {
    const tools = group.items.filter(item => item.kind === 'tool');
    const meaningful = group.items.filter(item => ['message', 'error'].includes(item.kind) && text(item.text).trim() && !(index === canonicalGroup && item.final));
    for (const item of meaningful) rows.push({ kind: item.kind, content: item.text, at: eventTime(item), key: item.seq || `${index}:${rows.length}`, tools: [] });
    if (meaningful.length) rows.at(-1).tools = tools;
    else if (tools.length && index !== canonicalGroup && group.ended) rows.push({ kind: 'tools', content: '', at: eventTime(tools.at(-1)), key: `group-${index}`, tools });
  });
  if (text(call.result?.content).trim()) rows.push({
    kind: 'result', content: call.result.content, at: date(call.result.finished_at), key: 'result',
    tools: canonicalGroup >= 0 ? groups[canonicalGroup].items.filter(item => item.kind === 'tool') : []
  });
  if (call.result?.error) rows.push({ kind: 'error', content: text(call.result.error), at: date(call.result.finished_at), key: 'error', tools: [] });
  return rows;
}

/** A small projection of record facts; full traces stay in the Worker view. */
export function workerPreview(node, stream, messages = []) {
  const call = node.latest || {};
  const groups = turns(stream?.events || []);
  const rows = messageRows(node, groups);
  const visible = rows.slice(-3);
  const provider = providers[node.provider] || node.provider || 'Agent';
  const body = visible.map(row => `<div class="compact-event ${row.kind === 'error' ? 'compact-error' : ''}">${timeLabel(row.at)}<div class="compact-body">${row.kind === 'tools' ? '' : `<span class="compact-author">${esc(provider)}${row.kind === 'result' ? ' · ' + t('最终回复') : row.kind === 'error' ? ' · ' + t('错误') : ''}</span>${prose(row.content, `worker-text-${node.id}-${row.key}`)}`}${toolCapsule(row.tools, `worker-tools-${node.id}-${row.key}`)}</div></div>`).join('');

  const addresses = new Set([node.id, ...(node.calls || []).map(item => item.id)]);
  const peers = (Array.isArray(messages) ? messages : Object.values(messages || {})).filter(entry => {
    const message = entry.message || entry;
    return addresses.has(message.from) || addresses.has(message.to);
  });
  const peerGroups = [];
  peers.slice(-2).forEach((entry,index) => {
    const message = entry.message || entry;
    const at = date(message.created_at || message.at);
    const minute = at === null ? `unknown-${index}` : Math.floor(at / 60000);
    if (peerGroups.at(-1)?.minute === minute) peerGroups.at(-1).items.push({entry,index});
    else peerGroups.push({minute,at,items:[{entry,index}]});
  });
  const peerRows = peerGroups.map(group => `<div class="compact-event">${timeLabel(group.at)}<div class="compact-peer">${group.items.map(({entry,index}) => {
    const message = entry.message || entry;
    const outgoing = addresses.has(message.from);
    const name = outgoing ? entry.toName || entry.recipientName : entry.fromName || entry.senderName;
    const label = outgoing ? name ? t('发给 {name}', { name }) : t('发给协作者') : name ? t('{name} 发来', { name }) : t('协作者发来');
    return `<div class="compact-peer-message"><span class="compact-peer-label">${esc(label)}</span>${prose(message.content || message.text || '', `worker-peer-${node.id}-${entry.id || index}`)}</div>`;
  }).join('')}</div></div>`).join('');

  const current = workerStatus(node, stream);
  const activeGroup = groups.at(-1);
  // Open turns still need a compact tool entry even when there are no messages.
  const activeTools = activeGroup && !activeGroup.ended && !activeGroup.items.some(item => ['message', 'error'].includes(item.kind) && text(item.text).trim())
    ? activeGroup.items.filter(item => item.kind === 'tool') : [];
  const trailingTools = toolCapsule(activeTools, `worker-tools-${node.id}-active`);
  const showCurrent = ['running', 'waiting', 'approval', 'question', 'delegated'].includes(current.kind);
  const currentRow = showCurrent ? `<div class="compact-event compact-current ${esc(current.kind)}"><span class="compact-time">${t('现在')}</span><div class="compact-body"><p class="compact-current-line"><span class="compact-current-dot" aria-hidden="true"></span><strong>${esc(current.label)}</strong><span>${esc(current.activity)}</span></p>${trailingTools}</div></div>` : trailingTools ? `<div class="compact-event">${timeLabel(null)}<div class="compact-body">${trailingTools}</div></div>` : '';
  const task = text(call.task?.prompt);
  const missing = !body && !peerRows && !currentRow ? `<p class="compact-empty">${call.result ? t('本轮未返回文本内容。') : stream?.lazy ? t('正在读取过程记录…') : t('等待 Agent 返回进展。')}</p>` : '';
  return `<div class="worker-preview">${rows.length > 3 ? `<p class="compact-omitted">${t('最近 3 段关键内容 · 完整过程可在全屏中查看')}</p>` : ''}${body}${peerRows}${peers.length > 2 ? `<p class="compact-omitted">${t('另有 {count} 条协作消息，可在全屏中查看', { count: peers.length - 2 })}</p>` : ''}${currentRow}${missing}${task ? `<details class="compact-task" data-key="worker-task-${esc(node.id)}"><summary>${t('任务说明')}</summary><div class="compact-original-text">${esc(task)}</div></details>` : ''}</div>`;
}
