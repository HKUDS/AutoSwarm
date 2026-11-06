import { t } from './i18n.mjs';
import agents from './agents.json' with { type: 'json' };
// The agents Desk knows, in menu order, from the one list the main process reads too.
export const providers = agents.names;
const PNG_LOGOS = new Set(agents.pngLogos);
/** An agent's logo file (assets/agents), or the generic one. */
export const logoFile = provider => providers[provider] ? `${provider}.${PNG_LOGOS.has(provider) ? 'png' : 'svg'}` : 'more.svg';
export const labels = { running: '运行中', waiting: '等你决定', delegated: '上级代批中', completed: '已返回', failed: '失败', cancelled: '已停止', interrupted: '已中断' };
export function taskTitle(value, fallback = t('新任务')) {
  const text = String(value || '').split(/##? (?:My request|我的请求)[:：]?\s*/i).at(-1);
  const line = text.split('\n').map(x => x.replace(/^#+\s*|^[>\s]+/g, '').trim()).find(x => x && !/^(?:Files mentioned|Referenced chats|<)/i.test(x));
  return line ? line.slice(0, 60) : fallback;
}
export const isAssistant = agent => typeof agent?.name === 'string' && (agent.name === 'desk-assistant' || agent.name.startsWith('desk-assistant.'));
export function project(data) {
  const nodes = new Map(), aliases = new Map(), nativeAgents = new Map();
  const retainedDesks = Object.values(data.agents).filter(a => a.desk_controller === true);
  const controllerId = data.desk?.controller_id || (retainedDesks.length === 1 ? retainedDesks[0].agent_id : undefined);
  // One assistant conversation per agent (desk-assistant, desk-assistant.<agent>); the pane shows the chosen agent's.
  const assistantIds = new Set([...Object.values(data.agents).filter(a => controllerId && a.owner_id === controllerId && isAssistant(a)).map(a => a.agent_id),
    ...(data.desk?.assistants || []).map(a => a.agent_id), ...(data.desk?.assistant_agent_id ? [data.desk.assistant_agent_id] : [])]);
  // With the list from Desk, no id means the chosen agent has no assistant conversation yet; without it
  // (an older binding, a lost launch reply) the record itself names the assistant.
  const assistantId = data.desk?.assistant_agent_id || (Array.isArray(data.desk?.assistants) ? null : [...assistantIds][0]);
  for (const call of Object.values(data.calls)) {
    const id = call.agent_id || call.id;
    aliases.set(call.id, id);
    if (!nodes.has(id)) nodes.set(id, { id, calls: [], children: [], requests: [] });
    nodes.get(id).calls.push(call);
    const sid = call.result?.native_session_id || call.handle?.native_session_id || call.task.native_session_id;
    if (sid) nativeAgents.set(call.task.provider + ':' + sid, id);
  }
  // A child can itself invoke AutoSwarm from its native host. That controller
  // is an alias of the existing worker, not a second main conversation.
  for (const [id, record] of Object.entries(data.agents)) {
    const binding = record.parent;
    const managed = binding && nativeAgents.get(binding.host + ':' + binding.session_id);
    if (managed && managed !== id) aliases.set(id, managed);
  }
  for (const node of nodes.values()) {
    node.calls.sort((a, b) => (a.task.created_at || '').localeCompare(b.task.created_at || '') || a.id.localeCompare(b.id));
    const record = data.agents[node.id] || {};
    node.latest = node.calls.find(c => c.id === record.call_id) || node.calls.at(-1);
    node.provider = node.latest.task.provider;
    node.native = data.native_conversations?.[node.id];
    const name = record.title || record.name || node.latest.task.communication?.name;
    node.name = taskTitle(node.native?.title || (name && !/^[\w.-]+$/.test(name) ? name : node.calls[0].task.prompt), name || t('新任务'));
    node.owner = record.owner_id ?? node.latest.task.controller_id;
    node.owner = aliases.get(node.owner) || node.owner;
    node.state = node.latest.state;
    node.idle = record.idle === true;
  }
  const sessions = new Map();
  const external = (key, node, binding, missing = false) => {
    const native = data.native_conversations?.[node.owner];
    if (!sessions.has(key)) sessions.set(key, { id: key, target: node.owner, provider: binding?.host || node.provider,
      name: taskTitle(native?.title || binding?.title || node.calls[0].task.prompt, t('协作任务')), native,
      external: true, binding: {...binding, workspace: native?.workspace || binding?.workspace || node.latest.task.workspace}, missingOwner: missing, children: [] });
    sessions.get(key).children.push(node);
  };
  for (const node of nodes.values()) {
    // A broken or cyclic owner chain must never swallow a session or recurse forever.
    const visited = new Set([node.id]);
    let parent = nodes.get(node.owner), cursor = parent, cycle = false;
    while (cursor) {
      if (visited.has(cursor.id)) { cycle = true; break; }
      visited.add(cursor.id); cursor = nodes.get(cursor.owner);
    }
    if (parent && !cycle) { parent.children.push(node); node.parent = parent; }
    if (assistantIds.has(node.id)) continue;
    if (!cycle && (assistantIds.has(node.owner) || (controllerId && node.owner === controllerId))) sessions.set(node.id, node);
    else if (!parent || cycle) {
      const controller = data.agents[node.owner];
      const binding = controller?.parent || node.latest.task.parent;
      if (cycle || (node.owner?.startsWith('call_') && !parent)) {
        external('unresolved:' + (cycle ? [...visited].sort().join(':') : node.owner), node, binding, true);
      } else if (binding?.session_id) {
        const key = 'external:' + JSON.stringify([binding.host, binding.session_id, binding.host_instance]);
        external(key, node, binding);
      } else if (controller && !controller.parent && !controller.call_id) {
        // A CLI-created human controller has no native agent of its own.
        // Its actual run is the main conversation users can continue.
        sessions.set(node.id, node);
      } else if (node.owner) {
        external('external:' + node.owner, node, binding);
      } else sessions.set(node.id, node);
    }
  }
  for (const request of Object.values(data.requests)) {
    const call = data.calls[request.call_id], node = nodes.get(call?.agent_id || call?.id);
    if (!node) continue;
    const parent = nodes.get(node.owner);
    const needsUser = request.pending && (request.request.user_required || !request.delegate_to_parent || !parent || parent.latest.state !== 'running');
    node.requests.push({ ...request, needsUser, node });
  }
  for (const node of nodes.values()) {
    if (node.requests.some(r => r.needsUser)) node.state = 'waiting';
    else if (node.requests.some(r => r.pending)) node.state = 'delegated';
  }
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  for (const session of sessions.values()) {
    session.members = session.external ? session.children.flatMap(flatten) : flatten(session);
    session.counts = count(session.members);
    session.updated = session.members.reduce((date, n) => n.latest.task.created_at > date ? n.latest.task.created_at : date, '');
    session.done = session.native?.busy !== true && session.members.every(n => ['completed', 'failed', 'cancelled'].includes(n.state));
  }
  const assistants = [...assistantIds].map(id => nodes.get(id)).filter(Boolean).sort((a, b) => (b.latest?.task.created_at || '').localeCompare(a.latest?.task.created_at || ''));
  return { nodes, assistant: nodes.get(assistantId), assistants, sessions: [...sessions.values()].sort((a, b) => b.updated.localeCompare(a.updated)) };
}
export function count(nodes) {
  return { running: nodes.filter(n => n.state === 'running').length, waiting: nodes.filter(n => n.state === 'waiting').length,
    completed: nodes.filter(n => n.state === 'completed').length, total: nodes.length };
}
