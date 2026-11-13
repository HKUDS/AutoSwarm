// What needs the user, what is running and what just finished, computed from a
// watcher snapshot. The tray, the taskbar badge, the built-in triggers
// (notifications, optional assistant wake-up) and the user's trigger rules all
// read this one summary, so a request decided anywhere disappears everywhere.
import { project, providers } from '../renderer/model.mjs';
import { describeRequest } from '../renderer/interactions.mjs';
import i18n from './i18n.cjs';

const { t } = i18n;

const name = provider => providers[provider] || provider || 'Agent';

export function summarize(data) {
  const view = project(data);
  const assistantId = view.assistant?.id, controllerId = data.desk?.controller_id;
  const sessionOf = new Map();
  for (const session of view.sessions) for (const node of session.members) sessionOf.set(node.id, session);
  const waiting = [];
  for (const node of view.nodes.values()) for (const entry of node.requests) {
    if (!entry.needsUser) continue;
    const session = sessionOf.get(node.id), described = describeRequest(entry.request);
    waiting.push({
      key: entry.call_id + '/' + entry.id, call: entry.call_id, request: entry.id, kind: entry.request.kind,
      provider: node.provider, agent: node.id, agentName: node.name, session: session?.id || null,
      sessionName: session?.name || (node === view.assistant ? t('私人助理') : ''),
      workspace: node.latest.task.workspace || '',
      title: described.title, detail: described.command || described.subject || '', created_at: entry.request.created_at || ''
    });
  }
  waiting.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.key.localeCompare(b.key));
  const running = [...view.nodes.values()].filter(node => node.latest?.state === 'running');
  const sessions = view.sessions.map(session => ({
    id: session.id, name: session.name, provider: session.provider, owner: session.owner || null, external: !!session.external,
    // Who started it: the private assistant, the user in Desk, an external host, or a human on the command line.
    origin: session.external ? 'external' : session.owner && session.owner === assistantId ? 'assistant' : session.owner && session.owner === controllerId ? 'desk' : 'cli',
    workspace: (session.external ? session.binding?.workspace : session.latest?.task.workspace) || '',
    done: session.done, failed: session.members.some(node => node.state === 'failed'),
    stopped: !session.external && session.latest?.result?.outcome === 'cancelled',
    running: session.members.filter(node => node.latest?.state === 'running').length,
    waiting: waiting.filter(item => item.session === session.id).length, total: session.members.length
  }));
  const failed = [...view.nodes.values()].filter(node => node.state === 'failed' && node !== view.assistant).map(node => ({
    agent: node.id, call: node.latest.id, provider: node.provider, name: node.name, workspace: node.latest.task.workspace || '',
    session: sessionOf.get(node.id)?.id || null, sessionName: sessionOf.get(node.id)?.name || ''
  }));
  const assistant = view.assistant ? { id: view.assistant.id, state: view.assistant.latest.state } : null;
  return { waiting, running: running.length, sessions, failed, assistant };
}

/**
 * Everything that just happened between two summaries, one entry per thing:
 * a new request that needs the user (`waiting`), a session whose members have
 * all returned (`done`, or `failed` when one of them failed), and a failure
 * inside a session that is still going (`failed`, scope `call`). The first
 * summary after connecting only sets the baseline; existing state never fires.
 */
export function occurrences(before, after) {
  if (!before) return [];
  const out = [];
  const sessions = new Map(after.sessions.map(session => [session.id, session]));
  const known = new Set(before.waiting.map(item => item.key));
  for (const item of after.waiting) if (!known.has(item.key)) {
    out.push({ on: 'waiting', scope: 'request', key: item.key, item, session: sessions.get(item.session) || null, provider: item.provider,
      target: { session: item.session, request: item.key } });
  }
  const prior = new Map(before.sessions.map(session => [session.id, session]));
  const ended = new Set();
  for (const session of after.sessions) {
    // A session absent from the previous summary is new since the baseline; a
    // short one may start and finish between two frames and still counts.
    const old = prior.get(session.id);
    if (old?.done || !session.done || session.stopped) continue;
    ended.add(session.id);
    out.push({ on: session.failed ? 'failed' : 'done', scope: 'session', key: 'session:' + session.id, session, provider: session.provider, target: { session: session.id } });
  }
  const failedBefore = new Set(before.failed.map(item => item.call));
  for (const item of after.failed) {
    if (failedBefore.has(item.call) || ended.has(item.session)) continue;
    out.push({ on: 'failed', scope: 'call', key: 'call:' + item.call, item, session: sessions.get(item.session) || null, provider: item.provider,
      target: { session: item.session, worker: item.agent } });
  }
  return out;
}

/** The built-in notifications for some occurrences; more than two new requests become one. */
export function notifications(list) {
  const out = [];
  const fresh = list.filter(entry => entry.on === 'waiting').map(entry => entry.item);
  if (fresh.length > 2) {
    out.push({ kind: 'waiting', keys: fresh.map(item => item.key), title: t('{count} 件事等你决定', { count: fresh.length }),
      body: fresh.slice(0, 3).map(item => `${name(item.provider)} · ${item.title}${item.detail ? ' ' + item.detail : ''}`).join('\n'),
      target: { session: fresh[0].session, request: fresh[0].key } });
  } else for (const item of fresh) {
    out.push({ kind: 'waiting', keys: [item.key], title: item.kind === 'interaction_required' ? t('{agent} 在问你', { agent: name(item.provider) }) : t('{agent} 需要你决定', { agent: name(item.provider) }),
      body: [`${item.title}${item.detail ? ' ' + item.detail : ''}`, item.sessionName].filter(Boolean).join('\n'),
      target: { session: item.session, request: item.key } });
  }
  for (const entry of list) {
    if (entry.scope === 'session') {
      const session = entry.session;
      out.push({ kind: entry.on, session, title: session.failed ? t('会话已结束，有失败') : t('会话已完成'),
        body: [session.name, session.failed ? t('打开查看失败原因') : t('主控已返回')].join(' · '), target: entry.target });
    } else if (entry.scope === 'call') {
      out.push({ kind: 'failed', title: t('{agent} 失败', { agent: name(entry.item.provider) }), body: [entry.item.name, entry.item.sessionName].filter(Boolean).join(' · '), target: entry.target });
    }
  }
  return out;
}

/** Built-in triggers between two summaries (notifications for every occurrence). */
export function triggers(before, after) {
  return notifications(occurrences(before, after));
}

/** Tooltip, badge and menu wording for the tray. */
export function trayText(summary) {
  const waiting = summary?.waiting.length || 0, running = summary?.running || 0;
  const parts = [waiting ? t('{count} 件等你决定', { count: waiting }) : '', running ? t('{count} 个运行中', { count: running }) : ''].filter(Boolean);
  return { waiting, running, tooltip: 'AutoSwarm Desk' + (parts.length ? ' · ' + parts.join(' · ') : '') };
}
