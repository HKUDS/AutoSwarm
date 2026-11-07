import { marked } from '../node_modules/marked/lib/marked.esm.js';
import DOMPurify from '../node_modules/dompurify/dist/purify.es.mjs';
import { project, providers, labels, count, taskTitle, logoFile } from './model.mjs';
import { createDemo } from '../electron/sample.mjs';
import { combine, activity, turns } from './streams.mjs';
import { processView } from './stream-ui.mjs';
import { workerPreview, workerStatus, elapsed } from './worker-view.mjs';
import { icon } from './icons.mjs';
import { workSummary, liveSteps, latestActivity, durationText } from './turn-view.mjs';
import { questionForm, encodeAnswer, describeRequest } from './interactions.mjs';
import { t, t as tr, setLanguage, language, locale, LANGUAGES } from './i18n.mjs';
import { createPreview, looksLikePath } from './preview.mjs';  // tr where a local t (a turn) hides it

const browserPreview = !window.desk;
const previewFrame = () => ({ event: 'snapshot', data: createDemo({ showcase: true }), demo: true, changes: [], python: '仅桌面版可用', skills: null });
const desk = window.desk || {
  initial: async () => previewFrame(),
  subscribe: () => () => {},
  demo: async () => receive(previewFrame()),
  chooseHome: desktopOnly, choosePython: desktopOnly, reconnect: desktopOnly,
  reveal: desktopOnly, window: desktopOnly
};
document.body.classList.toggle('browser-preview', browserPreview);
// macOS: the system's window buttons sit at the top left of the titlebar; the menu bar takes the tray's place.
const mac = desk.platform === 'darwin';
document.body.classList.toggle('mac', mac);
desk.onFullScreen?.(value => document.body.classList.toggle('full-screen', value));

const app = document.querySelector('#app');
const settings = document.querySelector('#settings');
const detail = document.querySelector('#detail');
let data = { calls: {}, agents: {}, requests: {}, messages: {}, warnings: [] }, view = project(data);
let selected = null, overview = true, expanded = new Set(), search = '', collapsed = false, assistantDockClosed = false;
const dockState = new Map();
const dockIsClosed = s => dockState.has(s.id) ? dockState.get(s.id) : s.members.filter(n => s.external || n.id !== s.id).every(n => ['completed','failed','cancelled'].includes(n.state));
let workerId = null, streamCache = new Map(), limits = new Map(), reading = new Set(), loaded = new Set(), lastHome, streamEpoch = 0, paging = false, userScrolling = 0;
let assistantSettings = {}, assistantDraft = '', assistantSubmitting = false, assistantError = '';
const assistantDrafts = new Map(), questionDrafts = new Map();
const chatDrafts = new Map(), chatModes = new Map(), chatTargets = new Map(), chatPending = new Set(), chatErrors = new Map();
const copies = new Map(); let copySeq = 0;
const nativeCache = new Map(), nativeTimers = new Map();
// New main conversation started directly from Desk (no assistant in between).
let composing = false, sessionDraft = { prompt: '' }, sessionSubmitting = false, sessionError = '', pendingSession = null;
let sessionDefaults = {}, available = null, ruleInfo = {};
// AutoSwarm switch: Skill status per agent (autoswarm skills), a setup in flight, failures kept for the Agent fallback.
let skills = null;
// Every agent can be a main agent with AutoSwarm; only Claude Code and Codex can hide a Skill for one
// conversation, so for the others the switch stays on (the Skill is visible to them anyway).
const SKILL_SWITCHABLE = new Set(['claude-code', 'codex']);
const DEMO_SKILLS = { hosts: { 'claude-code': { state: 'ready' }, codex: { state: 'ready' }, cursor: { state: 'missing' }, opencode: { state: 'ready' }, copilot: { state: 'ready' },
  muse: { state: 'missing' }, codebuddy: { state: 'ready' }, hermes: { state: 'ready' }, nanobot: { state: 'ready' }, dsh: { state: 'ready' }, grok: { state: 'ready' }, kimi: { state: 'ready' } },
  errors: { cursor: { message: '演示：找不到 Cursor 的技能目录' } } };
// An AutoSwarm older than this Desk reports fewer agents; those show no switch.
const swarmKnown = provider => !!providers[provider] && (!skills?.hosts || provider in skills.hosts);
const skillReady = provider => !swarmKnown(provider) || !skills?.hosts || skills.hosts[provider]?.state === 'ready';
const draftSkill = provider => skillReady(provider) && (!SKILL_SWITCHABLE.has(provider) || (sessionDraft.skill ?? true));
// Desk installs the Skill on launch for Claude Code, Codex and Cursor; any other agent gets it the first
// time it is picked as a main agent (the same standard setup). A failure stays for the install popover.
const skillTried = new Set();
function wantSkill(provider) {
  if (isDemo || !desk.skillInstall || !swarmKnown(provider) || !skills?.hosts || skillReady(provider) || skills.installing || skillTried.has(provider) || skills.errors?.[provider]) return false;
  if (available?.[provider] && !available[provider].available) return false;
  skillTried.add(provider);
  skills = { ...skills, installing: provider };
  desk.skillInstall(provider).catch(error => showToast(error.message));
  return true;
}
// Desk's permission mode per agent, named as each agent names its own (src/autoswarm/permissions.py
// keeps the same ids, order and defaults). A conversation may override it; its workers stay within it.
const PERMISSION_MODES = {
  'claude-code': [
    ['plan', 'Plan', '只读：研究和出方案，不改文件'],
    ['default', 'Manual', '改文件、运行命令前都问你'],
    ['acceptEdits', 'Accept edits', '直接改工作目录里的文件，运行命令前问你'],
    ['auto', 'Auto', 'Claude Code 的分类器审核每个操作，有风险的才拦下'],
    ['bypassPermissions', 'Bypass Permissions', '不做权限检查；只在你信任任务时使用']],
  'codex': [
    ['read-only', 'Read only', '只读工作区；改文件或联网要你批准'],
    ['ask', 'Ask for approval', '读写工作区、运行命令；联网或改工作区外的文件要你批准'],
    ['approve-for-me', 'Approve for me', 'Codex 自动审核，只有看起来不安全的操作才问你'],
    ['full-access', 'Full access', '不受沙箱限制，改任何文件、联网都不问你']],
  'cursor': [
    ['plan', 'Plan', '只读：分析和出方案，不改文件、不运行命令'],
    ['allowlist', 'Allowlist', '白名单里的命令直接运行，其余问你'],
    ['run-everything', 'Run Everything', '所有命令直接运行，不问你']],
};
const PERMISSION_DEFAULTS = { 'claude-code': 'acceptEdits', 'codex': 'ask', 'cursor': 'allowlist' };
let permissionPrefs = {};
const permissionDefault = provider => permissionPrefs[provider] || PERMISSION_DEFAULTS[provider];
const draftPermission = provider => sessionDraft.permission || permissionDefault(provider);
/** A chip showing one agent's mode, with a menu of that agent's own modes. */
function permissionChip(provider, mode, menu, place, heading) {
  const found = PERMISSION_MODES[provider]?.find(([id]) => id === mode);
  if (!found) return '';
  const list = PERMISSION_MODES[provider].map(([id, label, note]) => menuItem(`data-pick-permission="${id}" data-provider="${escape(provider)}" data-menu-id="${menu}"`, '', label, t(note), id === mode)).join('');
  return `<div class="menu-anchor"><button type="button" class="permission-chip" data-menu="${menu}" aria-haspopup="menu" aria-expanded="${openMenu === menu}" title="${escape(t('{agent} 权限 · {mode}：{note}', { agent: providers[provider] || provider, mode: found[1], note: t(found[2]) }))}">${icon('shield')}<span>${escape(found[1])}</span>${icon('down')}</button>${openMenu === menu ? `<div class="menu-pop ${place} permission-menu" role="menu" aria-label="${t('权限')}"><div class="menu-label">${escape(heading)}</div>${list}</div>` : ''}</div>`;
}
/** The official AutoSwarm logo (docs/assets/autoswarm), wherever Desk shows its own mark. */
const brand = (size, cls = '') => `<img class="autoswarm-logo ${cls}" src="../assets/logo-128.png" width="${size}" height="${size}" alt="">`;
const stopping = new Set();
let shellPrefs = {}, flash = null, update = null;
let preview = null;  // The side panel (preview.mjs), created once the page is set up.
const draftValue = key => sessionDraft[key] ?? sessionDefaults[key] ?? '';
// Chips with menus (DeepSeek's new-chat layout): workspace and main agent above the
// composer, model · effort at its bottom right. Catalogs come from the agents themselves.
let openMenu = null, assistantPick = null, modelFilter = '';
// 对话 / 轨迹 per page, like DeepSeek's session header.
const tabs = new Map();
const tabKey = () => overview ? 'assistant' : selected;
const tabOf = () => tabs.get(tabKey()) || 'chat';
const tabBar = () => `<div class="page-tabs" role="tablist" aria-label="${t('视图')}">${[['chat', '对话'], ['trace', '轨迹']].map(([key, label]) => `<button type="button" role="tab" class="page-tab ${tabOf() === key ? 'is-active' : ''}" aria-selected="${tabOf() === key}" data-tab="${key}">${t(label)}</button>`).join('')}</div>`;
const firstLine = text => String(text || '').trim().split(/\r?\n/)[0].slice(0, 200);
const DECISIONS = { allow: '已允许', approve: '已允许', deny: '已拒绝', reject: '已拒绝' };
/** 轨迹: every call of these agents in time order, each with its full process and decisions. */
function traceView(nodes) {
  const calls = nodes.filter(Boolean).flatMap(node => node.calls.map(call => ({ node, call }))).sort((a, b) => a.call.task.created_at.localeCompare(b.call.task.created_at));
  if (!calls.length) return `<div class="trace-view"><p class="trace-empty">${t('还没有轨迹。')}</p></div>`;
  return `<div class="trace-view">${calls.map(({ node, call }) => {
    const stream = streamCache.get(call.id) || call.stream;
    const role = node === view.assistant ? '私人助理' : view.sessions.some(x => x.id === node.id) ? '主控' : '子任务';
    const state = call.result ? ({ completed: '已完成', cancelled: '已停止' }[call.result.outcome] || '失败') : call.state === 'running' ? '进行中' : '未完成';
    const requests = Object.values(data.requests).filter(r => r.call_id === call.id).map(r => {
      const verdict = r.decision ? `${t(DECISIONS[r.decision.decision] || '已处理')} · ${r.decision.actor === 'user' ? t('你') : t('上级 Agent')}` : r.pending ? t('等待决定') : t('已结束');
      return `<li>${icon('flag')}<span>${escape(describeRequest(r.request).title)}</span><em>${escape(verdict)}</em></li>`;
    }).join('');
    const process = processView(call, stream, true, 0, { markdown, keyPrefix: 'trace' });  // Expanded here, independent of the 对话 tab.
    return `<section class="trace-call" id="trace-${escape(call.id)}"><div class="trace-head">${logo(call.task.provider)}<strong>${escape(providers[call.task.provider] || call.task.provider)}</strong><span class="role-tag">${t(role)}</span>${role === '子任务' ? `<span class="trace-name">${escape(node.name)}</span>` : ''}<span class="trace-meta">${escape(stamp(call.task.created_at))}${call.result ? ` · ${t('用时 {time}', { time: escape(elapsed(call)) })}` : ''} · ${t(state)}</span></div>${call.task.message_only ? '' : `<p class="trace-prompt" title="${escape(call.task.prompt)}">${escape(firstLine(call.task.prompt))}</p>`}${process || `<p class="trace-empty">${t('这一轮没有过程记录')}</p>`}${requests ? `<ul class="trace-decisions">${requests}</ul>` : ''}</section>`;
  }).join('')}</div>`;
}
const tokens = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
/** Under the composer, like DeepSeek: turns and steps, time, and tokens when the agent records them. */
function statsLine(node) {
  const calls = node?.calls || [];
  if (!calls.length) return '';
  const steps = new Set(), usage = { input: 0, cached: 0, output: 0 };
  let counted = false, ms = 0;
  for (const call of calls) {
    const stream = streamCache.get(call.id) || call.stream;
    for (const e of stream?.events || []) if (e.kind === 'tool') steps.add(`${call.id}:${e.id || e.seq}`);
    if (stream?.usage) { counted = true; for (const key of Object.keys(usage)) usage[key] += stream.usage[key] || 0; }
    const start = Date.parse(call.handle?.started_at || call.task.created_at), end = call.result ? Date.parse(call.result.finished_at || '') : nowMs();
    if (start && end && end > start) ms += end - start;
  }
  const parts = [`${t('{count} 轮', { count: calls.length })} · ${t('{count} 步', { count: steps.size })}`, ms ? t('用时 {time}', { time: durationText(ms) }) : '',
    counted ? t('输入 {input} tok · 输出 {output} tok', { input: tokens(usage.input), output: tokens(usage.output) }) : '', counted && usage.input ? t('缓存命中 {percent}%', { percent: Math.round(usage.cached * 100 / usage.input) }) : ''].filter(Boolean);
  return `<div class="composer-stats" aria-label="${t('统计')}">${parts.map(part => `<span>${escape(part)}</span>`).join('<i aria-hidden="true">|</i>')}</div>`;
}
const catalogs = new Map(), workspaceOpen = new Map();
const EFFORT_LABELS = { none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra High', max: 'Max', ultra: 'Ultra' };
// An agent's own name for a level (CodeBuddy's "disabled" is "Off") wins over Desk's.
const effortLabel = (value, provider) => (provider && catalogFor(provider).value?.effort_labels?.[value]) || EFFORT_LABELS[value] || value;
const baseName = value => String(value || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || String(value || '');
const DEMO_CATALOG = {
  'claude-code': { models: ['fable', 'opus', 'sonnet'].map(id => ({ id, label: id[0].toUpperCase() + id.slice(1), efforts: ['low', 'medium', 'high', 'xhigh', 'max'] })), efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
  codex: { models: [{ id: 'gpt-6.1-sol', label: 'GPT-6.1-Sol', efforts: ['low', 'medium', 'high', 'xhigh'], default_effort: 'low', default: true }, { id: 'gpt-6-luna', label: 'GPT-6-Luna', efforts: ['low', 'medium', 'high'], default_effort: 'medium' }], efforts: [] },
  cursor: { models: [{ id: 'auto', label: 'Auto', efforts: [], default: true }, { id: 'grok-4.7-high', label: 'Grok 4.7', efforts: ['low', 'medium', 'high', 'xhigh'], default_effort: 'high', variants: { low: 'grok-4.7-low', medium: 'grok-4.7-medium', high: 'grok-4.7-high', xhigh: 'grok-4.7-xhigh' } }], efforts: [] },
  // An agent with its own API providers lists every model they offer (OpenCode, Hermes).
  opencode: { models: [['volcengine', 'ark-code-latest'], ['volcengine', 'doubao-seed-2.5'], ['alibaba', 'qwen-max'], ['alibaba', 'qwen-plus'], ['alibaba', 'qwen-flash'], ['alibaba', 'kimi-k3'], ['deepseek', 'deepseek-v4-pro'], ['deepseek', 'deepseek-v4-flash'], ['zhipu', 'glm-5.3'], ['zhipu', 'glm-5.3-flash'], ['moonshot', 'kimi-k3-1'], ['minimax', 'minimax-m3'], ['openrouter', 'claude-opus-5-5'], ['openrouter', 'gpt-6.1-sol']]
    .map(([group, id], index) => ({ id: group + '/' + id, label: group + '/' + id, efforts: index === 0 ? ['low', 'medium', 'high'] : [], default: index === 0 })), efforts: ['low', 'medium', 'high'] },
  codebuddy: { models: [{ id: 'ark-code-latest', label: 'ark-code-latest', efforts: ['disabled', 'low', 'high', 'xhigh'], default_effort: 'high', default: true }, { id: 'hy4free', label: 'Hy4 · x0.00 credits', efforts: ['high'] }], efforts: ['disabled', 'low', 'high', 'xhigh'], effort_labels: { disabled: 'Off', xhigh: 'X-High' } }
};
function catalogFor(provider) {
  if (isDemo || browserPreview) return DEMO_CATALOG[provider] ? { value: DEMO_CATALOG[provider] } : { value: { models: [], efforts: [] } };
  if (!catalogs.has(provider) && desk.models) {
    catalogs.set(provider, { loading: true });
    desk.models(provider).then(value => { catalogs.set(provider, { value }); render(true); },
      failure => { catalogs.set(provider, { error: failure.message }); render(true); });
  }
  return catalogs.get(provider) || {};
}
/** A list read again replaces the shown one (in an open menu too); the selection stays as chosen. */
function takeCatalog(provider, value) {
  catalogs.set(provider, { value });
  render(true);
  if (settings.open) renderSettings();
}
function askCatalog(provider) {
  if (isDemo || browserPreview || !desk.models || !catalogs.get(provider)?.value) return;
  desk.models(provider).then(value => takeCatalog(provider, value), () => { /* The shown list stays. */ });
}
async function refreshCatalog(provider) {
  const before = catalogs.get(provider) || {};
  catalogs.set(provider, { ...before, refreshing: true });
  render(true); if (settings.open) renderSettings();
  try {
    const value = await desk.models(provider, { refresh: true });
    takeCatalog(provider, value);
    if (value.refresh_error) showToast(t('没能重新读取，仍显示上次的列表：{error}', { error: value.refresh_error }));
  } catch (error) {
    catalogs.set(provider, { ...before, refreshing: false, ...(before.value ? {} : { error: error.message }) });
    render(true); if (settings.open) renderSettings();
    showToast(error.message);
  }
}
function chosenModel(provider, model) {
  const models = catalogFor(provider).value?.models || [];
  return models.find(m => m.id === model || Object.values(m.variants || {}).includes(model)) || null;
}
/** What the model chip says, e.g. "GPT-6.1-Sol" + "High"; Cursor's effort is part of the model id. */
function modelChip(provider, model, effort) {
  const found = chosenModel(provider, model);
  const variant = found?.variants ? Object.entries(found.variants).find(([, id]) => id === model)?.[0] : '';
  return { name: found?.label || model || t('默认模型'), level: variant || effort ? effortLabel(variant || effort, provider) : '' };
}
function relTime(value) {
  const at = Date.parse(value || ''), now = nowMs();
  if (!at) return '';
  const minutes = Math.floor((now - at) / 60000), day = new Date(at), today = new Date(now);
  if (minutes < 1) return t('刚刚');
  if (minutes < 60) return t('{count} 分钟前', { count: minutes });
  if (day.toDateString() === today.toDateString()) return t('{count} 小时前', { count: Math.floor(minutes / 60) });
  if (new Date(now - 864e5).toDateString() === day.toDateString()) return t('昨天');
  return `${String(day.getMonth() + 1).padStart(2, '0')}/${String(day.getDate()).padStart(2, '0')}`;
}
const workspaceOf = s => (s.external ? s.binding?.workspace : s.latest?.task.workspace) || '';
function recentWorkspaces() {
  const seen = new Set();
  for (const call of Object.values(data.calls).sort((a, b) => b.task.created_at.localeCompare(a.task.created_at))) if (call.task.workspace) seen.add(call.task.workspace);
  return [...seen].slice(0, 8);
}
const menuItem = (attrs, glyph, title, note, on) => `<button type="button" class="menu-item ${on ? 'is-selected' : ''}" role="menuitemradio" aria-checked="${!!on}" ${attrs}>${glyph}<span class="menu-text"><strong>${escape(title)}</strong>${note ? `<small>${escape(note)}</small>` : ''}</span>${on ? `<span class="menu-check">${icon('check')}</span>` : ''}</button>`;
function agentMenu(provider, ctx = 'session') {
  const missing = id => available?.[id] && !available[id].available;
  const entries = Object.entries(providers), ready = entries.filter(([id]) => !missing(id) || id === provider), absent = entries.filter(([id]) => missing(id) && id !== provider);
  return `<div class="menu-pop agent-menu" role="menu" aria-label="${t('主 Agent')}"><div class="menu-label">${t('主 Agent')}</div><div class="menu-scroll">${ready.map(([id, name]) =>
    menuItem(`data-pick-agent="${id}" data-ctx="${ctx}"`, logo(id, ''), name, available?.[id]?.version || '', id === provider)).join('')}</div>${absent.length ? `<div class="menu-divider"></div><div class="menu-label">${t('本机未检测到')}</div><div class="menu-absent">${absent.map(([id, name]) => `<span title="${escape(t('{agent} 未安装或不在 PATH 中', { agent: name }))}">${logo(id, '')}${escape(name)}</span>`).join('')}</div>` : ''}</div>`;
}
function workspaceMenu(current) {
  const recent = recentWorkspaces();
  return `<div class="menu-pop" role="menu" aria-label="${t('工作目录')}"><div class="menu-label">${t('最近使用')}</div>${recent.map(w => menuItem(`data-pick-workspace="${escape(w)}" title="${escape(displayPath(w))}"`, icon('folder'), baseName(w), displayPath(w), w === current)).join('') || `<p class="menu-empty">${t('还没有用过的工作目录')}</p>`}<div class="menu-divider"></div><button type="button" class="menu-item" data-action="session-workspace">${icon('plus')}<span class="menu-text"><strong>${t('选择其他目录…')}</strong></span></button></div>`;
}
function modelMenu(provider, model, effort, place = 'menu-up', ctx = 'session') {
  const entry = catalogFor(provider), name = providers[provider] || provider, list = entry.value?.models || [];
  // When the list was read, and a way to read it again (a new login, an API model just configured, an upgrade).
  const read = entry.value?.read_at ? relTime(new Date(entry.value.read_at).toISOString()) : '';
  const refresh = isDemo || browserPreview ? '' : `<button type="button" class="menu-refresh" data-refresh-models="${escape(provider)}" data-ctx="${ctx}" ${entry.refreshing ? 'disabled' : ''} title="${escape(t('重新向 {agent} 读取模型列表', { agent: name }))}">${entry.refreshing ? t('正在读取…') : read ? t('{time}读取 · 刷新', { time: read }) : t('刷新')}</button>`;
  if (entry.loading) return `<div class="menu-pop ${place}" role="menu"><p class="menu-empty">${t('正在读取 {agent} 的模型…', { agent: escape(name) })}</p></div>`;
  if (!list.length) return `<div class="menu-pop ${place}" role="menu"><div class="menu-label model-menu-head"><span>${entry.error ? t('读不到 {agent} 的模型列表，可以直接填写', { agent: escape(name) }) : t('{agent} 不提供模型列表，可以直接填写', { agent: escape(name) })}</span>${refresh}</div><label class="menu-field"><span>${t('模型')}</span><input id="${ctx}-model" data-field="model" value="${escape(model)}" placeholder="${t('默认')}" autocomplete="off" spellcheck="false"></label><label class="menu-field"><span>${t('思考强度')}</span><input id="${ctx}-effort" data-field="effort" value="${escape(effort)}" placeholder="${t('默认')}" autocomplete="off" spellcheck="false"></label></div>`;
  const found = chosenModel(provider, model), base = found || list.find(m => m.default);
  const levels = (found ? found.efforts : base?.efforts?.length ? base.efforts : entry.value.efforts) || [];
  const variant = found?.variants ? Object.entries(found.variants).find(([, id]) => id === model)?.[0] : '';
  const level = variant || effort;
  const row = (attrs, label, tag, on, hint, search) => `<button type="button" class="menu-item is-compact ${on ? 'is-selected' : ''}" role="menuitemradio" aria-checked="${!!on}" ${attrs} title="${escape(hint)}"${search ? ` data-search="${escape(search.toLowerCase())}"${modelFilter && !search.toLowerCase().includes(modelFilter) ? ' hidden' : ''}` : ''}><span class="menu-text"><strong>${escape(label)}</strong></span>${tag ? `<span class="menu-tag">${escape(tag)}</span>` : ''}${on ? `<span class="menu-check">${icon('check')}</span>` : ''}</button>`;
  // Long lists (Hermes and OpenCode list every model of every configured provider) can be filtered.
  const filter = list.length > 12 ? `<label class="menu-filter">${icon('search')}<input type="search" id="model-filter" data-model-filter value="${escape(modelFilter)}" placeholder="${escape(t('搜索 {count} 个模型', { count: list.length }))}" aria-label="${escape(t('搜索模型'))}" autocomplete="off" spellcheck="false"></label>` : '';
  return `<div class="menu-pop ${place} model-menu" role="menu" aria-label="${t('模型')}"><div class="menu-label model-menu-head"><span>${t('{agent} 的模型', { agent: escape(name) })}</span>${refresh}</div>${filter}<div class="menu-scroll">${row(`data-pick-model="" data-ctx="${ctx}"`, t('默认'), '', !model, t('沿用 {agent} 自己的配置', { agent: name }))}${list.map(m => row(`data-pick-model="${escape(m.id)}" data-ctx="${ctx}"`, m.label, m.default ? t('默认') : '', found === m, m.description ? `${m.id} · ${m.description}` : m.id, `${m.label} ${m.id} ${m.description || ''}`)).join('')}<p class="menu-empty" data-filter-empty ${modelFilter && !list.some(m => `${m.label} ${m.id} ${m.description || ''}`.toLowerCase().includes(modelFilter)) ? '' : 'hidden'}>${t('没有匹配的模型')}</p></div>${levels.length ? `<div class="menu-divider"></div><div class="menu-label">${t('思考强度')}</div><div class="effort-row" role="radiogroup" aria-label="${t('思考强度')}">${(found?.variants ? levels : ['', ...levels]).map(l => `<button type="button" class="effort-option ${level === l ? 'is-selected' : ''}" role="radio" aria-checked="${level === l}" data-pick-effort="${l}" data-ctx="${ctx}">${l ? escape(effortLabel(l, provider)) : t('默认')}</button>`).join('')}</div>` : ''}</div>`;
}

/** Running calls in a conversation and everything beneath it: what 停止 would end. */
const runningUnder = node => [...(node.latest?.state === 'running' ? [node] : []), ...node.children.flatMap(runningUnder)];
const runningIn = target => { const node = view.nodes.get(target); if (node) return runningUnder(node); const s = view.sessions.find(x => x.target === target); return s ? s.children.flatMap(runningUnder) : []; };
const isStopping = node => { for (let n = node; n; n = n.parent) if (stopping.has(n.id) || stopping.has(n.owner)) return true; return false; };
const draftKey = target => data.home + ':' + target;
const chatTarget = () => workerId || chatTargets.get(selected) || current()?.target || current()?.id;
let status = 'connecting', error = '', isDemo = false, python = '', detailId = null;
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const json = value => escape(JSON.stringify(value, null, 2));
// Inline code that names a file becomes a link into the side panel (as file references are in Codex).
DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node.nodeName === 'CODE' && node.parentNode?.nodeName !== 'PRE' && looksLikePath(node.textContent)) node.setAttribute('data-path', node.textContent.trim());
});
// Links may be web or mail addresses, file URLs, drive paths (D:\x) or relative paths with a line
// (src/a.py:42); any other scheme (javascript:, data:, ...) is dropped.
const LINKS = /^(?:(?:https?|mailto|file):|[a-z]:(?:[\\/]|%5c)|(?![a-z][a-z0-9+.-]*:(?!\d)))/i;  // marked writes \ as %5C.
// In a link target such as (D:\work\.cache\a.md), Markdown reads \. as an escaped dot and drops the
// backslash; Windows takes forward slashes, so drive paths in link targets use them.
const slashed = text => text.replace(/(\]\(\s*<?)([a-zA-Z]:\\[^\n)>]*)/g, (all, open, file) => open + file.replaceAll('\\', '/'));
function markdown(value) {
  return DOMPurify.sanitize(marked.parse(slashed(String(value || '')), { breaks: true }), { FORBID_TAGS: ['img', 'video', 'audio', 'iframe', 'form', 'input', 'button', 'style'], FORBID_ATTR: ['style'], ALLOWED_URI_REGEXP: LINKS });
}
const logo = (provider, label) => `<img class="provider-logo" src="../assets/agents/${logoFile(provider)}" alt="${escape(label ?? (providers[provider] || provider || 'Agent'))}">`;
const stamp = value => value ? new Date(value).toLocaleString(locale(), { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const badge = (state, label = t(labels[state] || state), kind = state) => {
  const symbol = {waiting:'approval',approval:'approval',question:'question',delegated:'clock',completed:'check',failed:'alert',cancelled:'minus',interrupted:'pause'}[kind];
  return `<span class="state ${escape(state)}">${state === 'running' ? '<span class="state-dot"></span>' : `<span class="state-symbol">${icon(symbol || 'clock')}</span>`}${escape(t(label))}</span>`;
};
const current = () => view.sessions.find(s => s.id === selected);
const summary = counts => `${counts.running ? `<span class="blue">${t('{count} 运行', { count: counts.running })}</span>` : ''}${counts.waiting ? `<span class="amber">${t('{count} 等你', { count: counts.waiting })}</span>` : ''}${counts.completed ? `<span>${t('{count} 已返回', { count: counts.completed })}</span>` : ''}`;
function sessionSummary(session) {
  const workers = session.members.filter(n => session.external || n.id !== session.id);
  if(session.native?.busy && !workers.some(n=>['running','waiting'].includes(n.state)))return `<span class="blue">${t('主控处理中')}</span>`;
  if (workers.length) return summary(count(workers));
  return `<span class="${session.state === 'running' ? 'blue' : session.state === 'waiting' ? 'amber' : ''}">${t('主控{state}', { state: escape(t(labels[session.state] || session.state || '准备中')) })}</span>`;
}
let themeChoice = (() => { try { return localStorage.getItem('autoswarm-desk-theme') || 'system'; } catch { return 'system'; } })();
let searchOpen = false;
const darkQuery = matchMedia('(prefers-color-scheme: dark)');
const isDark = () => themeChoice === 'dark' || (themeChoice === 'system' && darkQuery.matches);
function applyTheme() {
  document.documentElement.dataset.theme = isDark() ? 'dark' : 'light';
  try { localStorage.setItem('autoswarm-desk-theme', themeChoice); } catch { /* Preview without storage. */ }
}
darkQuery.addEventListener('change', () => { if (themeChoice === 'system') { applyTheme(); render(); } });
applyTheme();
async function chooseTheme(value) {
  themeChoice = value; applyTheme(); render();
  if (settings.open) renderSettings();
  if (desk.theme) { try { await desk.theme(value); } catch (failure) { showToast(failure.message); } }
}
// Desk's own language: Electron keeps it in preferences, the browser preview in localStorage (or ?lang=).
setLanguage((() => { try { return new URLSearchParams(location.search).get('lang') || localStorage.getItem('autoswarm-desk-language') || 'en'; } catch { return 'en'; } })());
let zoomFactor = 1;
/** Text in index.html that exists before the first render. */
function applyStaticText() {
  const set = (selector, attribute, text) => { for (const el of document.querySelectorAll(selector)) attribute ? el.setAttribute(attribute, text) : (el.textContent = text); };
  set('.titlebar-toggle', 'title', t('折叠或展开侧栏')); set('.titlebar-toggle', 'aria-label', t('折叠或展开侧栏'));
  set('[data-window="minimize"]', 'aria-label', t('最小化')); set('[data-window="maximize"]', 'aria-label', t('最大化')); set('[data-window="close"]', 'aria-label', t('关闭'));
  set('#settings h2', '', t('连接与设置')); set('#settings [data-close="settings"]', 'aria-label', t('关闭设置'));
}
applyStaticText();
async function chooseLanguage(value) {
  setLanguage(value);
  try { localStorage.setItem('autoswarm-desk-language', language()); } catch { /* Preview without storage. */ }
  view = project(data); applyStaticText(); render(); if (settings.open) renderSettings();
  if (desk.language) { try { await desk.language(language()); } catch (failure) { showToast(failure.message); } }
}
/** A newer Desk: downloaded (installs when Desk quits, or restart now) or, for a ZIP copy, a link to it. */
function updateButton() {
  if (!update || isDemo || !['ready', 'available'].includes(update.kind)) return '';
  const ready = update.kind === 'ready', version = escape(update.version || '');
  const text = ready ? t('重启并更新到 {version}', { version }) : t('新版本 {version}，去下载', { version });
  const tip = ready ? t('新版本已下载，退出 Desk 时自动安装；有 Agent 正在运行时等到下次退出。') : t('打开新版本的下载页面');
  return `<button class="settings-button update-button" data-action="${ready ? 'update-install' : 'update-open'}" title="${escape(tip)}">${icon('download')}<span class="sidebar-text">${text}</span></button>`;
}
const appearanceSection = () => `<div class="settings-section appearance"><label>${t('外观')}</label><div class="theme-choices" role="radiogroup" aria-label="${t('外观')}">${[['system','跟随系统','monitor'],['light','浅色','sun'],['dark','深色','moon']].map(([value,text,glyph])=>`<button type="button" role="radio" aria-checked="${themeChoice===value}" data-theme-choice="${value}">${icon(glyph)}<span>${t(text)}</span></button>`).join('')}</div><p>${t(mac ? '深色模式也会用于窗口背景，跟随系统时随 macOS 的外观切换。' : '深色模式也会用于窗口背景，跟随系统时随 Windows 的应用主题切换。')}</p>`
  + `<div class="settings-row"><span class="settings-row-label">${t('语言')}<small>${t('只改变 Desk 自己的界面；Agent 写什么语言就显示什么语言。')}</small></span><div class="segmented" role="radiogroup" aria-label="${t('语言')}">${LANGUAGES.map(([value, name]) => `<button type="button" role="radio" aria-checked="${language() === value}" data-language="${value}" lang="${value === 'zh' ? 'zh-CN' : 'en'}">${name}</button>`).join('')}</div></div>`
  + (desk.zoom ? `<div class="settings-row"><span class="settings-row-label">${t('缩放')}<small>${t(mac ? '⌘ + = 放大，⌘ + - 缩小，⌘ + 0 还原' : 'Ctrl + = 放大，Ctrl + - 缩小，Ctrl + 0 还原')}</small></span><div class="zoom-control"><button type="button" class="zoom-step" data-zoom="-1" aria-label="${t('缩小')}" ${zoomFactor <= 0.8 ? 'disabled' : ''}>−</button><output class="zoom-value" aria-live="polite">${Math.round(zoomFactor * 100)}%</output><button type="button" class="zoom-step" data-zoom="1" aria-label="${t('放大')}" ${zoomFactor >= 2 ? 'disabled' : ''}>+</button><button type="button" class="text-button" data-zoom="0" ${zoomFactor === 1 ? 'disabled' : ''}>${t('还原')}</button></div></div>` : '')
  + '</div>';
const showToast = text => { const el = document.querySelector('#toast'); el.textContent = text; el.classList.add('visible'); setTimeout(() => el.classList.remove('visible'), 2800); };

function render(follow = false) {
  const questionFocus = document.activeElement?.closest?.('.native-question-form');
  const focusedAnswer = questionFocus ? { key: questionFocus.dataset.questionCall + '/' + questionFocus.dataset.questionId, name: document.activeElement.name, value: document.activeElement.value, caret: document.activeElement.selectionStart } : null;
  const oldPane = document.querySelector('.conversation-scroll');
  const stick = follow && oldPane && oldPane.scrollHeight - oldPane.scrollTop - oldPane.clientHeight < 40;
  const focusedComposer = ['assistant-input','chat-input','session-input','session-model','session-effort','session-provider','model-filter'].includes(document.activeElement?.id);
  const composerId = focusedComposer ? document.activeElement.id : null;
  const composerCaret = focusedComposer ? document.activeElement.selectionStart : null;
  const focusedSearch = document.activeElement?.id === 'search';
  const caret = focusedSearch ? document.activeElement.selectionStart : null;
  const scroll = document.querySelector('.conversation-scroll')?.scrollTop || 0;
  const dockScroll = document.querySelector('.dock-body')?.scrollTop || 0;
  const openDetails = [...app.querySelectorAll('details[open]')].map(el => el.dataset.key);
  const closedDetails = new Set([...app.querySelectorAll('details:not([open])')].map(el => el.dataset.key).filter(Boolean));
  copies.clear(); copySeq = 0;
  app.classList.toggle('collapsed', collapsed);
  app.classList.toggle('worker-page', !!workerId);
  app.innerHTML = `${workerId ? workerSidebar() : sidebar()}<main>${workerId && view.nodes.has(workerId) ? '' : header()}${browserPreview ? `<div class="preview-banner" role="status"><span>${t('浏览器预览 · 当前为示例数据，可切换会话、展开子任务查看')}</span><button data-action="settings">${t('连接真实记录 ↗')}</button></div>` : isDemo ? `<div class="preview-banner demo-banner" role="status"><span>${t('演示空间 · 示例数据，不连接真实 Agent，也不改动你的记录')}</span><button data-action="exit-demo">${t('退出演示，回到我的记录')}</button></div>` : ''}${status !== 'ready' ? `<div class="connection-banner ${status === 'error' ? 'error' : ''}" role="status">${escape(status === 'error' ? t(error) : t('正在读取本地记录…'))}<button data-action="reconnect">${t('重新连接')}</button></div>` : ''}${workerId && view.nodes.has(workerId) ? workerPage(view.nodes.get(workerId)) : composing ? newSessionPage() : overview ? overviewPage() : current() ? sessionPage(current()) : emptyPage()}</main>`;
  const pane = document.querySelector('.conversation-scroll'); if (pane) pane.scrollTop = scroll;
  const dock = document.querySelector('.dock-body'); if (dock) dock.scrollTop = dockScroll;
  for (const el of app.querySelectorAll('details')) { if (openDetails.includes(el.dataset.key)) el.open = true; else if (el.open && closedDetails.has(el.dataset.key)) el.open = false; }
  for (const form of app.querySelectorAll('.native-question-form')) {
    const key = form.dataset.questionCall + '/' + form.dataset.questionId;
    const draft = questionDrafts.get(data.home + ':' + key);
    if (draft) for (const field of form.elements) {
      if (!field.name) continue;
      if (['radio','checkbox'].includes(field.type)) field.checked = (draft[field.name] || []).includes(field.value);
      else if (draft[field.name]) field.value = draft[field.name][0];
    }
    if (focusedAnswer?.key === key) {
      const field = [...form.elements].find(f => f.name === focusedAnswer.name && (!['radio','checkbox'].includes(f.type) || f.value === focusedAnswer.value));
      if (field) { field.focus({preventScroll:true}); if (typeof focusedAnswer.caret === 'number') field.setSelectionRange(focusedAnswer.caret,focusedAnswer.caret); }
    }
  }
  if (stick && pane) pane.scrollTop = pane.scrollHeight;
  if (focusedSearch) { const input = document.querySelector('#search'); input.focus(); input.setSelectionRange(caret, caret); }
  if (focusedComposer) { const input = document.getElementById(composerId); if (input) { input.focus(); try { if (typeof composerCaret === 'number') input.setSelectionRange(composerCaret, composerCaret); } catch { /* select */ } } }
  if (settings.open && !settings.contains(document.activeElement)) renderSettings();
  else if (settings.open) refreshLiveSettings();
  if (detail.open && detailId) renderDetail(detailId);
  requestAnimationFrame(ensureStreams);
}

function sidebar() {
  const sessions = view.sessions.filter(s => !search || [s.name, workspaceOf(s), ...s.members.map(n => n.provider)].join(' ').toLowerCase().includes(search.toLowerCase()));
  const waiting = [...view.nodes.values()].reduce((n, node) => n + node.requests.filter(r => r.needsUser).length, 0);
  const groups = new Map();
  for (const s of sessions) { const w = workspaceOf(s); if (!groups.has(w)) groups.set(w, []); groups.get(w).push(s); }
  function item(s) {
    const on = selected === s.id && !overview && !composing, asks = s.members.reduce((n, node) => n + node.requests.filter(r => r.needsUser).length, 0);
    const state = asks ? `<span class="session-state is-waiting">${t('{count} 等你', { count: asks })}</span>` : !s.done ? `<span class="session-state is-running" title="${t('进行中')}"><i></i></span>` : `<time>${escape(relTime(s.updated))}</time>`;
    return `<button class="session-item ${s.done ? 'is-done' : ''} ${on ? 'active' : ''}" data-session="${escape(s.id)}" title="${escape(s.name)}" ${on ? 'aria-current="page"' : ''}><span class="session-line">${logo(s.provider)}<strong>${escape(s.name)}</strong>${state}</span></button>`;
  }
  // Few folders: all open. Many: the three most recent, plus the current and any still running.
  // The icon rail (collapsed, or a narrow window) shows every session as its agent's logo.
  const recent = new Set([...groups.keys()].slice(0, 3)), rail = collapsed || window.innerWidth <= 860;
  function group([w, list]) {
    const here = list.some(s => s.id === selected && !overview && !composing), live = list.some(s => !s.done);
    const open = search || rail ? true : workspaceOpen.has(w) ? workspaceOpen.get(w) : groups.size <= 4 || here || live || recent.has(w);
    return `<div class="workspace-group ${open ? 'is-open' : ''} ${here ? 'has-current' : ''}"><button class="workspace-row" data-workspace-toggle="${escape(w)}" aria-expanded="${open}" title="${escape(w ? displayPath(w) : t('未知工作目录'))}">${icon(open ? 'folder-open' : 'folder')}<span class="sidebar-text">${escape(w ? baseName(w) : t('其他'))}</span>${!open && live ? '<i class="group-live" aria-hidden="true"></i>' : ''}</button>${open ? `<div class="workspace-sessions">${list.map(item).join('')}</div>` : ''}</div>`;
  }
  const s = current(), context = isDemo ? [t('演示空间'), t('示例数据 · 不连接真实 Agent')] : status !== 'ready' ? ['AutoSwarm', t('等待连接')] : composing ? [t('新会话'), t('实时同步')]
    : overview ? [t('私人助理'), t('{count} 个会话 · 实时同步', { count: view.sessions.length })] : s ? [t('本会话'), t('{count} 个 Agent · 实时同步', { count: s.members.length })] : ['AutoSwarm', t('实时同步')];
  return `<aside><button class="new-session ${composing ? 'active' : ''}" data-action="new-session" title="${t('新会话：选主 Agent、模型和工作目录')}">${icon('plus-circle')}<span class="sidebar-text">${t('新会话')}</span></button>
    <button class="assistant-link ${overview && !composing ? 'active' : ''}" data-action="overview" title="${t('私人助理')}">${brand(18)}<span class="sidebar-text"><strong>${t('私人助理')}</strong></span>${waiting ? `<span class="count-tag sidebar-text" title="${t('{count} 件等你决定', { count: waiting })}">${t('{count} 等你', { count: waiting })}</span><i class="attention-dot" aria-hidden="true"></i>` : ''}</button>
    <div class="nav-label"><span>${t('工作区')}</span><button class="nav-icon" data-action="search" title="${t('查找会话')}" aria-label="${t('查找会话')}" aria-expanded="${searchOpen || !!search}">${icon('search')}</button><button class="nav-icon" data-action="new-session" title="${t('新会话')}" aria-label="${t('新会话')}">${icon('plus')}</button></div>
    ${searchOpen || search ? `<div class="search-box"><span class="search-glyph">${icon('search')}</span><input id="search" placeholder="${t('查找会话或目录')}" aria-label="${t('查找会话')}" value="${escape(search)}"></div>` : ''}
    <div class="session-list">${[...groups.entries()].map(group).join('') || `<p class="nav-empty">${search ? t('没有匹配的会话') : t('还没有会话')}</p>`}</div>
    <div class="sidebar-bottom">${updateButton()}<div class="source-info" title="${escape(data.home || '')}"><span class="connection-dot ${status === 'ready' ? 'connected' : ''}"></span><span class="sidebar-text"><strong>${escape(context[0])}</strong><small>${escape(context[1])}</small></span></div><button class="settings-button" data-action="settings" title="${t('连接与设置')}">${icon('settings')}<span class="sidebar-text">${t('设置')}</span></button></div></aside>`;
}

function headerControls() {
  // The side panel's toggle, once a link has been opened there.
  const panel = preview?.count ? `<button class="icon-button preview-toggle ${preview.collapsed ? '' : 'is-on'}" data-action="preview" aria-pressed="${!preview.collapsed}" title="${preview.collapsed ? t('展开侧栏 · {count} 个标签页', { count: preview.count }) : t('收起侧栏')}" aria-label="${preview.collapsed ? t('展开侧栏 · {count} 个标签页', { count: preview.count }) : t('收起侧栏')}">${icon('panel')}</button>` : '';
  // Settings live at the bottom of the sidebar only; the demo has its own way back here.
  return `<div class="header-right">${isDemo && !browserPreview ? `<button class="demo-badge" data-action="exit-demo" title="${t('退出演示，回到你的记录')}">${t('演示')} · ${t('退出')}</button>` : ''}${panel}<button class="icon-button theme-toggle" data-action="theme" title="${isDark() ? t('切换到浅色') : t('切换到深色')}" aria-label="${isDark() ? t('切换到浅色') : t('切换到深色')}">${icon(isDark() ? 'sun' : 'moon')}</button></div>`;
}
function header() {
  if (composing) return `<div class="page-header is-blank"><div class="page-heading"></div>${headerControls()}</div>`;
  if (overview) {
    const provider = view.assistant?.provider || assistantSettings.provider || 'claude-code', task = view.assistant?.latest.task || {};
    const tuned = [task.model || assistantSettings.model, task.effort || assistantSettings.effort].filter(Boolean).join(' · ');
    const place = task.workspace || assistantSettings.workspace;
    return `<div class="page-header assistant-header"><div class="page-heading"><div class="heading-line"><h1>${t('私人助理')}</h1><div class="header-chips"><button class="meta-chip" data-action="settings" title="${t('助理设置')}">${logo(provider)}<span>${escape(providers[provider] || provider)}${tuned ? ` · ${escape(tuned)}` : ''}</span></button>${place ? `<span class="meta-chip" title="${escape(displayPath(place))}">${icon('folder')}<span>${escape(baseName(place))}</span></span>` : ''}</div></div>${tabBar()}</div>${headerControls()}</div>`;
  }
  const s = current();
  if (!s) return `<div class="page-header"><div class="page-heading"><h1>${t('连接你的 Agent 团队')}</h1></div>${headerControls()}</div>`;
  const tuned = !s.external ? [s.latest.task.model, s.latest.task.effort].filter(Boolean).join(' · ') : '', place = workspaceOf(s);
  const mode = !s.external && (data.agents[s.id]?.permission || s.latest.task.permission);
  return `<div class="page-header"><div class="page-heading"><div class="heading-line"><h1 title="${escape(s.name)}">${escape(s.name)}</h1><div class="header-chips"><span class="meta-chip">${logo(s.provider)}<span>${s.external ? t('外部主控 · ') : t('主控 · ')}${escape(providers[s.provider] || s.provider)}${tuned ? ` · ${escape(tuned)}` : ''}</span></span>${place ? `<span class="meta-chip" title="${escape(displayPath(place))}">${icon('folder')}<span>${escape(baseName(place))}</span></span>` : ''}${mode ? permissionChip(s.provider, mode, 'session-permission', 'menu-down', t('这个会话的权限 · 从下一条消息开始生效')) : ''}</div></div>${tabBar()}</div>${headerControls()}</div>`;
}

function emptyPage() {
  return `<div class="empty-page"><div class="empty-symbol">${brand(44)}</div><span class="eyebrow">YOUR AGENTS, TOGETHER</span><h2>${search ? t('没有找到会话') : t('让每一次协作，都清晰可见。')}</h2><p>${t('选择 AutoSwarm 的记录目录，查看正在运行的 Agent、<br>需要你决定的事项，以及每个任务的最终回复。')}</p><div class="empty-actions"><button class="primary" data-action="choose-home">${t('选择记录目录')} <span>↗</span></button><button class="secondary" data-action="demo">${t('先看看演示')}</button></div>${data.home ? `<div class="empty-path">${data.exists === false ? t('目录尚不存在') : t('当前目录暂无调用记录')}<code>${escape(data.home)}</code></div>` : ''}<div class="empty-footnote">${t('从私人助理开始真实对话，任务由 AutoSwarm 管理。')}</div></div>`;
}

const previewDiff = text => text.split('\n').map(line => `<span class="${line.startsWith('+ ') ? 'addition' : line.startsWith('- ') ? 'deletion' : ''}">${escape(line)}</span>`).join('\n');
function requestCard(r) {
  const req = r.request;
  const described = describeRequest(req), title = described.title, commandText = described.command, reason = described.reason;
  const form = req.kind === 'interaction_required' ? questionForm(r, title) : '';
  const canDecide = req.kind === 'approval_required' && (req.scopes || []).includes('once');
  const canSession = canDecide && (req.scopes || []).includes('session');
  const waited = req.created_at ? elapsed({ task: { created_at: req.created_at } }, nowMs()) : '';
  const shortCommand = commandText && commandText.length <= 72 && !commandText.includes('\n');
  const hint = t(r.needsUser ? (canDecide ? '决定会以你的身份（--by user）交给此 Agent' : form ? '回答会交给此 Agent' : '这个问题需要在原会话中回答') : '父 Agent 正在运行，会由它代批；你也可以直接决定');
  return `<section id="request-${escape(r.call_id)}-${escape(r.id)}" class="decision-card ${r.needsUser ? '' : 'delegated-card'} ${flash && flash.key === r.call_id + '/' + r.id && Date.now() < flash.until ? 'is-flash' : ''}"><div class="decision-meta">${icon('flag')}<strong>${r.needsUser ? t('需要你决定') : t('等待上级代批')}</strong><span class="decision-source">${logo(r.node.provider)}${escape(providers[r.node.provider] || r.node.provider)} · ${escape(r.node.name)}${waited && waited !== '—' ? ` · ${t('已等 {time}', { time: escape(waited) })}` : ''}</span><button class="text-button decision-raw" data-request="${escape(r.call_id + '/' + r.id)}">${t('原始请求')}</button></div><h3>${escape(title)}${described.subject ? ` <code class="inline-command" title="${escape(described.subject)}">${escape(described.subject)}</code>` : ''}${shortCommand ? ` <code class="inline-command">${escape(commandText)}</code>` : ''}</h3>${commandText && !shortCommand ? `<pre class="permission-command">${escape(commandText)}</pre>` : ''}${described.preview ? `<pre class="permission-command permission-preview ${described.previewKind === 'diff' ? 'diff' : ''}">${described.previewKind === 'diff' ? previewDiff(described.preview) : escape(described.preview)}</pre>` : ''}${reason ? `<p class="decision-reason">${escape(reason)}</p>` : ''}${form}${canDecide ? `<div class="decision-footer"><div class="decision-buttons"><button class="primary" data-decision="approve" data-call="${escape(r.call_id)}" data-rid="${escape(r.id)}">${t('允许本次')}</button>${canSession ? `<button class="secondary" data-decision="approve" data-scope="session" data-call="${escape(r.call_id)}" data-rid="${escape(r.id)}" title="${t('这个会话里同类操作不再问你')}">${t('本会话都允许')}</button>` : ''}<button class="secondary" data-decision="deny" data-call="${escape(r.call_id)}" data-rid="${escape(r.id)}">${t('拒绝')}</button></div><span class="decision-hint">${escape(hint)}</span></div>` : form ? '' : `<div class="decision-footer"><span class="decision-hint">${escape(hint)}</span></div>`}</section>`;
}
const nowMs = () => isDemo ? Date.parse(data.observed_at || '2026-11-01T09:16:00Z') : Date.now();

const displayPath = value => String(value || '').replace(/^\\\\\?\\/, '');
const clock = at => { const d = new Date(typeof at === 'number' ? at : Date.parse(at)); return isNaN(d) ? '' : d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }); };
const remember = text => { const key = 'c' + (++copySeq); copies.set(key, String(text)); return key; };
function copyButton(text, label = '复制') {
  if (!text || !String(text).trim()) return '';
  const key = 'c' + (++copySeq); copies.set(key, String(text));
  return `<button class="action-icon" data-copy="${key}" title="${t(label)}" aria-label="${t(label)}">${icon('copy')}</button>`;
}
async function copyText(text) {
  if (desk.copy) return desk.copy(text);
  try { await navigator.clipboard.writeText(text); }
  catch {
    const area = Object.assign(document.createElement('textarea'), { value: text });
    area.style.cssText = 'position:fixed;opacity:0;pointer-events:none'; document.body.append(area); area.select();
    const ok = document.execCommand('copy'); area.remove(); if (!ok) throw Error(t('复制失败'));
  }
}
function userBubble(text, at, receipt = '') {
  const when = at ? (typeof at === 'number' ? clock(at) : stamp(at)) : '';
  return `<div class="chat-user"><div class="user-message">${escape(text)}</div><div class="message-actions user-actions">${when || receipt ? `<span class="action-meta">${escape([when, receipt].filter(Boolean).join(' · '))}</span>` : ''}${copyButton(text)}</div></div>`;
}
/** One agent turn: a single header, then the body (process row, live line, reply, actions). */
function agentTurn({ body }) {
  // DeepSeek's turns carry no avatar row: the header already names the agent.
  return `<div class="agent-message"><div class="message-content">${body}</div></div>`;
}
function replyActions(text, meta = '', call = null) {
  const copy = copyButton(text);
  const quote = text && String(text).trim() ? `<button class="action-icon" data-quote="${escape(remember(text))}" title="${t('引用到输入框')}" aria-label="${t('引用到输入框')}">${icon('quote')}</button>` : '';
  const trace = call ? `<button class="action-icon" data-trace="${escape(call.id)}" title="${t('查看这一轮的轨迹')}" aria-label="${t('查看这一轮的轨迹')}">${icon('layers')}</button>` : '';
  const reveal = call && !isDemo && !browserPreview ? `<button class="action-icon" data-reveal="${escape(call.id)}" title="${t('打开这一轮的记录文件夹')}" aria-label="${t('打开记录文件夹')}">${icon('folder')}</button>` : '';
  return copy || meta || trace ? `<div class="message-actions">${copy}${quote}${trace}${reveal}${meta ? `<span class="action-meta">${escape(meta)}</span>` : ''}</div>` : '';
}
function workingLine(state, text) {
  return `<div class="working-line">${badge(state)}<span>${escape(text)}</span></div>`;
}
/** Imported native turns (Codex): the agent's own grouping, rendered like AutoSwarm turns. */
function nativeConversation(node) {
  const summary = node.native;
  if (!summary) return '';
  const native = summary.turns ? summary : nativeCache.get(node.target || node.id)?.data;
  if (!native) return `<div class="stream-fallback">${nativeCache.get(node.target || node.id)?.error ? escape(t('原生对话读取失败：{error}', { error: nativeCache.get(node.target || node.id).error })) : t('正在读取原生对话…')}</div>`;
  const cutoff = node.calls?.[0] ? Date.parse(node.calls[0].task.created_at) : Infinity;
  const list = (native.turns || []).filter(t => (t.started_at ?? t.user?.at ?? 0) < cutoff);
  const role = view.sessions.some(s => s.id === node.id) ? '主控' : '';
  const caption = native.has_more ? `<p class="history-caption">${t('只显示最近的对话，更早的内容保存在原生会话中')}</p>` : '';
  if (!list.length && native.messages?.length) return caption + native.messages.filter(m => m.at < cutoff).map(m => m.role === 'user' ? userBubble(m.text, m.at)
    : agentTurn({ provider: node.provider, role, at: m.at, body: `<div class="markdown turn-reply">${markdown(m.text)}</div>${replyActions(m.text)}` })).join('');
  return caption + list.map((t, index) => {
    const running = t.status === 'in_progress' && index === list.length - 1;
    const head = running ? tr('进行中') : t.status === 'cancelled' ? tr('已中断') + (t.duration_ms ? ` · ${tr('用时 {time}', { time: durationText(t.duration_ms) })}` : '') : t.duration_ms ? tr('已处理 {time}', { time: durationText(t.duration_ms) }) : tr('已处理');
    // Running: interim text open, tools folded (as Codex shows it); finished: one folded work row.
    const work = running && t.items.length ? `<div class="process-turn is-live"><div class="live-steps">${liveSteps(t.items, `native-${node.id}-${t.id}`, markdown)}</div></div>`
      : t.items.length || t.omitted ? `<div class="process-turn">${workSummary(t.items, { head, key: `native-${node.id}-${t.id}`, omitted: t.omitted || 0, prose: markdown })}</div>` : '';
    const live = running ? workingLine('running', latestActivity(t.items) || tr('正在处理')) : '';
    const reply = t.final ? `<div class="markdown turn-reply">${markdown(t.final.text)}</div>${replyActions(t.final.text)}` : '';
    const error = t.error ? `<div class="error-text">${escape(t.error)}</div>` : t.status === 'failed' && !t.final ? `<div class="error-text">${tr('本轮失败')}</div>` : '';
    const at = t.final?.at ?? t.items[0]?.at ?? t.started_at;
    return `<section class="turn">${t.user ? userBubble(t.user.text, t.user.at) : ''}${work || live || reply || error ? agentTurn({ provider: node.provider, role, at, body: work + live + reply + error }) : ''}</section>`;
  }).join('');
}
// Input Desk sent on the user's behalf (auto wake-up, trigger rules) reads as a system note, not as the user typing.
const AUTOMATIC = /^\[Desk (自动唤醒|规则「([^」]+)」)\] /;
function systemNote(text) {
  const auto = String(text).match(AUTOMATIC);
  return auto ? `<div class="system-note">${brand(16)}<span><strong>${auto[2] ? t('Desk 规则「{name}」', { name: escape(auto[2]) }) : t('Desk 自动唤醒助理')}</strong>${escape(String(text).slice(auto[0].length))}</span></div>` : '';
}
function userMail(node, call) {
  return Object.values(data.messages).filter(m => m.message.kind === 'user_message' && m.message.to === (node.target || node.id) && (call ? m.delivery?.call_id === call.id : !m.delivery?.call_id)).sort((a,b)=>a.message.created_at.localeCompare(b.message.created_at)).map(m => `${systemNote(m.message.content) || userBubble(m.message.content,m.message.created_at,t((m.delivery?.mechanism==='native_input' && m.delivery?.state==='submitted' ? '已插入本轮' : m.delivery?.mechanism==='native_followup' ? (m.message.interrupt ? '打断后送达' : '本轮结束后送达') : ({submitted:'已送达',delivered:'已送达',needs_attention:'未送达'})[m.delivery?.state]) || (m.delivery?.reason==='native_turn_active' ? '等待本轮结束' : '等待送达')))}${m.delivery?.state === 'needs_attention' ? `<p class="chat-error" role="alert">${escape(t(m.delivery.error || '发送未成功，请重试。'))}</p>` : ''}`).join('');
}
/** AutoSwarm calls: one turn per call, process folded, result.json as the reply. */
function conversation(node, worker = false) {
  const assistant = node === view.assistant;
  const role = !worker && !assistant && view.sessions.some(x => x.id === node.id) ? '主控' : '';
  return nativeConversation(node) + node.calls.map((call, i) => {
    const stream = streamCache.get(call.id) || call.stream;
    const prompt = assistant && AUTOMATIC.test(call.task.prompt) ? systemNote(call.task.prompt)
      : !call.task.message_only && !(worker && i === 0)
      ? (i === 0 && node.owner === view.assistant?.id ? `<div class="handoff-note"><div>${brand(16)}<strong>${t('私人助理')}</strong><span>${t('交给 {agent} · {time}', { agent: escape(providers[node.provider] || node.provider), time: escape(stamp(call.task.created_at)) })}</span></div><p>${escape(call.task.prompt)}</p></div>` : userBubble(call.task.prompt, call.task.created_at))
      : '';
    const state = call.id === node.latest.id ? node.state : call.state;
    // While the turn's steps are on screen, the status line need not repeat the latest one.
    const shown = turns(stream?.events || []).at(-1), stepsShown = shown && !shown.ended && shown.items.length;
    const live = !call.result ? workingLine(state, call.state === 'running' && isStopping(node) ? t('正在停止…') : call.handle?.phase === 'startup' && call.state === 'running' ? t('正在启动 Agent') : stepsShown ? '' : activity(stream) || (call.state === 'running' ? t('正在处理你的任务') : t('可以发送消息继续这段对话'))) : '';
    const took = call.result ? elapsed(call) : '—';
    const errorText = call.result?.error ? (typeof call.result.error === 'string' ? call.result.error : JSON.stringify(call.result.error)) : '';
    // A stopped turn ends by closing the native process; that closure is not a failure to report.
    const halted = call.result?.outcome === 'cancelled';
    const error = errorText && !halted ? `<div class="error-text">${escape(errorText)}</div>` : '';
    const reply = call.result?.content ? `<div class="markdown turn-reply">${markdown(call.result.content)}</div>` : '';
    const stopped = halted ? `<div class="turn-note" ${errorText ? `title="${escape(t('运行记录：{log}', { log: errorText }))}"` : ''}>${icon('stop')}<span>${call.result.content ? t('这一轮已停止。发消息可以在原会话里接着做。') : t('这一轮已停止，没有最终回复。发消息可以在原会话里接着做。')}</span></div>` : '';
    const actions = call.result ? replyActions(call.result.content, [clock(call.task.created_at), took !== '—' ? t('用时 {time}', { time: took }) : ''].filter(Boolean).join(' · '), call) : '';
    const body = `${processView(call, stream, worker, limits.get(call.id) || 0, { markdown })}${!call.task.message_only ? userMail(node, call) : ''}${live}${reply}${stopped}${error}${assistant ? handoffs(call) : ''}${actions}`;
    return `<section class="turn" id="turn-${escape(call.id)}">${prompt}${call.task.message_only ? userMail(node, call) : ''}${agentTurn({ provider: call.task.provider, assistant, role, at: call.task.created_at, body })}</section>`;
  }).join('') + userMail(node);
}

/** 交接: sessions this assistant turn created, continued, or handed a message to. */
function handoffs(call) {
  const owner = view.assistant?.id;
  if (!owner) return '';
  const start = Date.parse(call.handle?.started_at || call.task.created_at), end = call.result?.finished_at ? Date.parse(call.result.finished_at) : Infinity;
  const within = at => { const t = Date.parse(at); return t >= start - 1000 && t <= end + 2000; };
  const byUser = c => Object.values(data.messages).some(m => m.message.kind === 'user_message' && m.delivery?.call_id === c.id);
  const found = new Map();
  for (const s of view.sessions) {
    if (s.external || s.owner !== owner) continue;
    if (within(s.calls[0].task.created_at)) found.set(s.id, { s, verb: '新建会话' });
    else if (s.calls.slice(1).some(c => within(c.task.created_at) && !c.task.message_only && !byUser(c))) found.set(s.id, { s, verb: '继续会话' });
  }
  for (const m of Object.values(data.messages)) {
    if (m.message.from !== owner || m.message.kind === 'user_message' || !within(m.message.created_at)) continue;
    const s = view.sessions.find(x => x.members.some(n => n.id === m.message.to));
    if (s && !found.has(s.id)) found.set(s.id, { s, verb: '转给会话', note: m.message.content });
  }
  if (!found.size) return '';
  return `<div class="handoff-cards">${[...found.values()].map(({ s, verb, note }) => `<button class="handoff-card" data-session="${escape(s.id)}">${logo(s.provider)}<span class="handoff-copy"><small>${t(verb)} · ${escape(providers[s.provider] || s.provider)}</small><strong>${escape(s.name)}</strong>${note ? `<em>${escape(String(note).slice(0, 90))}</em>` : ''}</span><span class="handoff-state">${s.done ? t('已完成') : sessionSummary(s)}</span>${icon('chevron')}</button>`).join('')}</div>`;
}
function messagesFor(members) {
  const ids = new Set(members.map(n => n.id));
  const list = Object.values(data.messages).filter(m => m.message.kind !== 'user_message' && (ids.has(m.message.from) || ids.has(m.message.to)) && m.message.content).sort((a,b)=>a.message.created_at.localeCompare(b.message.created_at));
  if (!list.length) return '';
  const sender = id => { const node=view.nodes.get(id); return node ? `${logo(node.provider)}<strong>${escape(providers[node.provider] || node.provider)}</strong>` : `<strong>${t('主控')}</strong>`; };
  const entry = m => `<article class="peer-chat"><div>${sender(m.message.from)}<span>→</span>${sender(m.message.to)}<time>${escape(stamp(m.message.created_at))}</time></div><p>${escape(m.message.content)}</p></article>`;
  return `<section class="peer-section"><h2>${t('协作消息')}</h2>${list.slice(-4).map(entry).join('')}${list.length>4?`<details class="earlier-peer"><summary>${t('更早的 {count} 条协作消息', { count: list.length - 4 })}</summary>${list.slice(0,-4).map(entry).join('')}</details>`:''}</section>`;
}

// Codex (turn/steer) and Claude Code (stream-json input) take a message into the running
// turn without stopping it; ACP agents such as Cursor have to be interrupted first.
const LIVE_INPUT = new Set(['codex', 'claude-code', 'muse', 'opencode']);
const defaultMode = node => node && LIVE_INPUT.has(node.provider) && ['running', 'waiting', 'delegated'].includes(node.state) ? 'immediate' : 'after_turn';
function composer(node, worker = false) {
  const target = chatTarget(), key = draftKey(target), recipient = view.nodes.get(target) || node;
  const workers = (current()?.members || []).filter(n=>n.id !== current()?.id);
  const placeholder = worker ? t('直接给 {agent} 发消息…', { agent: providers[node.provider] || node.provider }) : target === (node.target || node.id) ? t('对主控 {agent} 说…', { agent: providers[recipient.provider] || recipient.provider }) : t('对 {agent} 说…', { agent: providers[recipient.provider] || recipient.provider });
  const mode = chatModes.get(key) || defaultMode(recipient);
  const live = LIVE_INPUT.has(recipient.provider);
  const busy = ['running','waiting','delegated'].includes(recipient.state) || recipient.native?.busy === true;
  return `<form id="chat-form" class="chat-composer composer-card" data-target="${escape(target)}"><textarea id="chat-input" aria-label="${escape(placeholder)}" placeholder="${escape(placeholder)}" rows="2">${escape(chatDrafts.get(key) || '')}</textarea>${chatErrors.has(key) ? `<p class="chat-error" role="alert">${escape(chatErrors.get(key))}</p>` : ''}<div class="composer-row">${!worker && workers.length ? `<select id="chat-recipient" aria-label="${t('选择对话 Agent')}"><option value="${escape(node.target || node.id)}">@ ${t('主控 · ')}${escape(providers[node.provider] || node.provider)}</option>${workers.map(n=>`<option value="${escape(n.id)}" ${n.id===target?'selected':''}>@ ${escape(providers[n.provider] || n.provider)} · ${escape(n.name)}</option>`).join('')}</select>` : ''}${busy ? `<div class="delivery-modes" role="radiogroup" aria-label="${t('送达方式')}"><label><input type="radio" name="delivery-mode" value="after_turn" ${mode==='after_turn'?'checked':''}>${t('本轮结束后发送')}</label><label title="${live ? t('插进正在进行的这一轮，Agent 不中断，读到后照顾你的要求继续做') : t('先停下正在进行的这一轮，处理你的消息，再接着做原来的任务')}"><input type="radio" name="delivery-mode" value="immediate" ${mode==='immediate'?'checked':''}>${live ? t('立即插话') : t('打断并插话')}</label></div>` : ''}<span class="row-spacer"></span>${stopControl(target)}<button type="submit" class="send-button" aria-label="${t('发送消息')}" ${chatPending.has(key)?'disabled':''}>${icon('arrow-up')}</button></div></form>`;
}
/** The AutoSwarm switch on the new-session page: on, off, or locked until this agent has the Skill. */
function swarmPill(provider) {
  if (!swarmKnown(provider)) return '';
  const name = providers[provider], locked = !skillReady(provider), on = draftSkill(provider), installing = skills?.installing === provider, fixed = !SKILL_SWITCHABLE.has(provider);
  const title = installing ? t('正在为 {agent} 安装 AutoSwarm…', { agent: name }) : locked ? t('{agent} 还没有可用的 AutoSwarm 技能', { agent: name }) : fixed ? t('AutoSwarm 已开启：会话开始时让 {agent} 加载 AutoSwarm 技能（{agent} 不能按会话隐藏技能，所以不能关闭）', { agent: name }) : on ? t('AutoSwarm 已开启：会话开始时让 {agent} 加载 AutoSwarm 技能。点击关闭', { agent: name }) : t('AutoSwarm 已关闭：这个会话里 {agent} 看不到 AutoSwarm 技能。点击开启', { agent: name });
  return `<div class="menu-anchor swarm-anchor"><button type="button" class="swarm-pill is-${locked ? 'locked' : on ? 'on' : 'off'}" data-swarm="${escape(provider)}" aria-pressed="${on}" ${locked ? `aria-haspopup="dialog" aria-expanded="${openMenu === 'swarm'}"` : ''} ${installing || fixed && !locked ? 'aria-disabled="true"' : ''} title="${escape(title)}">${locked ? icon('lock') : brand(16)}<span>${installing ? t('正在安装…') : 'AutoSwarm'}</span></button>${locked && openMenu === 'swarm' ? swarmPopover(provider) : ''}</div>`;
}
function swarmPopover(provider) {
  const name = providers[provider], failure = skills?.errors?.[provider];
  return `<div class="menu-pop swarm-pop menu-down-left" role="dialog" aria-label="${t('安装 AutoSwarm')}">
    <strong class="swarm-pop-title">${escape(t('{agent} 还没有 AutoSwarm 技能', { agent: name }))}</strong>
    <p>${failure ? t('Desk 自动安装没有成功：') : t('Desk 还没能为它安装。')}</p>
    ${failure ? `<div class="swarm-error" role="alert"><p>${escape(t(failure.message))}</p>${failure.detail ? `<pre>${escape(failure.detail)}</pre>` : ''}</div>` : ''}
    <p>${t('可以交给一个 Agent：它会查明原因并完成安装，需要装软件或改配置时先问你。')}</p>
    <div class="swarm-actions"><button type="button" class="primary" data-action="skill-agent" data-host="${escape(provider)}">${t('让 Agent 安装')}</button></div>
  </div>`;
}
async function agentInstall(host) {
  if (isDemo) { showToast(t('这是界面演示，不会启动真实 Agent。')); return; }
  try {
    const started = await desk.skillAgent(host);
    openMenu = null; pendingSession = started.agent_id; openPending();
    showToast(t('已交给 {agent} 安装，装好后开关会自动解锁', { agent: providers[started.helper] || started.helper }));
  } catch (error) { showToast(error.message); }
  render(true);
}
function stopControl(target) {
  const running = runningIn(target);
  if (!running.length) return '';
  const wait = stopping.has(target) || running.every(isStopping);
  const label = wait ? t('正在停止…') : running.length > 1 ? t('停止（{count} 个运行中）', { count: running.length }) : t('停止');
  return `<button type="button" class="stop-button ${wait ? 'is-stopping' : ''}" data-stop="${escape(target)}" title="${escape(label)}" aria-label="${escape(label)}" ${wait ? 'disabled' : ''}>${icon('stop')}</button>`;
}
function sessionPage(s) {
  const requests = s.members.flatMap(n => n.requests).filter(r => r.pending);
  const children = s.children;
  const allChildren = s.members.filter(n => s.external || n.id !== s.id);
  const closed = dockIsClosed(s), halted = allChildren.filter(n => n.state === 'cancelled').length, finished = allChildren.filter(n => ['completed','failed','cancelled'].includes(n.state)).length, live = allChildren.some(n => ['running','waiting','delegated'].includes(n.state));
  const dialogue = tabOf() === 'trace' ? traceView(s.external ? s.members : [s, ...s.members.filter(n => n.id !== s.id)]) : s.external ? nativeConversation(s) + userMail(s) : conversation(s);
  return `<div class="conversation-scroll"><div class="reading-column">${dialogue || `<div class="conversation-start">${logo(s.provider)}<h2>${escape(s.name)}</h2><p>${t('给 {agent} 发消息，继续这项工作。', { agent: escape(providers[s.provider] || s.provider) })}</p></div>`}${tabOf() === 'trace' ? '' : requests.map(requestCard).join('')}</div></div><div class="bottom-area">${allChildren.length ? `<section class="task-dock ${closed ? 'is-closed' : ''}"><button class="dock-heading" data-action="dock" aria-expanded="${!closed}"><span><strong>${t('子任务')}</strong><span class="dock-description">${finished === allChildren.length ? (halted ? t('{done}/{total} 已结束 · {stopped} 个已停止', { done: finished, total: allChildren.length, stopped: halted }) : `${icon('check')}${t('{done}/{total} 已返回', { done: finished, total: allChildren.length })}`) : t('派给 {count} 个 Agent 并行', { count: allChildren.length })}</span></span><span class="dock-counts">${finished === allChildren.length ? '' : summary(count(allChildren))}${live ? `<span class="live-label">${isDemo ? t('示例') : t('实时')}</span>` : ''}<span class="chevron dock-collapse">${icon('down')}</span></span></button>${!closed ? `<div class="dock-body">${children.map((n,i) => workerRow(n,0,String(i+1))).join('')}</div>` : ''}</section>` : ''}${composer(s)}${statsLine(s.external ? null : s)}</div>`;
}

function workerRow(node, depth, number) {
  const open = expanded.has(node.id), stream = streamCache.get(node.latest.id) || node.latest.stream;
  const state = workerStatus(node, stream);
  const requests = node.requests.filter(r=>r.pending);
  const mail = Object.values(data.messages).filter(m=>m.message.from === node.id || m.message.to === node.id).map(m=>({...m,fromName:providers[view.nodes.get(m.message.from)?.provider],toName:providers[view.nodes.get(m.message.to)?.provider]}));
  return `<div class="worker depth-${Math.min(depth, 3)} ${open ? 'is-expanded' : ''}"><button class="worker-row" data-worker="${escape(node.id)}" aria-expanded="${open}"><span class="worker-index">${escape(number)}</span><span class="worker-name" title="${escape(node.name)}">${escape(node.name)}${node.children.length ? `<small>+${node.children.length}</small>` : ''}</span><span class="worker-provider">${logo(node.provider)}<span>${escape(providers[node.provider] || node.provider)}</span></span><span class="worker-state">${badge(node.state,state.label,state.kind)}${state.activity ? `<span class="worker-activity" title="${escape(state.activity)}">· ${escape(state.activity)}</span>` : ''}</span><span class="worker-duration">${escape(elapsed(node.latest,isDemo ? Date.parse(data.observed_at || '2026-11-01T09:16:00Z') : Date.now()))}</span><span class="worker-chevron">${icon('chevron')}</span></button>${open ? `<div class="worker-expanded"><div class="worker-expanded-heading"><span>${t('只显示关键内容 · 工具调用已折叠')}</span><div class="worker-actions"><button class="small-button" data-chat-worker="${escape(node.id)}">${t('发消息')}</button>${node.latest.state === 'running' ? `<button class="small-button stop-text" data-stop="${escape(node.id)}" ${isStopping(node) ? 'disabled' : ''}>${isStopping(node) ? t('正在停止…') : t('停止')}</button>` : ''}${requests.length ? `<button class="small-button" data-focus-request="request-${escape(requests[0].call_id)}-${escape(requests[0].id)}">${t('查看待决事项')}</button>` : ''}<button class="text-button" data-detail="${escape(node.id)}">${t('全屏查看')}</button></div></div>${workerPreview(node,stream,mail)}</div>` : ''}</div>${node.children.map((child,i)=>workerRow(child,depth+1,number+'.'+(i+1))).join('')}`;
}

function sessionDock(sessions, waiting) {
  const active = sessions.filter(s => !s.done), done = sessions.filter(s => s.done);
  const time = value => value ? new Date(value).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) : '—';
  const row = s => { const workers = s.external ? s.members.length : s.members.length - 1; return `<button class="session-row" data-session="${escape(s.id)}"><span class="session-row-name">${escape(s.name)}</span><span class="session-row-owner">${logo(s.provider)}<span>${escape(providers[s.provider] || s.provider)}</span></span><span class="session-progress">${workers > 0 ? `<span class="muted-count">${t('{count} 个子任务', { count: workers })}</span>` : ''}${sessionSummary(s)}</span><span class="session-row-time">${escape(time(s.updated))}</span><span class="worker-chevron">${icon('chevron')}</span></button>`; };
  if (!sessions.length) return `<section class="task-dock assistant-session-dock"><div class="dock-heading static"><span><strong>${t('会话')}</strong><span class="dock-description">${t('私人助理创建的主会话会出现在这里')}</span></span></div></section>`;
  return `<section class="task-dock assistant-session-dock"><button class="dock-heading" data-action="assistant-dock" aria-expanded="${!assistantDockClosed}"><span><strong>${t('会话')}</strong><span class="dock-description">${t('{count} 个进行中', { count: active.length })}</span></span><span class="dock-counts">${waiting ? `<span class="amber">${t('{count} 件等你', { count: waiting })}</span>` : ''}<span class="live-label">${isDemo ? t('示例') : t('实时')}</span><span class="chevron dock-collapse">${icon('down')}</span></span></button>${!assistantDockClosed ? `<div class="dock-body"><div class="session-row session-row-head" aria-hidden="true"><span>${t('会话')}</span><span>${t('主控')}</span><span>${t('进展')}</span><span>${t('更新')}</span><span></span></div>${active.map(row).join('')}${done.length ? `<details class="done-sessions" data-key="done-sessions"><summary>${icon('chevron')}${t('已完成的会话 · {count}', { count: done.length })}</summary>${done.map(row).join('')}</details>` : ''}</div>` : ''}</section>`;
}
function overviewPage() {
  const requests = [...view.nodes.values()].flatMap(n => n.requests).filter(r => r.needsUser);
  const active = view.assistant?.latest.state === 'running';
  const busy = active || assistantSubmitting || assistantSettings.busy;
  const ready = isDemo || (status === 'ready' && assistantSettings.ready && !assistantSettings.error);
  const sessions = view.sessions;
  return `<div class="conversation-scroll assistant-conversation"><div class="reading-column">${tabOf() === 'trace' ? traceView([view.assistant]) : view.assistant ? conversation(view.assistant) : `<div class="assistant-welcome"><span class="assistant-welcome-mark">${brand(48)}</span><h2>${t('有事情，交给我。')}</h2><p>${t('我会把新工作交给一个主 Agent；主 Agent 再组织子任务。<br>已有会话的进展和需要你决定的事项，都在这里。')}</p><div class="welcome-examples"><button data-example="${escape(t('看看当前会话的进展，有什么需要我决定的？'))}">${t('看看各会话的进展 ↗')}</button><button data-example="${escape(t('帮我规划一个新任务，先问清目标和工作目录。'))}">${t('开始一项新工作 ↗')}</button></div></div>`}${requests.length ? `<div class="assistant-notice">${brand(24, 'assistant-icon')}<div><strong>${t('需要你决定')}</strong><p>${t('下面 {count} 件事正在等你，处理后会在对应会话中同步。', { count: requests.length })}</p></div></div>${requests.map(requestCard).join('')}` : ''}${assistantSettings.error || assistantError ? `<div class="error-text" role="alert">${escape(t(assistantError || assistantSettings.error))}</div>` : ''}</div></div><div class="bottom-area assistant-bottom">${sessionDock(sessions, requests.length)}<form id="assistant-form" class="assistant-composer composer-card"><textarea id="assistant-input" aria-label="${t('对私人助理说')}" placeholder="${busy ? t('助理正在处理本轮，完成后可以继续聊…') : t('对私人助理说… 它会交给合适的会话，或新建一个')}" rows="2">${escape(assistantDraft)}</textarea><div class="composer-row">${!ready ? `<span class="composer-status">${t('正在准备私人助理…')}</span>` : !isDemo && !assistantSettings.workspace ? `<button type="button" class="meta-chip" data-action="settings">${t('先在设置中选择工作目录')}</button>` : ''}<span class="row-spacer"></span><button type="submit" class="send-button" aria-label="${t('发送给私人助理')}" ${!ready || busy ? 'disabled' : ''}>${icon('arrow-up')}</button></div></form>${statsLine(view.assistant)}</div>`;
}

/** 新会话: a main agent started directly by the user, with the native provider/model/effort. */
function newSessionPage() {
  const provider = draftValue('provider') || 'claude-code', model = draftValue('model'), effort = draftValue('effort'), workspace = draftValue('workspace');
  const name = providers[provider] || provider, chip = modelChip(provider, model, effort);
  const ready = isDemo || (status === 'ready' && assistantSettings.ready && !assistantSettings.error);
  const busy = sessionSubmitting || !!pendingSession;
  const note = sessionSubmitting ? t('正在启动 {agent}…', { agent: name }) : pendingSession ? t('已启动，正在打开会话…') : !ready ? t('正在连接本地记录…') : '';
  return `<div class="conversation-scroll new-session-scroll"><div class="home">
    <div class="home-title"><span class="home-mark">${brand(34)}</span><h2>${t('开始一项新工作')}</h2></div>
    <form id="session-form" class="home-form" aria-busy="${busy}">
      <div class="home-context">
        <div class="menu-anchor"><button type="button" class="context-chip ${workspace ? '' : 'is-empty'}" data-menu="workspace" aria-expanded="${openMenu === 'workspace'}" title="${escape(workspace ? t('工作目录 · {path}', { path: displayPath(workspace) }) : t('选择工作目录'))}">${icon('folder')}<span>${escape(workspace ? baseName(workspace) : t('选择工作目录'))}</span>${icon('down')}</button>${openMenu === 'workspace' ? workspaceMenu(workspace) : ''}</div>
        <div class="menu-anchor"><button type="button" class="context-chip" id="session-agent" data-menu="agent" aria-expanded="${openMenu === 'agent'}" title="${t('主 Agent')}">${logo(provider, '')}<span>${escape(name)}</span>${icon('down')}</button>${openMenu === 'agent' ? agentMenu(provider) : ''}</div>
      </div>
      <div class="composer-card">
        <textarea id="session-input" aria-label="${t('新会话的任务')}" placeholder="${t('描述要交给 {agent} 的工作', { agent: escape(name) })}" rows="3">${escape(sessionDraft.prompt || '')}</textarea>
        ${sessionError ? `<p class="chat-error" role="alert">${escape(t(sessionError))}</p>` : ''}
        <div class="composer-row">${swarmPill(provider)}${permissionChip(provider, draftPermission(provider), 'permission', 'menu-down-left', t('这个会话里 {agent} 的权限 · 默认值在设置里改', { agent: name }))}${note ? `<span class="composer-status">${escape(note)}</span>` : ''}<span class="row-spacer"></span><div class="menu-anchor"><button type="button" class="model-chip" id="session-model-chip" data-menu="model" aria-expanded="${openMenu === 'model'}" title="${t('模型与思考强度')}">${escape(chip.name)}${chip.level ? `<span class="model-level">${escape(chip.level)}</span>` : ''}${icon('down')}</button>${openMenu === 'model' ? modelMenu(provider, model, effort, 'menu-down') : ''}</div><button type="submit" class="send-button" aria-label="${t('开始新会话')}" ${!ready || busy ? 'disabled' : ''}>${icon('arrow-up')}</button></div>
      </div>
    </form>
  </div></div>`;
}
function confirmStop(target) {
  const node = view.nodes.get(target), session = view.sessions.find(s => s.target === target);
  const list = runningIn(target), self = node?.latest.state === 'running', others = list.filter(n => n !== node).length;
  const name = node ? `${providers[node.provider] || node.provider} · ${node.name}` : session?.name || t('这个会话');
  const scope = self && others ? t('会结束它当前这一轮，以及它名下 {count} 个仍在运行的子任务。', { count: others }) : self ? t('会结束它当前这一轮。') : t('会结束它名下 {count} 个仍在运行的子任务。', { count: others });
  detailId = null;
  document.querySelector('#detail-content').innerHTML = `<div class="dialog-heading"><h2>${t('停止 {name}？', { name: escape(name) })}</h2><button data-close="detail" class="icon-button" aria-label="${t('关闭')}">×</button></div><div class="confirm-copy"><p>${escape(scope)}</p><p>${t('已经返回的结果和文件改动会保留。之后给它发消息，会在原会话里接着做。')}</p></div><div class="dialog-actions"><button class="secondary" data-close="detail">${t('继续运行')}</button><button class="danger" data-confirm-stop="${escape(target)}">${icon('stop')}${t('停止')}</button></div>`;
  if (!detail.open) detail.showModal();
  detail.querySelector('[data-close="detail"].secondary')?.focus();
}
function demoStop(target) {
  const list = runningIn(target);
  for (const node of list) {
    const call = data.calls[node.latest.id];
    call.state = 'cancelled';
    call.result = { call_id: call.id, agent_id: node.id, provider: call.task.provider, outcome: 'cancelled', content: '', error: null, artifacts: [], finished_at: data.observed_at };
    for (const r of Object.values(data.requests)) if (r.call_id === call.id) r.pending = false;
  }
  view = project(data); render();
  showToast(list.length ? t('示例中已停止 {count} 个运行中的调用', { count: list.length }) : t('它已经结束了'));
}

function permissionSection() {
  // Every mode is a pill in the row (no pop-up menu), so the dialog never clips a choice.
  const row = id => {
    const mode = permissionDefault(id), found = PERMISSION_MODES[id].find(([m]) => m === mode) || PERMISSION_MODES[id][0];
    const name = providers[id] || id, missing = available && available[id]?.available !== true;
    return `<div class="permission-row"><div class="permission-agent">${logo(id, '')}<span>${escape(name)}${missing ? `<small>${t('本机未检测到')}</small>` : ''}</span></div><div class="permission-options" role="radiogroup" aria-label="${escape(t('{agent} 在新会话里的默认权限', { agent: name }))}">${PERMISSION_MODES[id].map(([m, label, note]) => `<button type="button" role="radio" class="permission-option" aria-checked="${m === found[0]}" data-default-permission="${m}" data-provider="${id}" title="${escape(t(note))}">${escape(label)}</button>`).join('')}</div><p class="permission-note">${escape(t(found[2]))}</p></div>`;
  };
  return `<div class="settings-section permission-settings"><label>${t('Agent 权限')}</label><p>${t('每个 Agent 在新会话里默认用的权限，名字和它自己的一样。只在 Desk 里生效，不改 Agent 自己的设置；单个会话可以另选。子 Agent 用它自己的默认，但不会超过派它的 Agent；它的审批先由派它的 Agent 决定，拿不准时再问你。')}</p>${Object.keys(PERMISSION_MODES).map(row).join('')}</div>`;
}
async function pickPermission(menu, provider, mode) {
  openMenu = null;
  if (menu === 'permission') { sessionDraft = { ...sessionDraft, permission: mode }; render(true); return; }
  if (menu === 'session-permission') {
    const s = current();
    if (!s) return;
    if (isDemo) data.agents[s.id] = { ...data.agents[s.id], permission: mode };
    else await desk.permission(s.id, mode);
    render(true); showToast(t('从下一条消息开始，这个会话按新权限运行'));
    return;
  }
  // Desk's own defaults are app settings, kept in the demo too; the browser preview has no main process.
  if (desk.permissions) await desk.permissions(provider, mode);
  permissionPrefs = { ...permissionPrefs, [provider]: mode };
  renderSettings();
}
function shellSection() {
  const row = (key, title, note = '') => `<label class="check-row"><input type="checkbox" data-pref="${key}" ${shellPrefs[key] ? 'checked' : ''} ${isDemo ? 'disabled' : ''}><span>${t(title)}${note ? `<small>${t(note)}</small>` : ''}</span></label>`;
  return `<div class="settings-section shell-settings"><label>${t(mac ? '菜单栏与通知' : '托盘与通知')}</label>${row('notify', '需要我决定时弹出系统通知', '审批或提问出现时提醒；点击通知直达对应卡片。主窗口在前台时只更新角标。')}${row('notifyDone', '会话完成或失败时通知')}${mac ? row('closeToTray', '关闭窗口后留在菜单栏', '继续接收提醒；右键菜单栏图标可以退出。') : row('closeToTray', '关闭窗口后留在托盘', '继续接收提醒；右键托盘图标可以退出。')}${row('openAtLogin', mac ? '开机时在菜单栏中启动' : '开机时在托盘中启动')}${row('wakeAssistant', '私人助理交出的会话全部返回时唤醒它', '助理会收取结果并告诉你结论。每次唤醒都会调用一次模型，默认关闭。')}</div>`;
}
const RULE_ACTIONS = { notify: '通知', silence: '静音内置通知', assistant: '交给私人助理', message: '发给会话' };
const RULE_STATUS = { shown: '已通知', watching: '窗口在前台，未弹出', silenced: '已静音', queued: '排队中', sent: '已发送', failed: '失败', limit: '已达次数上限，跳过', 'no-session': '没有可发送的会话' };
function rulesSection() {
  const r = ruleInfo || {};
  const head = !r.exists ? t('还没有规则文件') : t('{count} 条生效', { count: r.active || 0 }) + (r.disabled ? ` · ${t('{count} 条停用', { count: r.disabled })}` : '');
  const errors = r.errors?.length ? `<ul class="rule-errors" role="alert">${r.errors.map(e => `<li>${escape(t(e))}</li>`).join('')}</ul>` : '';
  const log = r.log?.length ? `<div class="rule-log"><strong>${t('最近触发')}</strong><ul>${r.log.map(e => `<li><time>${escape(stamp(e.at))}</time><span>${escape(e.rule)} · ${RULE_ACTIONS[e.do] ? t(RULE_ACTIONS[e.do]) : escape(e.do)}${e.session ? ` · ${escape(e.session)}` : ''}</span><em class="${e.status === 'failed' ? 'is-failed' : ''}">${escape(t(RULE_STATUS[e.status] || e.status))}${e.error ? `${t('：')}${escape(t(e.error))}` : ''}</em></li>`).join('')}</ul></div>` : '';
  return `<div class="settings-section rules-settings"><label>${t('触发规则 · {status}', { status: head })}</label><p>${t('每条规则写「什么时候」和「做什么」：有事等你决定、会话完成或失败时，弹你自己写的通知、不弹通知、交给私人助理处理，或给这个会话的主 Agent 发一句话。例如：')}</p><ul class="rule-examples"><li>${t('Codex 等你批准时，通知里写清是哪个会话、要做什么')}</li><li>${t('外部会话完成时不弹通知')}</li><li>${t('会话失败时，请私人助理查明原因再告诉你')}</li></ul><p>${t('点下面的按钮会生成一份带说明和这几条示例的规则文件（JSON，示例默认不启用），把想要的那条的 enabled 改成 true，保存后立即生效。交给助理和发消息会调用模型，同一条规则对同一个会话默认只触发一次。')}</p>${errors}<code class="home-path">${escape(r.path || 'triggers.json')}</code><button class="secondary" data-action="open-rules" ${isDemo ? 'disabled' : ''}>${r.exists ? t('打开规则文件') : t('创建规则文件')}</button>${log}</div>`;
}
function eventsSection() {
  const mode = t(data.capture === 'auto' ? '其他 Agent 由 Desk 保存' : data.capture ? '全部由 Desk 另存一份' : '未开启保存');
  return `<div class="settings-section events-settings"><label>${t('过程记录 · {mode}', { mode })}</label><p>${t('Claude Code、Codex、Cursor 的工具调用、命令输出和思考过程，直接读取它们自己的会话记录，不另存；只读这次调用对应的那个会话。其他 Agent 没有可读的本地记录，由 Desk 在运行时保存一份，单个调用超过 64 MB 后不再继续保存。Desk 不会删除任何记录。')}</p><label class="check-row"><input type="checkbox" data-pref="captureAll" ${shellPrefs.captureAll ? 'checked' : ''} ${isDemo || !assistantSettings.ready ? 'disabled' : ''}><span>${t('也为 Claude Code、Codex、Cursor 另存一份')}<small>${t('各家可能按自己的规则清理旧会话；另存后，即使原记录被删也还能看到过程。')}</small></span></label></div>`;
}
/** Rule status and the capture mode change while settings is open; refresh them unless the user is in them. */
const liveSections = new Map();
function refreshLiveSettings() {
  for (const [selector, build] of [['.appearance', appearanceSection], ['.rules-settings', rulesSection], ['.events-settings', eventsSection]]) {
    const node = settings.querySelector(selector), html = build(), active = document.activeElement;
    // A focused button or checkbox may be replaced; a select or text field the user is in may not.
    if (!node || (node.contains(active) && active.matches('select, textarea, input:not([type=checkbox]):not([type=radio])')) || liveSections.get(selector) === html) continue;
    liveSections.set(selector, html);
    node.outerHTML = html;
  }
}
function assistantPicker() {
  assistantPick ||= { provider: assistantSettings.provider || 'claude-code', model: assistantSettings.model || '', effort: assistantSettings.effort || '' };
  const { provider, model, effort } = assistantPick, chip = modelChip(provider, model, effort);
  return `<div class="picker-row"><div class="menu-anchor"><button type="button" class="context-chip" data-menu="a-agent" id="assistant-agent" title="${t('私人助理用哪个 Agent')}" aria-expanded="${openMenu === 'a-agent'}">${logo(provider, '')}<span>${escape(providers[provider] || provider)}</span>${icon('down')}</button>${openMenu === 'a-agent' ? agentMenu(provider, 'assistant') : ''}</div><div class="menu-anchor"><button type="button" class="model-chip" data-menu="a-model" id="assistant-model-chip" aria-expanded="${openMenu === 'a-model'}" title="${t('模型与思考强度')}">${escape(chip.name)}${chip.level ? `<span class="model-level">${escape(chip.level)}</span>` : ''}${icon('down')}</button>${openMenu === 'a-model' ? modelMenu(provider, model, effort, 'menu-down-left', 'assistant') : ''}</div></div><input type="hidden" name="provider" value="${escape(provider)}"><input type="hidden" name="model" value="${escape(model)}"><input type="hidden" name="effort" value="${escape(effort)}">`;
}
/** The assistant's conversations, one per agent: switching agent continues that agent's own. */
function assistantConversations() {
  const list = view.assistants || [];
  if (!list.length) return '';
  const title = node => data.agents[node.id]?.title || taskTitle(node.calls[0]?.task.prompt, t('私人助理'));
  return `<div class="assistant-list" role="list" aria-label="${t('助理会话')}">${list.map(node => {
    const on = node === view.assistant;
    return `<button type="button" role="listitem" class="assistant-row ${on ? 'is-current' : ''}" data-assistant-switch="${escape(node.provider)}" ${on ? 'aria-current="true"' : ''} title="${escape(on ? t('当前的助理会话') : t('切换到 {agent} 的助理会话，接着聊', { agent: providers[node.provider] || node.provider }))}">${logo(node.provider, '')}<span class="assistant-row-text"><strong>${escape(title(node))}</strong><small>${escape(providers[node.provider] || node.provider)} · ${escape(relTime(node.latest?.task.created_at))}</small></span>${on ? `<span class="menu-tag">${t('当前')}</span>` : ''}</button>`;
  }).join('')}</div>`;
}
/** Switch the assistant to `provider`: its own conversation continues, or a new one starts. */
async function switchAssistant(provider) {
  assistantPick = { provider, model: '', effort: '' };
  try {
    await desk.assistantSettings({ provider, model: '', effort: '' });
    const known = (view.assistants || []).some(node => node.provider === provider);
    showToast(known ? t('已切换到 {agent} 的助理会话', { agent: providers[provider] || provider }) : t('下一条消息会开始 {agent} 的助理会话，它能看到之前的会话', { agent: providers[provider] || provider }));
  } catch (error) { showToast(error.message); }
  renderSettings();
}
function renderSettings() {
  liveSections.clear();
  if (browserPreview) {
    const executable = decodeURIComponent(new URL('../dist/win-unpacked/AutoSwarm Desk.exe', location.href).pathname).replace(/^\/([a-z]:)/i, '$1').replaceAll('/', '\\');
    document.querySelector('#settings-content').innerHTML = `${appearanceSection()}<div class="settings-section"><label>${t('当前是浏览器预览')}</label><p>${t('你可以查看示例会话、Agent 层级和最终回复。浏览器无法启动本地监听进程；要连接真实记录，请打开 Windows 桌面程序。')}</p><label>${t('在文件资源管理器中双击运行')}</label><code class="home-path">${escape(executable)}</code><p>${t('进入桌面版后，在「连接与设置」中选择 AUTOSWARM_HOME 记录目录。程序自带 Python，无需另外安装。')}</p></div><div class="settings-section"><label>${t('继续查看界面')}</label><p>${t('演示内容独立于真实 Agent，不会执行任务或修改记录。')}</p><button class="secondary" data-close="settings">${t('返回预览')}</button></div>`;
    return;
  }
  document.querySelector('#settings-content').innerHTML = `${appearanceSection()}${permissionSection()}${shellSection()}${rulesSection()}<form id="assistant-settings-form" class="settings-section"><label>${t('私人助理 · Meta Agent')}</label>${assistantPicker()}<p>${t('可以随时换 Agent。每个 Agent 各有一个助理会话，换回来接着聊；换到新的 Agent 时，它会拿到之前各会话的标题和会话 ID，需要时读取它们最近的内容，不会忘掉之前说过的事。模型和思考强度来自 Agent 自己报告的列表。')}</p>${assistantConversations()}<code class="home-path">${escape(assistantSettings.workspace || t('未选择工作目录'))}</code><p>${view.assistant ? t('这个 Agent 的助理会话保留它的工作目录；还没有会话的 Agent 可以选新的目录。') : t('选择私人助理的工作目录后即可开始对话。')}</p><button type="button" class="secondary" data-action="choose-workspace" ${view.assistant || isDemo || !assistantSettings.ready ? 'disabled' : ''}>${t('选择工作目录')}</button><button type="submit" class="primary" ${isDemo || !assistantSettings.ready ? 'disabled' : ''}>${t('保存助理设置')}</button></form><div class="settings-section"><label>${t('记录目录')}</label><code class="home-path">${escape(isDemo ? t(data.home) : data.home || t('默认 AUTOSWARM_HOME'))}</code><p>${t('读取 AutoSwarm 已有的会话、审批、消息和结果。设置保存在本机，重启后会重新扫描。')}</p><button class="primary" data-action="choose-home">${t('选择记录目录')}</button><button class="secondary" data-action="reconnect">${t('重新连接')}</button></div>${eventsSection()}<div class="settings-section"><label>${t('Python 监听进程')}</label><code class="home-path">${escape(t(python))}</code><p>${t('安装包自带 Python，不用另外安装；也可以改用本机的 Python 3.10+。监听进程随窗口关闭而退出。')}</p><button class="secondary" data-action="choose-python">${t('选择 Python 解释器')}</button></div><div class="settings-section"><label>${t('演示空间')}</label><p>${isDemo ? t('正在演示：示例数据，不读取或修改真实任务。') : t('使用独立示例查看协作布局，不读取或修改真实任务。')}</p>${isDemo ? `<button class="primary" data-action="exit-demo">${t('退出演示，回到我的记录')}</button>` : `<button class="secondary" data-action="demo">${t('打开演示')}</button>`}</div>`;
}

function desktopOnly() {
  renderSettings();
  if (!settings.open) settings.showModal();
}

function renderDetail(id) {
  const node = view.nodes.get(id);
  if (!node) { detail.close(); return; }
  document.querySelector('#detail-content').innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">AGENT DETAILS</span><h2>${escape(node.name)}</h2></div><button data-close="detail" class="icon-button" aria-label="${t('关闭详情')}">×</button></div><div class="detail-body">${conversation(node)}${messagesFor([node])}</div>`;
}

let railWidth = window.innerWidth <= 860;
window.addEventListener('resize', () => { const now = window.innerWidth <= 860; if (now !== railWidth) { railWidth = now; render(true); } });
preview = createPreview({ desk, markdown, showToast, onLayout: () => render(true) });
/** Relative links resolve in that turn's workspace; inside the panel, next to the file shown there. */
function linkBase(element) {
  if (element.closest('#preview')) return preview.base();
  const turn = element.closest('[id^="turn-call_"]'), call = turn && data.calls[turn.id.slice(5)];
  if (call?.task.workspace) return call.task.workspace;
  return overview ? view.assistant?.latest.task.workspace || assistantSettings.workspace || '' : workspaceOf(current() || {});
}
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  // Any click outside an open menu closes it.
  if (openMenu && !event.target.closest('.menu-anchor')) { const inSettings = openMenu.startsWith('a-'); openMenu = null; inSettings ? renderSettings() : render(true); if (!button) return; }
  // Links in replies open in the side panel instead of leaving Desk.
  const link = event.target.closest('a[href], code[data-path]');
  if (link) { event.preventDefault(); await preview.open(link.getAttribute('href') ?? link.dataset.path, linkBase(link), { here: !!link.closest('#preview') }); return; }
  if (!button) return;
  try {
    if (button.dataset.tab) { tabs.set(tabKey(), button.dataset.tab); render(); document.querySelector('.conversation-scroll')?.scrollTo(0, 0); return; }
    if (button.dataset.trace) {
      tabs.set(tabKey(), 'trace'); render();
      requestAnimationFrame(() => document.getElementById('trace-' + button.dataset.trace)?.scrollIntoView({ block: 'start' }));
      return;
    }
    if (button.dataset.quote) {
      const text = copies.get(button.dataset.quote), input = document.querySelector('#chat-input, #assistant-input');
      if (!text || !input) return;
      const quoted = String(text).trim().split(/\r?\n/).slice(0, 12).map(line => '> ' + line).join('\n') + '\n\n';
      input.value = quoted + input.value; input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus(); input.setSelectionRange(input.value.length, input.value.length); return;
    }
    if (button.dataset.swarm) {
      const provider = button.dataset.swarm;
      if (skills?.installing === provider) return;
      if (!skillReady(provider)) { if (wantSkill(provider)) { render(true); return; } openMenu = openMenu === 'swarm' ? null : 'swarm'; render(true); if (openMenu && !isDemo) desk.skills().catch(() => {}); return; }
      if (SKILL_SWITCHABLE.has(provider)) { sessionDraft = { ...sessionDraft, skill: !draftSkill(provider) }; render(true); }
      return;
    }
    if (button.dataset.action === 'skill-agent') { await agentInstall(button.dataset.host); return; }
    if (button.dataset.pickPermission !== undefined) { await pickPermission(button.dataset.menuId, button.dataset.provider, button.dataset.pickPermission); return; }
    if (button.dataset.defaultPermission) { await pickPermission('default', button.dataset.provider, button.dataset.defaultPermission); return; }
    if (button.dataset.language) { await chooseLanguage(button.dataset.language); return; }
    if (button.dataset.zoom !== undefined) { zoomFactor = await desk.zoom(Number(button.dataset.zoom)); renderSettings(); return; }
    if (button.dataset.refreshModels) { await refreshCatalog(button.dataset.refreshModels, button.dataset.ctx); return; }
    if (button.dataset.menu) { openMenu = openMenu === button.dataset.menu ? null : button.dataset.menu; modelFilter = '';
      if (openMenu === 'model' || openMenu === 'a-model') askCatalog(openMenu === 'model' ? draftValue('provider') || 'claude-code' : assistantPick?.provider || assistantSettings.provider || 'claude-code'); button.dataset.menu.startsWith('a-') ? renderSettings() : render(true); return; }
    if (button.dataset.pickAgent !== undefined || button.dataset.pickModel !== undefined || button.dataset.pickEffort !== undefined || button.dataset.pickWorkspace !== undefined) {
      const ctx = button.dataset.ctx === 'assistant' ? 'assistant' : 'session';
      const get = key => ctx === 'assistant' ? assistantPick?.[key] || '' : draftValue(key);
      const set = patch => { if (ctx === 'assistant') assistantPick = { ...assistantPick, ...patch }; else sessionDraft = { ...sessionDraft, ...patch }; };
      const provider = get('provider') || 'claude-code';
      let close = true;
      if (button.dataset.pickAgent !== undefined && ctx === 'assistant' && !isDemo) { openMenu = null; catalogFor(button.dataset.pickAgent); await switchAssistant(button.dataset.pickAgent); return; }
      if (button.dataset.pickAgent !== undefined) { set({ provider: button.dataset.pickAgent, model: '', effort: '', ...(ctx === 'session' ? { skill: undefined, permission: undefined } : {}) }); catalogFor(button.dataset.pickAgent); if (ctx === 'session') wantSkill(button.dataset.pickAgent); }
      else if (button.dataset.pickWorkspace !== undefined) { set({ workspace: button.dataset.pickWorkspace }); sessionError = ''; }
      else if (button.dataset.pickModel !== undefined) {
        const found = (catalogFor(provider).value?.models || []).find(m => m.id === button.dataset.pickModel), effort = get('effort');
        set({ model: found?.variants ? found.variants[found.default_effort] || found.id : button.dataset.pickModel,
          effort: found?.variants ? '' : found && found.efforts?.length && !found.efforts.includes(effort) ? found.default_effort || '' : effort });
        close = false;  // Stay open for the effort row.
      } else {
        const found = chosenModel(provider, get('model')), level = button.dataset.pickEffort;
        if (found?.variants) set({ model: found.variants[level] || found.id }); else set({ effort: level });
      }
      if (close) openMenu = null;
      ctx === 'assistant' ? renderSettings() : render(true);
      return;
    }
    if (button.dataset.assistantSwitch) { if (!isDemo && button.getAttribute('aria-current') !== 'true') await switchAssistant(button.dataset.assistantSwitch); return; }
    if (button.dataset.workspaceToggle !== undefined) { const w = button.dataset.workspaceToggle, now = button.getAttribute('aria-expanded') === 'true'; workspaceOpen.set(w, !now); render(true); return; }
    if (button.dataset.example) { assistantDraft = button.dataset.example; render(); document.querySelector('#assistant-input')?.focus(); return; }
    if (button.dataset.decision) {
      button.disabled = true;
      if (isDemo) { const r = data.requests[button.dataset.call + '/' + button.dataset.rid]; r.pending = false; r.decision = {actor:'user',decision:button.dataset.decision}; view = project(data); render(); }
      else { try { await desk.decide(button.dataset.call, button.dataset.rid, button.dataset.decision, button.dataset.scope || 'once'); } finally { button.disabled = false; } }
      return;
    }
    if (button.dataset.stop) { confirmStop(button.dataset.stop); return; }
    if (button.dataset.confirmStop) {
      const target = button.dataset.confirmStop; detail.close();
      if (isDemo) { demoStop(target); return; }
      stopping.add(target); render();
      try {
        const result = await desk.cancel(target);
        if (!result.cancel_requested?.length) { stopping.delete(target); showToast(t('它已经结束了')); }
        else showToast(result.cancel_requested.length > 1 ? t('已请求停止 {count} 个运行中的调用', { count: result.cancel_requested.length }) : t('已请求停止，正在等待 Agent 结束这一轮'));
      } catch (failure) { stopping.delete(target); showToast(failure.message); }
      render(); return;
    }
    if (button.dataset.copy) { const text = copies.get(button.dataset.copy); if (text === undefined) return; await copyText(text); button.classList.add('copied'); button.innerHTML = icon('check'); showToast(t('已复制')); return; }
    if (button.dataset.themeChoice) { await chooseTheme(button.dataset.themeChoice); return; }
    if (button.dataset.focusRequest) { document.getElementById(button.dataset.focusRequest)?.scrollIntoView({behavior:'smooth',block:'center'}); return; }
    if (button.dataset.window) return desk.window(button.dataset.window);
    if (button.dataset.close) return document.getElementById(button.dataset.close).close();
    if (button.dataset.session) { selected = button.dataset.session; overview = false; composing = false; workerId = null; expanded.clear(); render(); document.querySelector('.conversation-scroll').scrollTop = 0; return; }
    if (button.dataset.worker) { const id = button.dataset.worker; expanded.has(id) ? expanded.delete(id) : expanded.add(id); render(); return; }
    if (button.dataset.detail) { workerId = button.dataset.detail; render(); document.querySelector('.conversation-scroll').scrollTop=0; return; }
    if (button.dataset.chatWorker) { workerId=button.dataset.chatWorker; render(); document.querySelector('#chat-input')?.focus(); return; }
    if (button.dataset.request) {
      const request = data.requests[button.dataset.request];
      detailId = null;
      document.querySelector('#detail-content').innerHTML = `<div class="dialog-heading"><h2>${t('原始请求')}</h2><button data-close="detail" class="icon-button" aria-label="${t('关闭请求')}">×</button></div><p class="muted">${t('请回到原会话，或使用 AutoSwarm CLI 处理。')}</p><pre>${json(request.request)}</pre><p class="muted">Call: ${escape(request.call_id)}<br>Request: ${escape(request.id)}</p>`;
      if (!detail.open) detail.showModal(); return;
    }
    if (button.dataset.reveal) return await desk.reveal(button.dataset.reveal);
    switch (button.dataset.action) {
      case 'focus-chat': document.querySelector('#chat-input')?.focus(); break;
      case 'assignment': { const card=document.querySelector('details.assignment-card'); if(card)card.open=true; document.querySelector('#assignment')?.scrollIntoView({block:'start',behavior:'smooth'}); break; }
      case 'worker-back': workerId = null; render(); break;
      case 'overview': workerId = null; overview = true; composing = false; render(); break;
      case 'sidebar': collapsed = !collapsed; render(); break;
      case 'theme': await chooseTheme(isDark() ? 'light' : 'dark'); break;
      case 'preview': preview.toggle(); break;
      case 'search': searchOpen = !searchOpen || !!search; render(); document.querySelector('#search')?.focus(); break;
      case 'new-session': workerId = null; overview = false; composing = true; sessionError = ''; openMenu = null; wantSkill(draftValue('provider') || 'claude-code'); render(); document.querySelector('#session-input')?.focus(); break;
      case 'session-workspace': {
        if (isDemo || !desk.sessionWorkspace) { showToast(t('演示中不能选择目录；连接本地记录后即可新建会话。')); break; }
        const chosen = await desk.sessionWorkspace();
        if (chosen) { sessionDraft.workspace = chosen; sessionError = ''; }
        openMenu = null; render();
        break;
      }
      case 'dock': { const s = current(); if (s) dockState.set(s.id, !dockIsClosed(s)); render(); break; }
      case 'assistant-dock': assistantDockClosed = !assistantDockClosed; render(); break;
      case 'choose-workspace': await desk.chooseWorkspace(); break;
      case 'rules': detailId = null; document.querySelector('#detail-content').innerHTML = `<div class="dialog-heading"><h2>${t('私人助理的交接规则')}</h2><button data-close="detail" class="icon-button" aria-label="${t('关闭规则')}">×</button></div><div class="rules-copy"><p>${t('新工作交给一个主 Agent，它负责拆解和组织 Worker。')}</p><p>${t('已有工作的补充要求，转给对应主会话。私人助理每次收到你的消息时检查进展，不反复占用模型轮询。')}</p><p>${t('运行中或需要决定的事项在这里汇总。需要用户授权的请求，由你决定。')}</p></div>`; detail.showModal(); break;
      case 'settings': assistantPick = null; openMenu = null; renderSettings(); settings.showModal(); break;
      case 'open-rules': { const opened = await desk.openRules(); showToast(opened.created ? t('已创建规则文件，里面的示例默认停用') : t('已打开规则文件')); break; }
      case 'choose-home': await desk.chooseHome(); break;
      case 'choose-python': await desk.choosePython(); break;
      case 'reconnect': await desk.reconnect(); break;
      case 'exit-demo': settings.close(); selected = null; overview = true; composing = false; workerId = null; await desk.reconnect(); break;
      case 'demo': settings.close(); await desk.demo(); break;
      case 'update-open': if (update?.page) await desk.openExternal(update.page); break;
      case 'update-install': {
        const result = await desk.update('install');
        if (result?.busy) showToast(result.busy > 0 ? t('有 {count} 个 Agent 进程正在用 Desk 自带的 Python，现在安装会中断它们。等它们结束再更新，或之后退出 Desk 时自动安装。', { count: result.busy }) : t('无法确认是否有 Agent 正在运行，这次不安装。退出 Desk 时会再检查。'));
        break;
      }
    }
  } catch (error) { showToast(error.message); }
});
/** Filtering a long model list hides rows in place; a re-render applies the same filter. */
function filterModels(input) {
  modelFilter = input.value.trim().toLowerCase();
  const menu = input.closest('.model-menu');
  let shown = 0;
  for (const row of menu.querySelectorAll('[data-search]')) { row.hidden = !!modelFilter && !row.dataset.search.includes(modelFilter); shown += row.hidden ? 0 : 1; }
  menu.querySelector('[data-filter-empty]').hidden = shown > 0;
}
document.addEventListener('input', event => {
  if (event.target.matches?.('[data-model-filter]')) { filterModels(event.target); return; }
  if (event.target.id === 'chat-input') { chatDrafts.set(draftKey(chatTarget()),event.target.value); return; }
  if (event.target.id === 'assistant-input') { assistantDraft = event.target.value; assistantDrafts.set(data.home,assistantDraft); return; }
  if (event.target.id === 'session-input') { sessionDraft.prompt = event.target.value; return; }
  if (['session-model','session-effort'].includes(event.target.id)) { sessionDraft[event.target.dataset.field] = event.target.value; return; }
  if (['assistant-model','assistant-effort'].includes(event.target.id)) { assistantPick = { ...assistantPick, [event.target.dataset.field]: event.target.value }; return; }
  if (event.target.id !== 'search') return;
  const caret = event.target.selectionStart; search = event.target.value; render();
  const input = document.querySelector('#search'); input.focus(); input.setSelectionRange(caret, caret);
});
for (const modal of [settings, detail]) modal.addEventListener('click', event => { if (event.target === modal) { const box = modal.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) modal.close(); } });
function openPending() {
  if (!pendingSession || !view.sessions.some(s => s.id === pendingSession)) return;
  selected = pendingSession; pendingSession = null; composing = false; overview = false; workerId = null; expanded.clear();
}
function receive(frame) {
  if (!frame) return;
  let changedHome = false;
  const priorAssistantSettings = assistantSettings;
  const wasDemo = isDemo;
  if (frame.event === 'connecting') { streamEpoch++; nativeCache.clear(); streamCache.clear(); loaded.clear(); reading.clear(); assistantSubmitting = false; assistantError = ''; }
  if (frame.theme && frame.theme !== themeChoice) { themeChoice = frame.theme; applyTheme(); }
  isDemo = frame.demo; python = frame.python; assistantSettings = frame.assistantSettings || {};
  sessionDefaults = frame.sessionDefaults || {}; available = frame.available || null; shellPrefs = frame.shell || {}; permissionPrefs = frame.permissions || {}; update = frame.update || null;
  if (frame.zoom) zoomFactor = frame.zoom;
  if (frame.language && frame.language !== language()) { setLanguage(frame.language); applyStaticText(); if (data) view = project(data); }
  skills = frame.skills || (frame.demo ? DEMO_SKILLS : null);
  ruleInfo = frame.rules || {};
  if (frame.event === 'snapshot') {
    const priorTarget = current()?.target;
    status = 'ready'; data = frame.data; view = project(data);
    if(priorTarget && view.sessions.some(s=>s.id===priorTarget))selected=priorTarget;
    if (lastHome !== data.home || wasDemo !== isDemo) { changedHome = true; composing = false; pendingSession = null; sessionDraft = { prompt: '' }; stopping.clear(); nativeCache.clear(); streamCache.clear(); loaded.clear(); limits.clear(); workerId = null; overview = true; assistantDraft = assistantDrafts.get(data.home) || ''; assistantError = ''; assistantSubmitting = false; }
    for (const key of questionDrafts.keys()) if (key.startsWith(data.home + ':') && !data.requests[key.slice(data.home.length+1)]?.pending) questionDrafts.delete(key);
    lastHome = data.home;
    for (const call of Object.values(data.calls)) if (call.stream?.events) {
      const old = streamCache.get(call.id), merged = combine(old, call.stream);
      if (limits.get(call.id)) { const count = stream => turns(stream?.events || []).reduce((n,t)=>n+t.items.length,0); limits.set(call.id, Math.max(0,limits.get(call.id)+count(merged)-count(old))); }
      streamCache.set(call.id, merged);
    }
    openPending();
    for (const target of [...stopping]) if (!runningIn(target).length) stopping.delete(target);
    if (!view.sessions.some(s => s.id === selected)) selected = view.sessions[0]?.id;
  } else { status = frame.event; error = frame.message || ''; }
  if (changedHome) { const pane=document.querySelector('.conversation-scroll'); if(pane)pane.scrollTop=0; }
  render(!changedHome);
  if (settings.open && (changedHome || priorAssistantSettings.workspace !== assistantSettings.workspace || priorAssistantSettings.ready !== assistantSettings.ready || priorAssistantSettings.binding?.controller_id !== assistantSettings.binding?.controller_id || priorAssistantSettings.binding?.assistant_agent_id !== assistantSettings.binding?.assistant_agent_id)) renderSettings();
}
function navigate(target = {}) {
  if (settings.open) settings.close();
  if (detail.open) detail.close();
  workerId = null;
  if (target.compose) { composing = true; overview = false; sessionError = ''; render(); document.querySelector('#session-input')?.focus(); return; }
  composing = false;
  const session = target.session && view.sessions.find(s => s.id === target.session);
  if (session) { selected = session.id; overview = false; } else overview = true;
  if (session && target.worker) { expanded.add(target.worker); dockState.set(session.id, false); }
  if (target.request) flash = { key: target.request, until: Date.now() + 2400 };
  render();
  const card = target.request && document.getElementById('request-' + target.request.replace('/', '-'));
  if (card) card.scrollIntoView({ block: 'center' });
  else if (target.worker) document.querySelector(`[data-worker="${CSS.escape(target.worker)}"]`)?.scrollIntoView({ block: 'nearest' });
  if (flash) setTimeout(() => { flash = null; render(); }, 2500);
}
async function start() {
  desk.onNavigate?.(navigate);
  desk.subscribe(receive);
  desk.onCatalog?.(({ provider, value }) => takeCatalog(provider, value));
  render();
  try { receive(await desk.initial()); }
  catch (failure) { status = 'error'; error = t('连接未能启动：{error}', { error: failure.message }); render(); }
}
start();

function workerSidebar() {
  const session = current();
  const members = (session?.members || []).filter(n=>session?.external || n.id !== session?.id);
  const mark = n => n.state==='running'?'<i class="state-dot"></i>':n.state==='waiting'?`<em>${t('等你')}</em>`:n.state==='delegated'?`<em class="muted-mark">${t('代批')}</em>`:n.state==='completed'?`<span class="done-mark">${icon('check')}</span>`:['failed','cancelled','interrupted'].includes(n.state)?`<em class="failed-mark">${escape(t(labels[n.state]))}</em>`:'';
  return `<aside class="worker-sidebar"><button class="back-conversation" data-action="worker-back">${icon('back')}<span class="sidebar-text">${t('返回对话')}</span></button><div class="worker-nav-title sidebar-text">${escape(session?.name || '')} · agents</div><nav aria-label="${t('本任务的 Agent')}">${members.map(n=>`<button class="worker-nav-item ${n.id === workerId ? 'active' : ''}" data-detail="${escape(n.id)}" title="${escape(n.name)}" ${n.id===workerId?'aria-current="page"':''}>${logo(n.provider)}<span class="sidebar-text"><strong>${escape(providers[n.provider] || n.provider)}</strong><small>${escape(n.name)}</small></span><span class="worker-nav-mark sidebar-text">${mark(n)}</span></button>`).join('')}</nav><div class="sidebar-bottom"><button class="settings-button" data-action="settings">${icon('settings')}<span class="sidebar-text">${t('设置')}</span></button></div></aside>`;
}
function workerHeader(node) {
  if (!node) return '';
  const owner = node.parent || current(), state = workerStatus(node,streamCache.get(node.latest.id) || node.latest.stream);
  const first = node.calls[0]?.task || {}, pending = node.requests.filter(r => r.pending);
  const dispatched = first.created_at ? new Date(first.created_at).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}) : '';
  return `<div class="worker-hero"><div class="worker-hero-line">${logo(node.provider)}<div class="worker-hero-copy"><h1>${escape(providers[node.provider] || node.provider)}<span> · ${escape(node.name)}</span></h1><p>${t(dispatched ? '由{who}在 {time} 派发' : '由{who}派发', { who: owner && owner.id !== node.id ? t('主控 {agent}', { agent: escape(providers[owner.provider] || owner.provider) }) : t('你'), time: escape(dispatched) })}${node.latest.task.workspace ? ` · ${t('工作目录')} <span class="path">${escape(displayPath(node.latest.task.workspace))}</span>` : ''}</p></div><div class="worker-hero-state ${escape(state.kind)}">${badge(node.state,state.label,state.kind)}<time>${escape(elapsed(node.latest,nowMs()))}</time></div></div><div class="worker-hero-actions"><button class="secondary" data-action="focus-chat">${icon('message')}${t('发消息')}</button><button class="secondary" data-action="assignment">${icon('file')}${t('派发说明')}</button>${node.latest.state === 'running' ? `<button class="secondary stop-text" data-stop="${escape(node.id)}" ${isStopping(node) ? 'disabled' : ''}>${icon('stop')}${isStopping(node) ? t('正在停止…') : t('停止')}</button>` : ''}${pending.length ? `<button class="secondary attention" data-focus-request="request-${escape(pending[0].call_id)}-${escape(pending[0].id)}">${icon('flag')}${t('待决 {count}', { count: pending.length })}</button>` : ''}</div></div>`;
}
function workerChanges(node) {
  const files = new Map();
  for (const call of node.calls) for (const group of turns((streamCache.get(call.id) || call.stream)?.events || [])) for (const item of group.items) {
    if (item.kind !== 'tool') continue;
    for (const change of item.changes || []) {
      const key = change.path || change.type || item.title; if (!key) continue;
      const entry = files.get(key) || { path: key, add: 0, del: 0 };
      for (const line of String(change.diff || '').split('\n')) { if (/^\+(?!\+\+)/.test(line)) entry.add++; else if (/^-(?!--)/.test(line)) entry.del++; }
      files.set(key, entry);
    }
  }
  return [...files.values()];
}
function workerRail(node) {
  const changes = workerChanges(node);
  const ids = new Set([node.id, ...node.calls.map(c => c.id)]);
  const mail = Object.values(data.messages).filter(m => m.message.kind !== 'user_message' && m.message.content && (ids.has(m.message.from) || ids.has(m.message.to))).sort((a,b)=>a.message.created_at.localeCompare(b.message.created_at));
  const name = id => { const n = view.nodes.get(id); return n ? escape(providers[n.provider] || n.provider) : t('主控'); };
  const time = value => value ? new Date(value).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '';
  const delivered = { delivered: '已送达', submitted: '已送达', needs_attention: '未送达' };
  const actor = { parent: '主控', user: '你', policy: '规则' };
  const verdict = d => /deny|reject|decline/i.test(String(d?.decision || d?.response?.action || '')) ? '拒绝' : d?.response ? '回答' : '允许';
  const requestText = r => { const q = r.request; return q.native_request?.command || q.payload?.command || q.title || q.description || t(q.kind === 'interaction_required' ? '提问' : '执行许可'); };
  const section = (title, body) => `<section class="rail-section"><h2>${title}</h2>${body}</section>`;
  const calls = [...node.calls].reverse();
  return `<aside class="worker-rail" aria-label="${t('Agent 详情')}">
    ${section(t('改动 · {count} 个文件', { count: changes.length }), changes.length ? `<div class="rail-card rail-files">${changes.map(f => `<div class="rail-file"><code title="${escape(f.path)}">${escape(f.path)}</code><span>${f.add ? `<b class="addition">+${f.add}</b>` : ''}${f.del ? `<b class="deletion">−${f.del}</b>` : ''}</span></div>`).join('')}</div>` : `<p class="rail-empty">${t('还没有记录到文件改动')}</p>`)}
    ${section(t('消息 · {count}', { count: mail.length }), mail.length ? mail.slice(-6).map(m => `<div class="rail-card rail-message"><small>${name(m.message.from)} → ${name(m.message.to)} · ${escape(time(m.message.created_at))}${m.delivery?.state ? ` · ${escape(t(delivered[m.delivery.state] || m.delivery.state))}` : ''}</small><p>${escape(m.message.content)}</p></div>`).join('') : `<p class="rail-empty">${t('没有协作消息')}</p>`)}
    ${section(t('审批 · {count}', { count: node.requests.length }), node.requests.length ? node.requests.map(r => `<button class="rail-card rail-request ${r.pending ? 'pending' : ''}" ${r.pending ? `data-focus-request="request-${escape(r.call_id)}-${escape(r.id)}"` : `data-request="${escape(r.call_id + '/' + r.id)}"`}><code>${escape(String(requestText(r)).slice(0, 120))}</code><small>${r.pending ? t(r.needsUser ? '等你决定' : '等主控代批') : r.decision ? `${escape(t('{actor}{verdict}', { actor: actor[r.decision.actor] ? t(actor[r.decision.actor]) : r.decision.actor || '', verdict: t(verdict(r.decision)) }))}${r.decision.scope === 'session' ? t(' · 本会话') : r.decision.scope === 'once' ? t(' · 本次') : ''}` : t('已处理')}</small></button>`).join('') : `<p class="rail-empty">${t('没有审批或提问')}</p>`)}
    ${section(t('调用记录'), `<div class="rail-card rail-calls">${calls.map((c, i) => { const sid = c.result?.native_session_id || c.handle?.native_session_id || c.task.native_session_id; return `<dl class="${i ? 'older-call' : ''}"><dt>${t('调用')}</dt><dd><code>${escape(c.id)}</code>${c.task.message_only ? t(' · 消息') : i === calls.length - 1 ? ' · run' : ' · resume'}</dd><dt>${t('状态')}</dt><dd>${escape(t(labels[c.state] || c.state))}${c.state === 'running' ? ` (${t(c.handle?.phase === 'startup' ? '启动中' : 'runner 持有锁')})` : ''}</dd>${c.task.model || c.task.effort ? `<dt>${t('模型')}</dt><dd>${escape([c.task.model, c.task.effort].filter(Boolean).join(' · '))}</dd>` : ''}${sid ? `<dt>${t('原生会话')}</dt><dd><code title="${escape(sid)}">${escape(String(sid).slice(0, 18))}${String(sid).length > 18 ? '…' : ''}</code></dd>` : ''}</dl>`; }).join('')}</div><p class="rail-note">${t('完整对话保存在 {agent} 自己的会话中；这里显示 AutoSwarm 的调用记录与过程。', { agent: escape(providers[node.provider] || node.provider) })}</p>`)}
  </aside>`;
}
function workerPage(node) {
  const prompt = node.calls[0].task.prompt || '';
  return `<div class="worker-layout"><div class="worker-main">${workerHeader(node)}<div class="conversation-scroll worker-scroll"><div class="reading-column"><section class="assignment-section" id="assignment"><h2>${t('主控的派发说明')}</h2>${prompt.length<240?`<div class="assignment-card">${escape(prompt)}</div>`:`<details class="assignment-card" data-key="assignment-${escape(node.id)}"><summary>${escape(prompt.split('\n')[0].slice(0,120))}</summary><div>${escape(prompt)}</div></details>`}</section><h2 class="section-title">${t('过程')}</h2>${conversation(node,true)}${node.requests.filter(r => r.pending).map(requestCard).join('')}<div class="inline-peers">${messagesFor([node])}</div></div></div><div class="bottom-area worker-bottom">${composer(node,true)}</div></div>${workerRail(node)}</div>`;
}
async function readStream(id, before) {
  if (reading.has(id) || isDemo || !desk.history) return;
  reading.add(id);
  const home = data.home;
  const epoch = streamEpoch;
  try {
    const result = await desk.history(id, before);
    if (home !== data.home || epoch !== streamEpoch) return;
    streamCache.set(id, combine(streamCache.get(id), result, before !== undefined));
    loaded.add(id); render();
  } catch (failure) {
    if (epoch === streamEpoch && home === data.home) { loaded.add(id); streamCache.set(id, {events:[],available:false,warning:failure.message}); render(); }
  }
  finally { if (epoch === streamEpoch) reading.delete(id); }
}
function readNative(key, version) {
  const entry = nativeCache.get(key) || {};
  if (entry.loading || isDemo || !desk.native) return;
  const wait = 2500 - (Date.now() - (entry.at || 0));
  if (entry.data && wait > 0) {
    if (!nativeTimers.has(key)) nativeTimers.set(key, setTimeout(() => { nativeTimers.delete(key); ensureStreams(); }, wait));
    return;
  }
  const home = data.home, epoch = streamEpoch;
  nativeCache.set(key, { ...entry, loading: true });
  desk.native(key).then(value => {
    if (home !== data.home || epoch !== streamEpoch) return;
    nativeCache.set(key, { version, data: value, at: Date.now() }); render(true);
  }).catch(failure => {
    if (home !== data.home || epoch !== streamEpoch) return;
    nativeCache.set(key, { ...entry, version, error: failure.message, at: Date.now(), loading: false }); render();
  });
}
function ensureStreams() {
  if (status !== 'ready') return;
  const nodes = overview ? [view.assistant] : workerId ? [view.nodes.get(workerId)] : current() ? [current(), ...current().members.filter(n => expanded.has(n.id) || tabOf() === 'trace')] : [];
  for (const node of nodes) {
    const summary = node?.native, key = node?.target || node?.id;
    if (summary && !summary.turns && nativeCache.get(key)?.version !== summary.version) readNative(key, summary.version);
  }
  for (const node of nodes) for (const call of node?.calls || []) {
    if (call.stream?.lazy && call.stream.available && !loaded.has(call.id)) readStream(call.id);
  }
}
async function paginate(event) {
  if (paging || Date.now() - userScrolling > 700) return;
  const pane = event.target;
  if (!(pane instanceof Element) || !pane.isConnected || !pane.matches('.conversation-scroll,.dock-body')) return;
  const older = pane.scrollTop <= 60;
  const newer = pane.scrollHeight - pane.clientHeight - pane.scrollTop <= 60;
  const edge = event.direction > 0 ? (newer ? pane.querySelector('[data-newer]') : null) : (older ? pane.querySelector('[data-history]') : null) || (newer ? pane.querySelector('[data-newer]') : null);
  if (!edge) return;
  const towardOlder = !!edge.dataset.history;
  const id = edge.dataset.history || edge.dataset.newer;
  if (reading.has(id)) return;
  paging = true;
  const offset = limits.get(id) || 0;
  if (towardOlder) {
    let stream = streamCache.get(id);
    const count = value => turns(value?.events || []).reduce((n,t)=>n+t.items.length,0);
    if (count(stream) <= offset + 80 && stream?.before > 0) await readStream(id, stream.before);
    stream = streamCache.get(id);
    if (count(stream) > offset + 80) limits.set(id, offset + 80);
  } else limits.set(id, Math.max(0, offset - 80));
  render();
  const next = document.querySelector(pane.matches('.dock-body') ? '.dock-body' : '.conversation-scroll');
  if (next) next.scrollTop = towardOlder ? 61 : Math.max(0,next.scrollHeight-next.clientHeight-61);
  setTimeout(() => { paging = false; }, 100);
}
document.addEventListener('scroll', paginate, true);
document.addEventListener('wheel', event => {
  const pane = event.target.closest?.('.conversation-scroll,.dock-body');
  if (!pane) return;
  userScrolling = Date.now();
  if ((event.deltaY < 0 && pane.scrollTop <= 60) || (event.deltaY > 0 && pane.scrollHeight-pane.clientHeight-pane.scrollTop <= 60)) paginate({target:pane,direction:event.deltaY});
}, {capture:true,passive:true});
document.addEventListener('pointerdown', event => { if (event.target.closest?.('.conversation-scroll,.dock-body')) userScrolling = Date.now(); }, true);
document.addEventListener('keydown', event => { if (['PageUp','PageDown','ArrowUp','ArrowDown','Home','End'].includes(event.key)) userScrolling = Date.now(); }, true);

document.addEventListener('submit', async event => {
  if (event.target.matches('.native-question-form')) {
    event.preventDefault(); const form=event.target, key=form.dataset.questionCall+'/'+form.dataset.questionId, request=data.requests[key];
    const button=form.querySelector('[type=submit]'); button.disabled=true;
    try { const response=encodeAnswer(request.request,new FormData(form)); if(isDemo){request.pending=false;request.decision={actor:'user',response};view=project(data);render();}else await desk.answer(request.call_id,request.id,response); }
    catch(failure){showToast(failure.message);}finally{button.disabled=false;} return;
  }
  if (event.target.id === 'chat-form') {
    event.preventDefault();
    const target=event.target.dataset.target, key=draftKey(target), text=chatDrafts.get(key)?.trim(), home=data.home;
    if(!text || chatPending.has(key))return;
    if(isDemo){showToast(t('示例会话不会发送消息；连接本地记录后即可直接对话。'));return;}
    chatPending.add(key);chatErrors.delete(key);render();
    try {
      const result=await desk.message(target,text,chatModes.get(key)||defaultMode(view.nodes.get(target)));
      if(chatDrafts.get(key)?.trim()===text)chatDrafts.delete(key);
      if(result.delivery?.state==='needs_attention')chatErrors.set(key,t(result.delivery.error || '消息未送达'));
    }catch(failure){chatErrors.set(key,failure.message);}
    finally{chatPending.delete(key);if(data.home===home)render(true);}
    return;
  }
  if (event.target.id === 'session-form') {
    event.preventDefault();
    if (!sessionDraft.prompt?.trim() || sessionSubmitting || pendingSession) return;
    if (isDemo) { showToast(t('这是界面演示，不会启动真实 Agent。连接本地记录目录后即可新建会话。')); return; }
    const value = { prompt: sessionDraft.prompt, provider: draftValue('provider') || 'claude-code', model: draftValue('model').trim(), effort: draftValue('effort').trim(), workspace: draftValue('workspace'), skill: skills?.hosts?.[draftValue('provider') || 'claude-code']?.state === 'ready' ? draftSkill(draftValue('provider') || 'claude-code') : undefined, permission: sessionDraft.permission || undefined };
    if (!value.workspace) { sessionError = t('请先选择工作目录。'); render(); return; }
    const home = data.home;
    sessionSubmitting = true; sessionError = ''; render();
    try {
      const result = await desk.sessionStart(value);
      if (data.home !== home) return;
      sessionDraft = { prompt: '' }; pendingSession = result.agent_id; openPending();
    } catch (failure) { if (data.home === home) sessionError = failure.message; }
    finally { sessionSubmitting = false; render(); }
    return;
  }
  if (!['assistant-form','assistant-settings-form'].includes(event.target.id)) return;
  event.preventDefault();
  if (event.target.id === 'assistant-settings-form') {
    const form = new FormData(event.target);
    try { await desk.assistantSettings({provider:form.get('provider') || assistantSettings.provider || 'claude-code',model:form.get('model'),effort:form.get('effort')}); showToast(t('助理设置已保存')); }
    catch (failure) { showToast(failure.message); }
    return;
  }
  if (!assistantDraft.trim() || assistantSubmitting || view.assistant?.latest.state === 'running') return;
  if (isDemo) { showToast(t('这是界面演示。连接本地记录目录后，可与真实私人助理对话。')); return; }
  const text = assistantDraft, home = data.home, epoch = streamEpoch;
  assistantSubmitting = true; assistantError = ''; render();
  try {
    await desk.assistantSend(text);
    if (assistantDrafts.get(home) === text) assistantDrafts.delete(home);
    if (data.home === home && epoch === streamEpoch && assistantDraft === text) assistantDraft = '';
  }
  catch (failure) { if (data.home === home && epoch === streamEpoch) assistantError = failure.message; }
  finally { if (data.home === home && epoch === streamEpoch) { assistantSubmitting = false; render(); } }
});
function rememberQuestion(event) {
  const form = event.target.closest?.('.native-question-form'); if (!form) return;
  const value = new FormData(form), fields = {};
  for (const field of form.elements) if (field.name) fields[field.name] = value.getAll(field.name);
  questionDrafts.set(data.home + ':' + form.dataset.questionCall + '/' + form.dataset.questionId, fields);
}
document.addEventListener('input',rememberQuestion);
document.addEventListener('change',rememberQuestion);
document.addEventListener('keydown', event => {
  if (event.target.id === 'search' && event.key === 'Escape') { search = ''; searchOpen = false; render(); return; }
  if (event.key === 'Escape' && openMenu) { const inSettings = openMenu.startsWith('a-'); openMenu = null; inSettings ? renderSettings() : render(true); event.preventDefault(); return; }
  if (['assistant-input','chat-input','session-input'].includes(event.target.id) && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); event.target.closest('form').requestSubmit();
  }
});

document.addEventListener('change', async event => {
  if (event.target.dataset?.pref && settings.contains(event.target)) {
    const key = event.target.dataset.pref, value = event.target.checked;
    try { await desk.preferences({ [key]: value }); showToast(value ? t('已开启') : t('已关闭')); }
    catch (failure) { event.target.checked = !value; showToast(failure.message); }
    return;
  }
  if(event.target.id==='chat-recipient'){chatTargets.set(selected,event.target.value);render();document.querySelector('#chat-input')?.focus();}
  if(event.target.name==='delivery-mode')chatModes.set(draftKey(chatTarget()),event.target.value);
  // Model IDs and effort values are native to each provider: switching clears them.
});
