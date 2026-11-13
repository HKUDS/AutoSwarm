// IDs merge within protocol turns, never across turn boundaries.
export function turns(events) {
  const result = [], fresh = () => ({ items: [], ids: new Map(), ended: false });
  let turn = fresh();
  for (const event of events) {
    if (event.kind === 'turn') {
      turn.ended = true; turn.status = event.status;
      const last = [...turn.items].reverse().find(item => item.kind === 'message');
      if (last) last.final = true;
      result.push(turn); turn = fresh(); continue;
    }
    let old = event.id ? turn.ids.get(event.id) : null;
    if (!event.id && ['message', 'thought'].includes(event.kind) && !event.replace && turn.items.at(-1)?.kind === event.kind) old = turn.items.at(-1);
    if (old) {
      const text = event.text === undefined ? old.text : event.replace ? event.text : (old.text || '') + event.text;
      const output = event.outputDelta ? (typeof old.output === 'string' ? old.output : '') + event.output : event.output ?? old.output;
      Object.assign(old, event, { text, output });
    } else {
      const item = { ...event }; turn.items.push(item);
      if (event.id) turn.ids.set(event.id, item);
    }
  }
  if (turn.items.length) result.push(turn);
  return result;
}
export function combine(old, incoming, older = false) {
  if (!old || (!older && incoming.size < old.size)) return { ...incoming };
  const rows = new Map((old.events || []).filter(e => older || Number(e.seq.split(':')[0]) < incoming.before).map(e => [e.seq, e]));
  for (const event of incoming.events || []) rows.set(event.seq, event);
  return { ...incoming, before: Math.min(old.before ?? 0, incoming.before ?? 0), size: Math.max(old.size || 0, incoming.size || 0), events: [...rows.values()].sort((a, b) => Number(a.seq.split(':')[0]) - Number(b.seq.split(':')[0]) || Number(a.seq.split(':')[1]) - Number(b.seq.split(':')[1])) };
}
export function activity(stream) {
  const items = turns(stream?.events || []).flatMap(t => t.items);
  const latest = [...items].reverse().find(e => (e.kind === 'tool' && e.title) || (e.kind === 'message' && e.text));
  return String(latest?.title || latest?.text || '').slice(0, 160);
}
export function windowTurns(groups, offset = 0, size = 80) {
  let skip = offset, remaining = size;
  return groups.map((group, index) => ({ ...group, index })).reverse().map(group => {
    const end = Math.max(0, group.items.length - skip);
    skip = Math.max(0, skip - group.items.length);
    const items = group.items.slice(Math.max(0, end - remaining), end);
    remaining -= items.length;
    return { ...group, items };
  }).reverse().filter(group => group.items.length);
}
