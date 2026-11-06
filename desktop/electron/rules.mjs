// The user's trigger rules (triggers.json in Desk's data folder). A rule names
// an event, optionally narrows it, and does one thing: a custom notification,
// silencing the built-in one, handing the event to the private assistant, or
// sending a message to the session. The last two run through the AutoSwarm CLI
// (desk assistant / desk message) exactly like the user's own input.
import { providers } from '../renderer/model.mjs';
import i18n from './i18n.cjs';

const { t } = i18n;
export const EVENTS = ['waiting', 'done', 'failed'];
export const ACTIONS = ['notify', 'silence', 'assistant', 'message'];
const eventLabel = event => ({ waiting: t('等你决定'), done: t('已完成'), failed: t('失败') })[event] || event;
const ORIGINS = ['assistant', 'desk', 'external', 'cli'], KINDS = ['approval', 'question'];
const MATCH = ['provider', 'session', 'workspace', 'owner', 'kind'];
const FIELDS = ['session', 'provider', 'event', 'title', 'detail', 'workspace', 'rule'];
// Actions that start a model call fire once per session unless the rule says otherwise.
const COSTLY = new Set(['assistant', 'message']);

// A fixed marker in what the rule sends (records keep it; the renderer recognises it in any language).
export const rulePrefix = name => `[Desk 规则「${name}」] `;

const list = value => Array.isArray(value) ? value : [value];
const text = value => typeof value === 'string' && value.trim() !== '';

/** Parse triggers.json. Broken rules are reported and skipped; the rest stay active. */
export function parseRules(source) {
  let value;
  try { value = JSON.parse(String(source).replace(/^\uFEFF/, '')); }
  catch (error) { return { rules: [], errors: [t('规则文件不是有效的 JSON：{message}', { message: error.message })], disabled: 0 }; }
  const items = Array.isArray(value) ? value : value && typeof value === 'object' ? value.rules : undefined;
  if (items === undefined) return { rules: [], errors: [], disabled: 0 };
  if (!Array.isArray(items)) return { rules: [], errors: [t('"rules" 应该是一个数组')], disabled: 0 };
  const rules = [], errors = [];
  let disabled = 0;
  items.forEach((raw, index) => {
    const label = raw && text(raw.name) ? t('第 {index} 条「{name}」', { index: index + 1, name: raw.name.trim() }) : t('第 {index} 条', { index: index + 1 });
    const fail = message => errors.push(t('{label}：{message}', { label, message }));
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail(t('应该是一个对象'));
    if (raw.enabled === false) { disabled++; return; }
    const name = text(raw.name) ? raw.name.trim().slice(0, 60) : t('规则 {index}', { index: index + 1 });
    const on = list(raw.on);
    if (!raw.on || !on.length || !on.every(event => EVENTS.includes(event))) return fail(t('on 应该是 {values}', { values: EVENTS.join(' / ') }));
    if (!ACTIONS.includes(raw.do)) return fail(t('do 应该是 {values}', { values: ACTIONS.join(' / ') }));
    const match = {};
    if (raw.match !== undefined) {
      if (!raw.match || typeof raw.match !== 'object' || Array.isArray(raw.match)) return fail(t('match 应该是一个对象'));
      for (const [key, wanted] of Object.entries(raw.match)) {
        if (!MATCH.includes(key)) return fail(t('match 不认识「{key}」，可用 {values}', { key, values: MATCH.join(' / ') }));
        const values = list(wanted);
        if (!values.length || !values.every(text)) return fail(t('match.{key} 应该是文字或文字数组', { key }));
        if (key === 'owner' && !values.every(v => ORIGINS.includes(v))) return fail(t('match.owner 应该是 {values}', { values: ORIGINS.join(' / ') }));
        if (key === 'kind' && !values.every(v => KINDS.includes(v))) return fail(t('match.kind 应该是 {values}', { values: KINDS.join(' / ') }));
        match[key] = values.map(v => v.trim());
      }
    }
    if (COSTLY.has(raw.do) && !text(raw.text)) return fail(t('do 为 {action} 时需要 text', { action: raw.do }));
    for (const key of ['text', 'title']) if (raw[key] !== undefined && (typeof raw[key] !== 'string' || raw[key].length > 4000)) return fail(t('{key} 应该是不超过 4000 字的文字', { key }));
    if (raw.limit !== undefined && !(Number.isInteger(raw.limit) && raw.limit >= 1 && raw.limit <= 100)) return fail(t('limit 应该是 1 到 100 的整数'));
    rules.push({ index, name, on, match, do: raw.do, text: raw.text || '', title: raw.title || '', limit: raw.limit ?? (COSTLY.has(raw.do) ? 1 : Infinity) });
  });
  const names = new Set();
  for (const rule of rules) {
    if (names.has(rule.name)) errors.push(t('规则名「{name}」重复；触发次数按名字计算，请改成不同的名字', { name: rule.name }));
    names.add(rule.name);
  }
  return { rules, errors, disabled };
}

const path = value => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();

/** The fields a rule can match and a template can use, for one occurrence. */
export function context(entry) {
  const session = entry.session, item = entry.item || {};
  return {
    event: entry.on, provider: entry.provider || '', sessionId: session?.id || item.session || null, external: !!session?.external,
    session: session?.name || item.sessionName || '', owner: session?.origin || '',
    workspace: session?.workspace || item.workspace || '',
    kind: entry.on === 'waiting' ? (item.kind === 'interaction_required' ? 'question' : 'approval') : '',
    title: entry.on === 'waiting' ? item.title || '' : entry.scope === 'call' ? item.name || '' : '',
    detail: entry.on === 'waiting' ? item.detail || '' : ''
  };
}

export function matches(rule, ctx) {
  if (!rule.on.includes(ctx.event)) return false;
  const m = rule.match;
  if (m.provider && !m.provider.some(v => [ctx.provider, providers[ctx.provider]].filter(Boolean).some(p => p.toLowerCase() === v.toLowerCase()))) return false;
  if (m.session && !m.session.some(v => ctx.session.toLowerCase().includes(v.toLowerCase()))) return false;
  if (m.workspace && !m.workspace.some(v => { const w = path(v), c = path(ctx.workspace); return !!c && (c === w || c.startsWith(w + '/')); })) return false;
  if (m.owner && !m.owner.includes(ctx.owner)) return false;
  if (m.kind && !m.kind.includes(ctx.kind)) return false;
  return true;
}

/** Fill {session} {provider} {event} {title} {detail} {workspace} {rule}; anything else stays as written. */
export function render(template, ctx, rule) {
  const values = { ...ctx, provider: providers[ctx.provider] || ctx.provider, event: eventLabel(ctx.event), rule: rule?.name || '' };
  return String(template).replace(/\{(\w+)\}/g, (all, key) => FIELDS.includes(key) ? String(values[key] ?? '') : all);
}

/**
 * Apply the rules to what just happened. `counts` (a Map kept by the caller)
 * remembers how often each rule fired for each session, so a rule that sends
 * a message to a session cannot keep re-triggering itself when that session
 * finishes again. Returns the actions to run and which occurrences no longer
 * get a built-in notification (silenced, or replaced by a custom one).
 */
export function plan(rules, entries, counts = new Map()) {
  const actions = [], quiet = new Set();
  for (const entry of entries) {
    const ctx = context(entry);
    for (const rule of rules) {
      if (!matches(rule, ctx)) continue;
      if (rule.do === 'silence' || rule.do === 'notify') quiet.add(entry);
      const action = { rule: rule.name, do: rule.do, entry, ctx, target: entry.target };
      if (rule.do === 'notify') {
        action.title = render(rule.title || `{provider} · {event}`, ctx, rule);
        action.text = render(rule.text || [ctx.title && ctx.detail ? '{title} {detail}' : ctx.title ? '{title}' : '', ctx.session ? '{session}' : ''].filter(Boolean).join('\n'), ctx, rule);
      } else if (COSTLY.has(rule.do)) {
        const key = rule.name + '\u0000' + (ctx.sessionId || entry.key);
        const used = counts.get(key) || 0;
        if (used >= rule.limit) { action.skipped = 'limit'; actions.push(action); continue; }
        if (rule.do === 'message' && (!ctx.sessionId || ctx.external)) { action.skipped = 'no-session'; actions.push(action); continue; }
        counts.set(key, used + 1);
        action.text = rulePrefix(rule.name) + render(rule.text, ctx, rule);
      }
      actions.push(action);
    }
  }
  return { actions, quiet };
}

/**
 * The file Desk creates when the user opens rules for the first time, written in
 * Desk's current language (t() without values keeps the {placeholders}). Every example starts disabled.
 */
export function template() {
  return JSON.stringify({
    [t('说明')]: [
      t('AutoSwarm Desk 的触发规则。保存后立即生效，不需要重启；把 enabled 改成 true 即可启用示例。'),
      t('on：waiting（有事等你决定）、done（会话全部返回）、failed（会话或子任务失败）；可以写成数组。'),
      t('match（可选）：provider（codex、claude-code、cursor…）、session（会话名包含的文字）、workspace（工作目录及其子目录）、owner（assistant 助理交出的、desk 你在 Desk 新建的、external 外部主控、cli 命令行发起的）、kind（approval 审批、question 提问）。'),
      t('do：notify 自定义系统通知（title、text）并替代内置通知；silence 不弹内置通知；assistant 把 text 交给私人助理；message 把 text 发给这个会话的主 Agent。'),
      t('title 和 text 里可以用 {session} {provider} {event} {title} {detail} {workspace} {rule}。'),
      t('assistant 和 message 会调用模型：同一条规则对同一个会话默认只触发 1 次，可以用 limit 调大。')
    ],
    rules: [
      { name: t('失败时请助理看看'), enabled: false, on: 'failed', do: 'assistant', text: t('「{session}」里的 {provider} 失败了（{title}）。请用 receive 或 inspect 看看原因，告诉我该怎么处理。') },
      { name: t('外部会话完成时不弹通知'), enabled: false, on: 'done', match: { owner: 'external' }, do: 'silence' },
      { name: t('Codex 审批写清会话'), enabled: false, on: 'waiting', match: { provider: 'codex', kind: 'approval' }, do: 'notify', title: t('{provider} 等你批准'), text: '{session}\n{title} {detail}' },
      { name: t('完成后自查一遍'), enabled: false, on: 'done', match: { owner: 'desk', workspace: 'D:\\your\\project' }, do: 'message', text: t('请检查一下刚才的改动有没有遗漏，并运行相关测试；没有问题就简短告诉我。') }
    ]
  }, null, 2) + '\n';
}
