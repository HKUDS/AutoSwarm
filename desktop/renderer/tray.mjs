// Tray popup (design 04): everything that needs the user across all sessions,
// decided right here; what is running; and a way into each session. Frames and
// decisions go through the same preload IPC as the main window.
import { project, providers, logoFile } from './model.mjs';
import { describeRequest, questionForm, encodeAnswer } from './interactions.mjs';
import { icon } from './icons.mjs';
import { elapsed, workerStatus } from './worker-view.mjs';
import { createDemo } from '../electron/sample.mjs';
import { t, setLanguage } from './i18n.mjs';

const preview = !window.desk;
const desk = window.desk || {
  initial: async () => ({ event: 'snapshot', data: createDemo({ showcase: true }), demo: true, shell: { notify: true }, language: new URLSearchParams(location.search).get('lang') || 'en' }),
  subscribe: () => () => {}, decide: async () => {}, answer: async () => {}, show: async () => {}, tray: async () => {}, preferences: async () => {}
};
const root = document.querySelector('#tray');
let frame = null, data = null, view = null;
const pending = new Map(), failures = new Map();
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const name = provider => providers[provider] || provider || 'Agent';
const logo = provider => `<img class="provider-logo" src="../assets/agents/${logoFile(provider)}" alt="">`;
const nowMs = () => frame?.demo ? Date.parse(data.observed_at || '2026-11-01T09:16:00Z') : Date.now();
const waited = at => { const time = Date.parse(at); if (isNaN(time)) return ''; const s = Math.max(0, Math.floor((nowMs() - time) / 1000)); return s < 3600 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : t('{count} 小时', { count: Math.floor(s / 3600) }); };
const mark = '<img class="swarm-mark autoswarm-logo" src="../assets/logo-128.png" width="18" height="18" alt="">';

function sessionOf(node) { return view.sessions.find(s => s.members.includes(node)); }
function needs() {
  return [...view.nodes.values()].flatMap(node => node.requests.filter(r => r.needsUser).map(entry => ({ entry, node, session: sessionOf(node) })))
    .sort((a, b) => String(a.entry.request.created_at || '').localeCompare(String(b.entry.request.created_at || '')));
}

function card({ entry, node, session }) {
  const req = entry.request, key = entry.call_id + '/' + entry.id, d = describeRequest(req);
  const question = req.kind === 'interaction_required';
  const where = node === view.assistant ? t('私人助理') : [session?.name, session && session.id !== node.id ? node.name : ''].filter(Boolean).join(' · ');
  const canDecide = !question && (req.scopes || []).includes('once');
  const form = question ? questionForm(entry, d.title) : '';
  const busy = pending.get(key);
  const body = question
    ? `${form && d.title === t('Agent 需要你的回答') ? '' : `<p class="tray-question">${esc(d.title)}</p>`}${form || `<button class="secondary tray-wide" data-open-request="${esc(key)}">${t('在主窗口回答')}</button>`}`
    : `<div class="tray-card-title">${esc(d.title)}${d.subject ? ` <code>${esc(d.subject)}</code>` : ''}</div>${d.command ? `<pre class="tray-command">${esc(d.command)}</pre>` : d.preview ? `<pre class="tray-command">${esc(d.preview)}</pre>` : ''}${d.reason ? `<p class="tray-reason">${esc(d.reason)}</p>` : ''}${canDecide ? `<div class="tray-actions"><button class="primary" data-decide="approve" data-key="${esc(key)}" ${busy ? 'disabled' : ''}>${busy === 'approve' ? t('正在允许…') : t('允许')}</button><button class="secondary" data-decide="deny" data-key="${esc(key)}" ${busy ? 'disabled' : ''}>${busy === 'deny' ? t('正在拒绝…') : t('拒绝')}</button></div>` : `<button class="secondary tray-wide" data-open-request="${esc(key)}">${t('在主窗口处理')}</button>`}`;
  return `<article class="tray-card" data-request-key="${esc(key)}"><div class="tray-card-head">${logo(node.provider)}<strong>${esc(question ? t('{agent} 在问', { agent: name(node.provider) }) : t('{agent} 请求执行', { agent: name(node.provider) }))}</strong><time>${esc(waited(req.created_at))}</time></div><button class="tray-where" data-open-request="${esc(key)}" title="${t('在主窗口中查看')}">${esc(where || t('打开会话'))}</button>${body}${failures.has(key) ? `<p class="chat-error" role="alert">${esc(failures.get(key))}</p>` : ''}</article>`;
}

function running(waitingNodes) {
  const list = [...view.nodes.values()].filter(n => n.latest?.state === 'running' && !waitingNodes.has(n));
  if (!list.length) return '';
  const row = node => {
    const status = workerStatus(node, node.latest.stream), session = sessionOf(node);
    const target = session ? { session: session.id, worker: session.id === node.id ? undefined : node.id } : {};
    return `<button class="tray-row" data-open='${esc(JSON.stringify(target))}'>${logo(node.provider)}<span class="tray-row-copy"><strong>${esc(name(node.provider))}${node === view.assistant ? ' · ' + t('私人助理') : ''}</strong><small><i class="tray-dot"></i>${esc(status.activity || t('正在处理'))}</small></span><time>${esc(elapsed(node.latest, nowMs()))}</time></button>`;
  };
  return `<section class="tray-section"><h2 class="tray-label">${t('运行中 · {count}', { count: list.length })}</h2>${list.slice(0, 6).map(row).join('')}${list.length > 6 ? `<p class="tray-more">${t('还有 {count} 个在运行', { count: list.length - 6 })}</p>` : ''}</section>`;
}

function sessions(items) {
  if (!view.sessions.length) return '';
  const waitingIn = s => items.filter(i => i.session === s).length;
  const ordered = [...view.sessions].sort((a, b) => waitingIn(b) - waitingIn(a) || Number(a.done) - Number(b.done) || b.updated.localeCompare(a.updated));
  const row = s => {
    const runningCount = s.members.filter(n => n.latest?.state === 'running').length, w = waitingIn(s);
    const state = w ? `<span class="amber">${t('{count} 等你', { count: w })}</span>` : runningCount ? `<span class="blue">${t('{count} 运行', { count: runningCount })}</span>` : s.done ? `<span class="tray-done">${icon('check')}${t('已完成')}</span>` : '';
    return `<button class="tray-row tray-session" data-open='${esc(JSON.stringify({ session: s.id }))}'>${logo(s.provider)}<span class="tray-row-copy"><strong>${esc(s.name)}</strong></span><span class="tray-state">${state}</span></button>`;
  };
  return `<section class="tray-section"><h2 class="tray-label">${t('会话')}</h2>${ordered.slice(0, 5).map(row).join('')}${ordered.length > 5 ? `<button class="tray-more" data-open='{}'>${t('全部 {count} 个会话 ↗', { count: ordered.length })}</button>` : ''}</section>`;
}

function render() {
  if (!data) return;
  const items = needs(), waitingNodes = new Set(items.map(i => i.node));
  const notify = frame?.shell?.notify !== false;
  const focusedForm = document.activeElement?.closest?.('.native-question-form');
  const draft = focusedForm ? { key: focusedForm.dataset.questionCall + '/' + focusedForm.dataset.questionId, value: document.activeElement.value, name: document.activeElement.name } : null;
  root.innerHTML = `<header class="tray-head">${mark}<strong>AutoSwarm</strong>${frame?.demo ? `<span class="demo-badge">${t('演示')}</span>` : ''}<button class="icon-button" data-open='{}' title="${t('打开主窗口')}" aria-label="${t('打开主窗口')}">${icon('external')}</button></header>
    <div class="tray-scroll">${items.length ? `<section class="tray-section"><h2 class="tray-label attention">${t('需要你 · {count}', { count: items.length })}</h2>${items.map(card).join('')}</section>` : `<div class="tray-clear">${icon('check')}<span>${t('没有需要你决定的事')}</span></div>`}${running(waitingNodes)}${sessions(items)}</div>
    <footer class="tray-foot"><label><input type="checkbox" data-pref="notify" ${notify ? 'checked' : ''}>${t('agent 需要我时弹系统通知')}</label><button class="text-button" data-open='{}'>${t('打开主窗口')}</button></footer>`;
  if (draft) {
    const form = [...root.querySelectorAll('.native-question-form')].find(f => f.dataset.questionCall + '/' + f.dataset.questionId === draft.key);
    const field = form?.querySelector(`[name="${draft.name}"]`);
    if (field && 'value' in field && field.type !== 'radio') { field.value = draft.value; field.focus(); }
  }
  requestAnimationFrame(() => {
    const head = root.querySelector('.tray-head'), foot = root.querySelector('.tray-foot'), scroll = root.querySelector('.tray-scroll');
    desk.tray({ height: head.offsetHeight + foot.offsetHeight + scroll.scrollHeight + 2 });
  });
}

function receive(value) {
  if (!value) return;
  frame = value;
  setLanguage(frame.language);
  if (frame.theme) {
    const dark = frame.theme === 'dark' || (frame.theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }
  if (frame.event !== 'snapshot') { if (!data) root.innerHTML = `<div class="tray-loading">${esc(frame.message || t('正在读取本地记录…'))}</div>`; return; }
  data = frame.data; view = project(data);
  for (const key of pending.keys()) if (!data.requests[key]?.pending) pending.delete(key);
  render();
}

function settleDemo(key, decision) {
  const entry = data.requests[key];
  entry.pending = false; entry.decision = { actor: 'user', ...decision };
  view = project(data); render();
}

root.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.open !== undefined || button.dataset.openRequest) {
    let target = {};
    if (button.dataset.openRequest) {
      const key = button.dataset.openRequest, entry = data.requests[key], node = view.nodes.get(data.calls[entry?.call_id]?.agent_id);
      target = { session: node ? sessionOf(node)?.id : undefined, request: key };
    } else target = JSON.parse(button.dataset.open || '{}');
    await desk.show(target);
    return;
  }
  if (button.dataset.decide) {
    const key = button.dataset.key, [call, rid] = key.split('/');
    pending.set(key, button.dataset.decide); failures.delete(key); render();
    try {
      if (frame.demo) settleDemo(key, { decision: button.dataset.decide });
      else await desk.decide(call, rid, button.dataset.decide);
    } catch (error) { pending.delete(key); failures.set(key, error.message); render(); }
  }
});
root.addEventListener('submit', async event => {
  const form = event.target.closest('.native-question-form');
  if (!form) return;
  event.preventDefault();
  const key = form.dataset.questionCall + '/' + form.dataset.questionId, entry = data.requests[key];
  try {
    const response = encodeAnswer(entry.request, new FormData(form));
    pending.set(key, 'answer'); failures.delete(key);
    form.querySelectorAll('button, input, textarea').forEach(el => { el.disabled = true; });
    if (frame.demo) settleDemo(key, { response });
    else await desk.answer(entry.call_id, entry.id, response);
  } catch (error) { pending.delete(key); failures.set(key, error.message); render(); }
});
// A single choice answers in one click, like the option buttons in the design.
root.addEventListener('change', async event => {
  if (event.target.dataset.pref) { await desk.preferences({ [event.target.dataset.pref]: event.target.checked }); return; }
  const form = event.target.closest('.native-question-form');
  if (form && event.target.type === 'radio' && form.querySelectorAll('fieldset').length === 1) form.requestSubmit();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !preview) desk.tray('hide');
  // Typed answers go out with Enter (Shift+Enter for a new line).
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.target.matches?.('.native-question-form textarea') && event.target.value.trim()) {
    event.preventDefault(); event.target.closest('form').requestSubmit();
  }
});

// tray.html's placeholder follows the language until the first frame arrives.
root.querySelector('.tray-loading')?.replaceChildren(t('正在读取本地记录…'));
desk.subscribe(receive);
desk.initial().then(receive);
// Waiting times keep counting while the popup is open.
setInterval(() => { if (data && view && document.visibilityState === 'visible' && !root.contains(document.activeElement?.closest?.('form'))) render(); }, 15000);
