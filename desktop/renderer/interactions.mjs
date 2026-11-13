import { t } from './i18n.mjs';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function contract(request) {
  const kind=request.response_format, native=request.native_request || {};
  let questions=native.questions || [];
  if (['claude-question','codebuddy-question'].includes(kind)) questions=(native.input || native.toolCall?.rawInput || native).questions || [];
  if (kind==='codex-async-question') questions=Array.isArray(native.item?.questions) && native.item.questions.length ? native.item.questions.map(q=>typeof q==='string'?{question:q}:q) : [{question:native.item?.text || native.question || native.message || t('请输入你的回答')}];
  if (!['claude-question','codebuddy-question','codex-question','opencode-question','cursor-question','muse-question','codex-async-question'].includes(kind)) return null;
  if (!Array.isArray(questions) || !questions.length || !questions.every(q=>q && typeof q==='object' && !Array.isArray(q))) return null;
  return {kind,questions};
}
export function questionForm(entry, heading = '') {
  const value=contract(entry.request); if (!value) return '';
  return `<form class="native-question-form" data-question-call="${esc(entry.call_id)}" data-question-id="${esc(entry.id)}">${value.questions.map((q,i)=>{
    const multi=!!(q.multiSelect || q.multiple || q.allowMultiple || q.selection?.mode==='multiple');
    const options=Array.isArray(q.options)?q.options.map(o=>typeof o==='string'?{label:o}:o).filter(o=>o&&typeof o==='object'):[], custom=value.kind!=='cursor-question' && q.custom!==false;
    const legend=q.question || q.prompt || q.header || q.title || t('请选择');
    return `<fieldset><legend class="${value.questions.length===1 && legend===heading?'sr-only':''}">${esc(legend)}</legend>${options.length?'<div class="question-options">':''}${options.map(o=>`<label class="question-option"><input type="${multi?'checkbox':'radio'}" name="q${i}" value="${esc(value.kind==='cursor-question'?o.id:o.label || o.title || o.value)}"><span>${esc(o.label || o.title || o.value)}${o.description?`<small>${esc(o.description)}</small>`:''}</span></label>`).join('')}${options.length?'</div>':''}${custom?`<textarea name="custom${i}" rows="1" placeholder="${options.length?t('或者直接写你的要求…'):t('填写你的回答…')}" aria-label="${t('填写回答')}"></textarea>`:''}</fieldset>`;
  }).join('')}<button type="submit" class="primary">${t('提交回答')}</button></form>`;
}
export function encodeAnswer(request, form) {
  const {kind,questions}=contract(request) || {};
  if (!kind) throw Error(t('此问题需要在原会话中处理'));
  const selections=questions.map((q,i)=>{const custom=String(form.get(`custom${i}`)||'').trim();const values=form.getAll(`q${i}`).map(String); const optional=kind==='muse-question' && q.selection?.mode==='multiple' && (q.selection.minSelections || 0)===0; if(!values.length&&!custom&&!optional)throw Error(t('请回答每个问题后再提交'));return {values:custom?[custom]:values,custom};});
  if(kind==='codex-async-question')return {text:selections.length===1?selections[0].values[0]:questions.map((q,i)=>(q.question || q.title || q.prompt || t('问题 {number}', { number: i+1 }))+': '+selections[i].values.join(', ')).join('\n')};
  if(['claude-question','codebuddy-question'].includes(kind))return {answers:Object.fromEntries(questions.map((q,i)=>[q.question,q.multiSelect?selections[i].values:selections[i].values[0]]))};
  if(kind==='codex-question')return {answers:Object.fromEntries(questions.map((q,i)=>[q.id,{answers:selections[i].values}]))};
  if(kind==='opencode-question')return {answers:selections.map(s=>s.values)};
  if(kind==='cursor-question')return {outcome:'answered',answers:questions.map((q,i)=>({questionId:q.id,selectedOptionIds:selections[i].values}))};
  return {action:'answer',answers:questions.map((q,i)=>({questionId:q.id,...(selections[i].custom?{freeText:selections[i].custom}:q.selection?.mode==='multiple'?{selectedLabels:selections[i].values}:{selectedLabel:selections[i].values[0]})}))};
}

/**
 * What a native permission request would do, in the user's terms: the tool
 * action, its subject (file/URL/query) relative to the workspace, the command,
 * and a short preview of written content or edits. Wording AutoSwarm already
 * attached (title/description) wins; unknown tools show their input.
 */
export function describeRequest(req = {}) {
  const n = req.native_request || {}, input = n.input || n.tool_input || n.toolCall?.rawInput || {};
  const tool = String(n.display_name || n.tool_name || '');
  const raw = n.command || input.command || n.tool_input?.command || req.payload?.command;
  let command = raw ? (typeof raw === 'string' ? raw : JSON.stringify(raw)) : '', shell = '';
  // ACP agents (Cursor, Copilot, CodeBuddy…) describe a request as a toolCall: a kind,
  // a title, and content blocks. Cursor's execute title is the command in backticks.
  const acp = n.toolCall && typeof n.toolCall === 'object' ? n.toolCall : null;
  const acpTitle = typeof acp?.title === 'string' ? acp.title.trim() : '';
  const acpContent = Array.isArray(acp?.content) ? acp.content.filter(c => c && typeof c === 'object') : [];
  if (!command && acp?.kind === 'execute') command = (acpTitle.match(/^`([^`][\s\S]*)`$/) || [])[1] || '';
  // Kimi names the tool only ("Bash", "Write") and says the rest in text: "Requesting approval to Running: …".
  const asked = acpContent.map(c => c?.content?.text).filter(v => typeof v === 'string').join('\n');
  const running = /^(?:bash|shell|terminal|pwsh|powershell|cmd)$/i.test(acpTitle) || acp?.kind === 'execute' ? asked.match(/\bRunning:\s*([\s\S]+)$/) : null;
  if (!command && running) command = running[1].trim().replace(/^"([\s\S]*)"$/, '$1');
  const writing = !command && /^(?:write|edit)$/i.test(acpTitle) ? asked.match(/\b(?:Writing|Editing)\s+(\S[\s\S]*)$/) : null;
  // Codex runs every command through a shell; show what runs inside it, as Codex itself does.
  const shells = { powershell: 'PowerShell', pwsh: 'PowerShell', cmd: 'cmd', bash: 'bash', sh: 'sh', zsh: 'zsh' };
  const wrapper = command.match(/^\s*(?:"(?:[^"]*[\\/])?|(?:[^"\s]*[\\/])?)(powershell|pwsh|cmd|bash|sh|zsh)(?:\.exe)?"?\s+(?:-NoProfile\s+|-NoLogo\s+|-lc?\s+)*(?:-Command|-c|\/c|-lc)?\s*([\s\S]+)$/i);
  const actions = Array.isArray(n.commandActions) ? n.commandActions.map(a => a?.command).filter(c => typeof c === 'string' && c.trim()) : [];
  if (wrapper) shell = shells[wrapper[1].toLowerCase()];
  if (actions.length) command = actions.join('\n');
  else if (wrapper) command = wrapper[2].trim().replace(/^(['"])([\s\S]*)\1$/, '$2');
  const clip = (text, lines = 12, chars = 1600) => {
    const all = String(text ?? ''), parts = all.split('\n');
    let out = parts.slice(0, lines).join('\n');
    if (out.length > chars) out = out.slice(0, chars);
    return out.length < all.replace(/\s+$/, '').length ? out.replace(/\s+$/, '') + '\n…' : out.replace(/\s+$/, '');
  };
  const strip = value => String(value || '').replace(/^\\\\\?\\/, '');
  const relative = file => {
    const f = strip(file), w = strip(req.workspace).replace(/[\\/]+$/, '');
    const norm = value => value.replace(/\\/g, '/').toLowerCase();
    return w && norm(f).startsWith(norm(w) + '/') ? f.slice(w.length + 1) : f;
  };
  const file = input.file_path || input.notebook_path || input.path || writing?.[1]?.trim() || '';
  const own = req.title || req.description || req.summary;
  const lines = (value, mark) => String(value ?? '').split('\n').filter(line => line.trim()).map(line => mark + ' ' + line);
  // Whole-file diffs (ACP oldText/newText): show only the lines that changed.
  const changed = (before, after) => {
    const a = String(before ?? '').split('\n'), b = String(after ?? '').split('\n');
    let head = 0, tail = 0;
    while (head < a.length && head < b.length && a[head] === b[head]) head++;
    while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
    return [...lines(a.slice(head, a.length - tail).join('\n'), '-'), ...lines(b.slice(head, b.length - tail).join('\n'), '+')].join('\n');
  };
  let title, subject = '', preview = '', previewKind = 'text';
  if (req.kind === 'interaction_required') title = own || t('Agent 需要你的回答');
  else if (own) title = own;
  else if (command) title = shell ? t('运行命令 · {shell}', { shell }) : t('运行命令');
  else if (/^Write$/i.test(tool)) { title = t('写入文件'); subject = relative(file); preview = clip(input.content); }
  else if (/^(Edit|MultiEdit|NotebookEdit)$/i.test(tool)) {
    title = t('修改文件'); subject = relative(file); previewKind = 'diff';
    const edits = Array.isArray(input.edits) ? input.edits : [{ old_string: input.old_string, new_string: input.new_string ?? input.new_source }];
    preview = clip(edits.flatMap(e => [...lines(e.old_string, '-'), ...lines(e.new_string, '+')]).join('\n'), 16);
  }
  else if (writing) { title = /^edit$/i.test(acpTitle) ? t('修改文件') : t('写入文件'); subject = relative(file); }
  else if (acp && ['edit', 'delete'].includes(acp.kind)) {
    const diff = acpContent.find(c => c.type === 'diff');
    const where = diff?.path || acp.locations?.find?.(l => l?.path)?.path || file;
    title = acp.kind === 'delete' ? t('删除文件') : diff && diff.oldText == null ? t('写入文件') : t('修改文件');
    subject = where ? relative(where) : acpTitle;
    if (diff?.oldText == null) preview = diff ? clip(diff.newText) : '';
    else { previewKind = 'diff'; preview = clip(changed(diff.oldText, diff.newText), 16); }
  }
  else if (/^WebFetch$/i.test(tool)) { title = t('访问网页'); subject = input.url || ''; }
  else if (/^WebSearch$/i.test(tool)) { title = t('搜索网页'); subject = input.query || ''; }
  else if (acpTitle) title = acpTitle;
  else if (tool) { title = t('使用 {tool}', { tool }); subject = file ? relative(file) : ''; const rest = Object.keys(input).length ? JSON.stringify(input, null, 2) : ''; preview = rest ? clip(rest) : ''; previewKind = 'json'; }
  else title = t('Agent 请求执行许可');
  // ACP text content says why the agent asks, e.g. Cursor's "Not in allowlist: dir".
  const said = acpContent.filter(c => c.type === 'content' && c.content?.type === 'text' && typeof c.content.text === 'string')
    .map(c => c.content.text.trim()).filter(Boolean).join('\n').replace(/^Not in allowlist:[\s\S]*$/i, t('不在允许列表中，需要你确认'));
  // Why the agent asks: its own words (Claude Code's description, the tool's rawInput.description) or ACP text.
  const note = typeof n.description === 'string' ? n.description.trim() : typeof input.description === 'string' && input.description.trim() ? input.description.trim() : writing || running ? '' : said;
  const reason = req.payload?.reason || req.reason || (req.description && req.description !== title ? req.description : '')
    || (typeof req.payload?.question === 'string' && req.payload.question !== title ? req.payload.question : '')
    || (note && note !== title && !(subject && subject.replace(/\\/g, '/').endsWith(note)) ? note : '');
  return { title, subject, command, shell, preview, previewKind, reason };
}
