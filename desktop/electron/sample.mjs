// In-memory fixtures only: no demo records are written into AUTOSWARM_HOME.
// showcase adds a Codex-bound external session for the preview and --demo;
// the plain fixture (no options) stays stable for model tests.
export function createDemo({ showcase = false } = {}) {
  const data = { home: '演示空间 · 独立示例数据', exists: true, agents: {}, calls: {}, requests: {}, messages: {}, warnings: [], desk: { controller_id: 'main_demo', assistant_agent_id: 'call_meta' } };
  const start = '2026-11-01T09:02:00+00:00';
  data.observed_at = '2026-11-01T09:16:00Z';
  function call(id, name, provider, owner, state, prompt, content) {
    data.agents[id] = { agent_id: id, call_id: id, owner_id: owner, name, idle: state === 'completed' };
    data.calls[id] = { id, agent_id: id, state, directory: '', task: { call_id: id, agent_id: id, controller_id: owner, provider, prompt,
      model: provider === 'claude-code' ? 'claude-sonnet-4-5' : null, workspace: 'D:\\code\\payment-svc', created_at: start },
      handle: { phase: 'execution', started_at: start }, result: state === 'completed' ? { outcome: state, content, finished_at: '2026-11-01T09:14:00+00:00' } : null };
  }
  call('call_01', 'auth 模块迁移到 JWT', 'claude-code', 'main_demo', 'running', '把 auth 模块从 session 迁移到 JWT。请并行完成实现、测试和文档，并交叉检查安全性。');
  data.calls.call_01.task.permission = 'acceptEdits';  // Desk's permission mode for this conversation
  call('call_02', '实现 JWT 签发与校验', 'codex', 'call_01', 'running', '实现 JWT 签发与校验，保留现有权限检查。');
  call('call_03', '补齐 auth 单元测试', 'opencode', 'call_01', 'running', '为 JWT 登录流程补齐单元测试和过期 token 场景。');
  call('call_04', '迁移旧 session 数据', 'copilot', 'call_01', 'running', '制定旧 session 数据的迁移方案。');
  call('call_05', '更新 API 文档', 'dsh', 'call_01', 'completed', '同步认证接口文档与示例。', '### 认证接口文档已更新\n\n- 补充 `Authorization: Bearer <token>` 示例\n- 标注 token 过期与刷新规则\n- 登录、登出接口文案已核对\n\n文档位于 `docs/auth.md`，等待主控核对。');
  call('call_06', '登录接口压测基线', 'grok', 'call_01', 'running', '整理登录接口压测脚本，记录迁移前基线。');
  call('call_07', 'JWT 安全复核', 'cursor', 'call_02', 'running', '检查签名算法白名单、过期时间与错误处理。');
  call('call_08', '落地页改版 A/B', 'codex', 'main_demo', 'running', '比较两个落地页方案，给出改版建议。');
  call('call_09', 'Q3 销售数据清洗', 'dsh', 'main_demo', 'completed', '清洗第三季度销售数据，列出异常值和处理结果。', '## Q3 数据已整理完成\n\n已完成重复记录清理、字段统一与异常标注。\n\n| 检查项 | 结果 |\n| --- | --- |\n| 重复订单 | 已去重 |\n| 日期格式 | 已统一 |\n| 缺失金额 | 已单独标注 |\n\n异常行保留在单独的工作表中，方便人工复核。');
  call('call_meta', '私人助理', 'claude-code', 'main_demo', 'completed', '把 auth 模块迁移到 JWT，并看看落地页改版的进展。', '新的认证迁移工作已交给 **Claude Code** 主控，它会派出实现、测试、文档与安全复核的子 Agent。\n\n落地页由 **Codex** 继续处理；销售数据清洗已完成。认证迁移有两件事需要你决定，见下方。');
  for (const id of ['call_01','call_08']) { data.agents[id].owner_id = 'call_meta'; data.calls[id].task.controller_id = 'call_meta'; }
  function request(id, cid, value) { data.requests[cid + '/' + id] = { id, call_id: cid, request: value, pending: true, delegate_to_parent: true, decision: null, resolution: null }; }
  request('approval_01', 'call_03', { kind: 'approval_required', created_at: '2026-11-01T09:14:48Z', title: '安装测试依赖', user_required: true, payload: { command: 'pip install pyjwt==2.9.0', reason: '补齐 JWT 测试依赖；这会改变当前 Python 环境。' }, scopes: ['once', 'session'] });
  request('approval_02', 'call_04', { kind: 'interaction_required', created_at: '2026-11-01T09:13:12Z', response_format:'opencode-question', native_request:{questions:[{question:'旧的 session 登录态要保留多久？',options:[{label:'立即失效'},{label:'兼容 7 天'},{label:'兼容 30 天'}]}]}, title: '旧的 session 登录态要保留多久？', user_required: true, payload: { question: '立即失效会让尚未过期的用户重新登录。', options: ['立即失效', '兼容 7 天', '兼容 30 天'] } });
  data.messages.msg_01 = { id: 'msg_01', message: { from: 'call_05', to: 'call_01', content: '认证文档已更新，请在汇总时核对 token 刷新规则。', created_at: start }, delivery: { state: 'delivered' } };
  data.messages.msg_02 = {id:'msg_02',message:{from:'call_03',to:'call_02',content:'过期字段用 exp 还是 expires_at？',created_at:'2026-11-01T09:12:00Z'},delivery:{state:'delivered'}};
  data.messages.msg_03 = {id:'msg_03',message:{from:'call_02',to:'call_03',content:'使用 exp，遵循 RFC 7519。',created_at:'2026-11-01T09:12:15Z'},delivery:{state:'delivered'}};
  for (const [id,time] of Object.entries({call_02:'09:07:20',call_03:'09:07:30',call_04:'09:07:40',call_05:'09:10:20',call_06:'09:08:05',call_07:'09:11:00'})) {
    data.calls[id].task.created_at = `2026-11-01T${time}Z`;
    data.calls[id].handle.started_at = `2026-11-01T${time}Z`;
  }
  const examples = [
    {kind:'thought', text:'先核对认证入口与签名校验，再执行测试。'},
    {kind:'tool', id:'read-1', title:'读取 auth/token.py', tool:'read', status:'completed', output:'def verify_token(token): ...'},
    {kind:'tool', id:'edit-1', title:'更新 JWT 校验', tool:'edit', status:'completed', changes:[{path:'auth/token.py', diff:'- verify_signature = False\n+ verify_signature = True\n+ algorithms = ["HS256"]'}]},
    {kind:'tool', id:'test-1', title:'python -m pytest tests/test_auth.py', tool:'execute', status:'completed', output:'collected 11 items\n11 passed in 0.42s'},
    {kind:'message', text:'签名校验已完成，测试通过。'},
    {kind:'turn', status:'completed'},
    {kind:'notice', title:'provider.extension', raw:{type:'provider.extension', detail:'未知事件保留供查看'}},
    {kind:'message', text:'实现部分已完成，接着检查过期 token 和权限边界。'},
    {kind:'tool', id:'review-1', title:'检查过期 token 与权限边界', tool:'read', status:'in_progress'}
  ];
  for (const id of ['call_01','call_02','call_05']) data.calls[id].stream = {available:true,before:0,size:1000,events:examples.map((e,i)=>({...e,seq:`${i*100}:0`,raw:e.raw || {...e,example:true,timestamp:`2026-11-01T09:${String(9+Math.floor(i/2)).padStart(2,'0')}:00Z`}}))};
  data.calls.call_08.stream = {available:true,before:0,size:100,events:[{kind:'message',seq:'0:0',text:'正在比较两个落地页方案。',raw:{example:true}}]};
  if (showcase) externalCodex(data);
  return data;
}

// A Codex thread that used AutoSwarm from its own terminal: its native turns
// arrive already grouped (commentary, batched tools, final answer), the way the
// desktop reads them from the Codex projection.
function externalCodex(data) {
  const base = Date.parse('2026-11-01T10:57:00Z');
  const at = minutes => base + minutes * 60000;
  data.agents.main_codex = { agent_id: 'main_codex', parent: { host: 'codex', session_id: 'demo-codex-thread' } };
  for (const [id, provider, name, prompt, minute] of [
    ['call_10', 'claude-code', 'Terminal-Bench 4.0 轨迹核查', '核查 Terminal-Bench 4.0 的公开轨迹：每题是否有多条、是否覆盖多个模型。', 1],
    ['call_11', 'codex', 'Terminal-Bench 2.1 轨迹核查', '核查 Terminal-Bench 2.1 的公开轨迹：每题是否有多条、是否覆盖多个模型。', 1]]) {
    const created = new Date(at(minute)).toISOString();
    data.agents[id] = { agent_id: id, call_id: id, owner_id: 'main_codex', name, idle: true };
    data.calls[id] = { id, agent_id: id, state: 'completed', directory: '', task: { call_id: id, agent_id: id, provider, prompt, workspace: 'D:\\research', created_at: created, parent: { host: 'codex', session_id: 'demo-codex-thread' } },
      handle: { phase: 'execution', started_at: created }, result: { outcome: 'completed', content: `${name}完成，明细已写入 outputs/。`, finished_at: new Date(at(30)).toISOString() } };
  }
  const tool = (minute, tool, title, extra = {}) => ({ kind: 'tool', tool, title, status: 'completed', at: at(minute), id: `t${minute}-${title.length}`, ...extra });
  const note = (minute, text) => ({ kind: 'message', phase: 'commentary', text, at: at(minute), id: `n${minute}` });
  data.native_conversations = { main_codex: { title: '调研 Terminal 轨迹数据覆盖', workspace: 'D:\\research', provider: 'codex', busy: false, version: 'demo', has_more: false, turns: [{
    id: 'turn-1', status: 'completed', started_at: at(0), duration_ms: 36 * 60000 + 35000, error: null, omitted: 0,
    user: { id: 'u1', at: at(0), text: '当前 autoswarm 是否可用，用两个 agent（claude code 和 codex）分别调研下 terminal4.0 和 terminal2.1 的轨迹数据，是否每道题都有多条轨迹数据，是否有多个模型的轨迹数据。' },
    items: [
      note(0, '我会按 autoswarm 技能检查本地可用性，并分别让 Claude Code 和 Codex 核查 Terminal 4.0、Terminal 2.1 的轨迹数据，重点确认每题是否有多条轨迹、是否覆盖多个模型。'),
      tool(0, 'read', '读取 SKILL.md'), tool(0, 'execute', 'autoswarm --version; autoswarm agents --json', { output: 'autoswarm 0.4.0\nclaude-code 2.1.304 · ready\ncodex 0.164.0-alpha.11.1 · ready' }),
      note(1, '本地已检测到两个 CLI：Claude Code 2.1.304 和 Codex CLI 0.164.0-alpha.11.1。接下来让 Claude Code 查 **Terminal-Bench 4.0**，Codex 查 **2.1**，并区分公开题库、榜单成绩和可下载的完整轨迹。'),
      tool(1, 'execute', 'autoswarm run --provider claude-code --name "Terminal-Bench 4.0 轨迹核查"'), tool(1, 'execute', 'autoswarm run --provider codex --name "Terminal-Bench 2.1 轨迹核查"'),
      tool(2, 'execute', 'autoswarm wait call_10 call_11 --timeout 600', { status: 'failed', exit: 1, output: 'PermissionError: [WinError 5] 拒绝访问。' }),
      note(7, '已定位到原因：首次调用继承了离线沙箱，网络被系统阻止。现已在正常 Windows 用户上下文重试两个 agent；API 网关可以连通。'),
      tool(7, 'execute', 'autoswarm resume call_10'), tool(7, 'execute', 'autoswarm resume call_11'),
      tool(9, 'search', 'Terminal-Bench 2.1 leaderboard trials ATIF trajectory', { output: 'https://www.tbench.ai/leaderboard\nhttps://huggingface.co/datasets/…' }),
      tool(9, 'fetch', 'https://www.tbench.ai/leaderboard/terminal-bench/2.1'),
      note(12, '2.1 的核查已有明确结果：GPT-6 Astra、Fable 5、Gemini 3.1 Pro 三个官方提交都覆盖 89 道题，每题各有 5 次试验，共 1,335 条试验记录。'),
      tool(15, 'execute', 'python verify_trials.py --version 2.1 --check-files', { output: 'checked 1335 trial dirs\ntrajectory.json present: 876\nmissing or errored: 459' }),
      tool(15, 'edit', '修改 outputs/tb21-coverage.json', { changes: [{ path: 'outputs/tb21-coverage.json', diff: '+{"tasks": 89, "models": 3, "trials": 1335,\n+ "trajectory_files": 876}' }] }),
      note(27, '目前 4.0 的轨迹接口持续超时。它的官方提交索引和全题重复试验记录已核实，但我会保留“未完成轨迹正文核验”这个限制。'),
      tool(31, 'execute', 'autoswarm receive call_10 call_11 --json'), tool(33, 'execute', 'python -m json.tool outputs/tb40-evidence.json')
    ],
    final: { id: 'f1', at: at(36), text: '**autoswarm 当前可用**，本次已实际运行并回收两个 agent 的结果：Claude Code 查 4.0，Codex 查 2.1。\n\n| 版本 | 是否每题都有多条轨迹 | 是否有多个模型的轨迹 |\n| --- | --- | --- |\n| 2.1：89 题 | **已确认有。** Astra 每题 5 个轨迹文件，Fable 每题 4–5 个。 | **已确认有。** Astra、Fable、Gemini 均读到了轨迹正文样本。 |\n| 4.0：66 题 | 已确认 Astra 每题 5 次独立试验，共 330 次；完整轨迹文件的逐题覆盖尚未核实。 | 已确认 Astra、Fable 5 的官方试验索引；正文接口持续超时。 |\n\n轨迹文件存在也不代表任务成功；本次保留了失败、异常和访问未确认状态。可复查明细：`outputs/tb21-coverage.json` · `outputs/tb40-evidence.json`。' }
  }] } };
}
