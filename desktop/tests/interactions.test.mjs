import test from 'node:test';
import assert from 'node:assert/strict';
import { encodeAnswer, questionForm, describeRequest } from '../renderer/interactions.mjs';
import { setLanguage } from '../renderer/i18n.mjs';
setLanguage('zh');  // These assertions read Desk's Chinese source text.

function request(response_format, native_request) { return { response_format, native_request }; }
function fields(values) {
  const result = new FormData();
  for (const [name, value] of Object.entries(values)) for (const item of Array.isArray(value) ? value : [value]) result.append(name, item);
  return result;
}
function form(value) { return questionForm({ id: 'approval_01', call_id: 'call_01', request: value }); }

test('Claude preserves exact question keys and explicit free-text answers', () => {
  const value = request('claude-question', { input: { questions: [{ question: '保留 "旧登录态" 多久？', options: [{ label: '7 天' }] }] } });
  assert.deepEqual(encodeAnswer(value, fields({custom0:'  兼容 30 天  '})), {answers:{'保留 "旧登录态" 多久？':'兼容 30 天'}});
});
test('CodeBuddy finds toolCall.rawInput and retains multiple selections', () => {
  const value = request('codebuddy-question', {toolCall:{rawInput:{questions:[{question:'覆盖哪些环境？',multiSelect:true,options:[{label:'开发'},{label:'测试'}]}]}}});
  assert.deepEqual(encodeAnswer(value, fields({q0:['开发','测试']})), {answers:{'覆盖哪些环境？':['开发','测试']}});
});
test('Codex uses native question IDs and nested answer arrays', () => {
  const value = request('codex-question', {questions:[{id:'scope',question:'Scope?'},{id:'reason',question:'Reason?'}]});
  assert.deepEqual(encodeAnswer(value, fields({q0:'Local',custom1:'用户说明'})), {answers:{scope:{answers:['Local']},reason:{answers:['用户说明']}}});
});
test('Cursor answers option IDs, with no invented custom-answer control', () => {
  const value = request('cursor-question', {questions:[{id:'scope',question:'Scope?',options:[{id:'local-id',label:'Local'}]}]});
  assert.match(form(value), /value="local-id"/);
  assert.doesNotMatch(form(value), /name="custom0"/);
  assert.deepEqual(encodeAnswer(value, fields({q0:'local-id'})), {outcome:'answered',answers:[{questionId:'scope',selectedOptionIds:['local-id']}]});
});
test('OpenCode preserves native question order and custom=false', () => {
  const value = request('opencode-question', {questions:[{question:'Scope?',custom:false,options:[{label:'Local'}]},{question:'Targets?',multiple:true,options:[{label:'A'},{label:'B'}]}]});
  assert.doesNotMatch(form(value), /name="custom0"/);
  assert.deepEqual(encodeAnswer(value, fields({q0:'Local',q1:['A','B']})), {answers:[['Local'],['A','B']]});
});
test('Muse maps single, multiple and text answers to distinct native fields', () => {
  const value = request('muse-question', {questions:[{id:'one',title:'One',options:[{label:'A'}]},{id:'many',title:'Many',selection:{mode:'multiple'},options:[{label:'B'},{label:'C'}]},{id:'text',title:'Text'}]});
  assert.deepEqual(encodeAnswer(value,fields({q0:'A',q1:['B','C'],custom2:'内容'})), {action:'answer',answers:[{questionId:'one',selectedLabel:'A'},{questionId:'many',selectedLabels:['B','C']},{questionId:'text',freeText:'内容'}]});
});
test('Codex asynchronous requests display the actual nested native question', () => {
  const value = request('codex-async-question', {threadId:'thread',turnId:'turn',item:{text:'Choose scope',questions:[{title:'Which scope?',options:['Local','Shared']}]}});
  assert.match(form(value), /Which scope\?/);
  assert.deepEqual(encodeAnswer(value, fields({custom0:'Local'})), {text:'Local'});
});
test('unanswered fields are rejected before consuming a native request', () => {
  const value = request('codex-question', {questions:[{id:'one',question:'One?'},{id:'two',question:'Two?'}]});
  assert.throws(()=>encodeAnswer(value,fields({q0:'A'})), /请回答每个问题/);
});
test('unsupported and malformed native contracts degrade without crashing the page', () => {
  for (const value of [request('future-protocol',{}),request('cursor-question',{questions:'unexpected'}),request('opencode-question',{questions:[null]}),request('claude-question',{input:{questions:{id:'unexpected'}}})]) {
    assert.equal(form(value),'');
  }
});
test('native text, labels and IDs are escaped in question markup', () => {
  const value = request('cursor-question', {questions:[{id:'q',question:'<script>bad()</script>',options:[{id:'x" onclick="bad()',label:'<img src=x onerror=bad()>',description:'<b>unsafe</b>'}]}]});
  const html=form(value);
  assert.doesNotMatch(html, /<script|<img|<b>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /value="x&quot; onclick=&quot;bad\(\)"/);
});

// Shapes recorded from real Claude Code 2.1 permission requests (P2 acceptance).
const workspace = 'D:\\验收 工作区';
const claude = (tool_name, input, extra = {}) => ({ kind: 'approval_required', workspace, native_request: { subtype: 'can_use_tool', tool_name, display_name: tool_name, input, ...extra } });

test('a write request names the file relative to the workspace and previews the content', () => {
  const value = describeRequest(claude('Write', { file_path: 'D:\\验收 工作区\\hello.txt', content: 'P2 验收通过\n' }, { description: 'hello.txt' }));
  assert.deepEqual([value.title, value.subject, value.preview, value.reason], ['写入文件', 'hello.txt', 'P2 验收通过', '']);
});

test('a command request shows the command and the agent’s own explanation', () => {
  const value = describeRequest(claude('Bash', { command: 'rm hello.txt', description: 'Delete hello.txt' }, { description: 'Delete hello.txt and list the directory', blocked_path: 'D:\\验收 工作区\\hello.txt' }));
  assert.deepEqual([value.title, value.command, value.subject, value.reason], ['运行命令', 'rm hello.txt', '', 'Delete hello.txt and list the directory']);
});

test('edits preview removed and added lines; unknown tools show their input', () => {
  const edit = describeRequest(claude('Edit', { file_path: 'D:\\other\\a.py', old_string: 'x = 1', new_string: 'x = 2\ny = 3' }));
  assert.equal(edit.subject, 'D:\\other\\a.py', 'paths outside the workspace stay absolute');
  assert.equal(edit.preview, '- x = 1\n+ x = 2\n+ y = 3');
  assert.equal(edit.previewKind, 'diff');
  assert.deepEqual([describeRequest(claude('WebFetch', { url: 'https://example.org' })).subject, describeRequest(claude('WebSearch', { query: 'terminal bench' })).title], ['https://example.org', '搜索网页']);
  const other = describeRequest(claude('mcp__db__query', { sql: 'select 1' }));
  assert.equal(other.title, '使用 mcp__db__query');
  assert.match(other.preview, /select 1/);
  const long = describeRequest(claude('Write', { file_path: 'D:\\验收 工作区\\big.txt', content: Array.from({ length: 40 }, (_, i) => 'line ' + i).join('\n') }));
  assert.equal(long.preview.split('\n').length, 13, 'twelve lines and an ellipsis');
  assert.equal(describeRequest({ kind: 'approval_required', title: '由 AutoSwarm 提供的标题' }).title, '由 AutoSwarm 提供的标题');
});

// Shape recorded from a real Codex 0.166 command approval (P3 acceptance).
test('a Codex command shows what runs inside the shell, not the shell executable', () => {
  const inner = "[System.IO.File]::WriteAllText((Join-Path (Get-Location) 'a.txt'), 'A')";
  const codex = { kind: 'approval_required', native_request: { method: 'item/commandExecution/requestApproval', command: `"C:\\windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "${inner}"`, commandActions: [{ type: 'unknown', command: inner }] } };
  assert.deepEqual([describeRequest(codex).title, describeRequest(codex).command], ['运行命令 · PowerShell', inner]);
  const bare = describeRequest({ kind: 'approval_required', native_request: { command: '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoProfile -Command \'Get-ChildItem\'' } });
  assert.deepEqual([bare.title, bare.command], ['运行命令 · PowerShell', 'Get-ChildItem'], 'without commandActions the wrapper is removed');
  assert.equal(describeRequest({ kind: 'approval_required', native_request: { command: 'npm test' } }).title, '运行命令');
});

// Shape recorded from a real Cursor Agent 2026.10 ACP permission request (P3 acceptance).
test('a Cursor command shows the command, not its backticked title, and why it asks', () => {
  const cursor = { kind: 'approval_required', native_request: { sessionId: 's', toolCall: { toolCallId: 'call-1', title: '`dir`', kind: 'execute', status: 'pending', content: [{ type: 'content', content: { type: 'text', text: 'Not in allowlist: dir' } }] }, options: [{ optionId: 'allow-once', kind: 'allow_once' }] } };
  const value = describeRequest(cursor);
  assert.deepEqual([value.title, value.command, value.reason], ['运行命令', 'dir', '不在允许列表中，需要你确认']);
  const described = describeRequest({ kind: 'approval_required', native_request: { toolCall: { title: 'Run the test suite', kind: 'execute', rawInput: { command: 'npm test' } } } });
  assert.deepEqual([described.title, described.command], ['运行命令', 'npm test']);
  assert.equal(describeRequest({ kind: 'approval_required', native_request: { toolCall: { title: 'Fetch docs', kind: 'fetch' } } }).title, 'Fetch docs');
});

test('ACP edits show the file and only the changed lines', () => {
  const acp = (kind, content, extra = {}) => ({ kind: 'approval_required', workspace, native_request: { toolCall: { title: 'Edit', kind, content, ...extra } } });
  const edit = describeRequest(acp('edit', [{ type: 'diff', path: 'D:\\验收 工作区\\src\\a.py', oldText: 'a = 1\nb = 2\nc = 3\n', newText: 'a = 1\nb = 20\nc = 3\n' }]));
  assert.deepEqual([edit.title, edit.subject, edit.preview, edit.previewKind], ['修改文件', 'src\\a.py', '- b = 2\n+ b = 20', 'diff']);
  const created = describeRequest(acp('edit', [{ type: 'diff', path: 'D:\\验收 工作区\\new.txt', oldText: null, newText: 'hello\n' }]));
  assert.deepEqual([created.title, created.subject, created.preview], ['写入文件', 'new.txt', 'hello']);
  const removed = describeRequest(acp('delete', [], { locations: [{ path: 'D:\\验收 工作区\\old.txt' }] }));
  assert.deepEqual([removed.title, removed.subject, removed.preview], ['删除文件', 'old.txt', '']);
});

test('approval cards say what Kimi and DSH want to do (requests recorded 2026-11-05)', () => {
  // Kimi names the tool only and says the rest in text.
  const kimi = text => describeRequest({ provider: 'kimi', workspace: 'D:/work', native_request: { toolCall: { toolCallId: 't', title: text[0], content: [{ type: 'content', content: { type: 'text', text: text[1] } }] } } });
  const write = kimi(['Write', 'Requesting approval to Writing D:/work/notes/task.txt']);
  assert.deepEqual([write.title, write.subject, write.reason], ['写入文件', 'notes/task.txt', '']);
  const run = kimi(['Bash', 'Requesting approval to Running: "python -m autoswarm wait --any call_x"']);
  assert.deepEqual([run.title, run.command, run.reason], ['运行命令', 'python -m autoswarm wait --any call_x', '']);
  // DSH names its tool call by id; AutoSwarm fills in what the call said about itself.
  const dsh = describeRequest({ provider: 'dsh', native_request: { toolCall: { toolCallId: 'pwsh_4', title: 'pwsh', kind: 'other', rawInput: { command: 'git status', description: 'Check the working tree' } } } });
  assert.deepEqual([dsh.title, dsh.command, dsh.reason], ['运行命令', 'git status', 'Check the working tree']);
});
