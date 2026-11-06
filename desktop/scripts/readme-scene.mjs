// The scene of the README screenshots (scripts/readme-screenshots.cjs), in English and Chinese:
// Codex leads a release halfway through; six agents work on its parts. One waits for the user's
// approval, two have returned, three are running. In-memory data in the shape of a Desk snapshot.
const TEXT = {
  en: {
    request: 'Get release 2.4 of the web app ready: dark mode, end-to-end tests, the user guide, a review of the login changes, a page-load check and release notes. Split the work across agents and check their results before you report back.',
    main: 'Release 2.4',
    workers: [
      ['Implement dark mode', 'Add a dark theme that follows the system setting, with a switch in Settings.'],
      ['Refactor theme tokens', 'Move colours and spacing into theme tokens so both themes share them.'],
      ['Write end-to-end tests', 'Cover sign-in, settings and the theme switch with end-to-end tests.'],
      ['Update the user guide', 'Describe dark mode and the new Settings page in the user guide.'],
      ['Review the login changes', 'Review the session and token changes in src/auth for security problems.'],
      ['Measure page load', 'Measure page load before and after the theme refactor and report the difference.']],
    results: {
      cursor: '### Theme tokens are in place\n\n- 42 colours and 12 spacing values moved to `src/styles/tokens.css`\n- Light and dark themes now share one set of names\n- No visual change in the light theme (screenshots compared)',
      kimi: '### User guide updated\n\n- New section *Dark mode* with the system and manual settings\n- Settings page screenshots replaced\n- Changelog entry drafted for 2.4'},
    plan: 'I will split release 2.4 into six parts and give each one to the agent that suits it best.',
    progress: 'Cursor has finished the theme tokens and Kimi Code has updated the user guide; I am checking both while the other four keep working. OpenCode is waiting for your approval to install Playwright.',
    diff: 'git diff --stat main..theme-tokens',
    diffOutput: ' src/styles/tokens.css | 118 +++++++++\n src/styles/app.css    |  86 +++----\n 2 files changed, 151 insertions(+), 53 deletions(-)',
    wait: 'autoswarm wait --any',
    activity: { 'claude-code': 'Edit src/styles/theme.css', copilot: 'Read src/auth/session.ts', grok: 'npx lighthouse http://localhost:4173 --preset=desktop' },
    approval: { title: 'Install Playwright', command: 'npm install --save-dev @playwright/test', reason: 'The end-to-end tests need Playwright. This adds a development dependency to package.json.' },
    mail: ['Store the theme choice in localStorage; it does not need a cookie.', 'Done, the theme is saved in localStorage.'],
    others: [['Weekly metrics report', 'claude-code', 'D:\\reports', 'Summarise this week\'s sign-ups and retention for the team update.'],
      ['Fix the flaky login test', 'codex', 'D:\\code\\web-app', 'The login test fails about one run in ten; find out why and fix it.']],
    assistant: ['Start release 2.4 with Codex and tell me when it needs me.', 'Codex is leading **release 2.4** and has handed out six parts. One needs your approval; I will tell you when the release notes are ready.']
  },
  zh: {
    request: '准备 Web 应用的 2.4 版发布：深色模式、端到端测试、用户指南、登录改动的复核、页面加载检查和发布说明。把工作分给合适的 Agent，检查它们的结果后再告诉我。',
    main: '2.4 版发布',
    workers: [
      ['实现深色模式', '加入跟随系统设置的深色主题，并在设置里提供切换开关。'],
      ['整理主题变量', '把颜色和间距统一成主题变量，让两套主题共用。'],
      ['编写端到端测试', '为登录、设置和主题切换编写端到端测试。'],
      ['更新用户指南', '在用户指南里说明深色模式和新的设置页面。'],
      ['复核登录改动', '检查 src/auth 里会话和 token 的改动有没有安全问题。'],
      ['测量页面加载', '测量主题重构前后的页面加载时间，报告差异。']],
    results: {
      cursor: '### 主题变量已整理完成\n\n- 42 个颜色和 12 个间距值移到 `src/styles/tokens.css`\n- 浅色和深色主题共用同一套变量名\n- 浅色主题外观没有变化（已对比截图）',
      kimi: '### 用户指南已更新\n\n- 新增「深色模式」一节，说明跟随系统和手动切换\n- 替换了设置页面的截图\n- 起草了 2.4 版的更新日志'},
    plan: '我把 2.4 版发布拆成六部分，各交给最合适的 Agent。',
    progress: 'Cursor 已整理好主题变量，Kimi Code 已更新用户指南；我在检查这两份结果，另外四个仍在进行。OpenCode 在等你批准安装 Playwright。',
    diff: 'git diff --stat main..theme-tokens',
    diffOutput: ' src/styles/tokens.css | 118 +++++++++\n src/styles/app.css    |  86 +++----\n 2 files changed, 151 insertions(+), 53 deletions(-)',
    wait: 'autoswarm wait --any',
    activity: { 'claude-code': '编辑 src/styles/theme.css', copilot: '读取 src/auth/session.ts', grok: 'npx lighthouse http://localhost:4173 --preset=desktop' },
    approval: { title: '安装 Playwright', command: 'npm install --save-dev @playwright/test', reason: '端到端测试需要 Playwright，这会在 package.json 里新增一个开发依赖。' },
    mail: ['主题选择存在 localStorage 就行，不需要 cookie。', '已改好，主题保存在 localStorage。'],
    others: [['本周数据周报', 'claude-code', 'D:\\reports', '汇总本周的注册和留存数据，用于团队周会。'],
      ['修复不稳定的登录测试', 'codex', 'D:\\code\\web-app', '登录测试大约每十次失败一次，查明原因并修复。']],
    assistant: ['用 Codex 开始准备 2.4 版发布，需要我的时候告诉我。', '**2.4 版发布**由 Codex 主控，已派出六个子任务。有一项需要你批准；发布说明完成后我会告诉你。']
  }
};

export const MAIN = 'call_main';

export function readmeScene(language = 'en') {
  const text = TEXT[language] || TEXT.en;
  const workspace = 'D:\\code\\web-app', at = minute => new Date(Date.parse('2026-11-12T10:00:00Z') + minute * 60000).toISOString();
  const data = { home: 'D:\\AutoSwarm', exists: true, agents: {}, calls: {}, requests: {}, messages: {}, warnings: [],
    desk: { controller_id: 'desk_main', assistant_agent_id: 'call_assistant' }, observed_at: at(21.5) };
  const call = (id, name, provider, owner, state, prompt, minute, { content, finished, place = workspace } = {}) => {
    data.agents[id] = { agent_id: id, call_id: id, owner_id: owner, name, idle: state === 'completed' };
    data.calls[id] = { id, agent_id: id, state, directory: '', task: { call_id: id, agent_id: id, controller_id: owner, provider, prompt, workspace: place, created_at: at(minute) },
      handle: { phase: 'execution', started_at: at(minute) }, result: state === 'completed' ? { outcome: 'completed', content, finished_at: at(finished) } : null };
  };
  const stream = (id, events) => {
    data.calls[id].stream = { available: true, before: 0, size: events.length * 100, events: events.map((event, i) => ({ ...event, seq: `${i * 100}:0`, raw: { ...event, timestamp: at(2 + i) } })) };
  };
  const tool = (id, title, tool, extra = {}) => ({ kind: 'tool', id, title, tool, status: 'completed', ...extra });

  call('call_assistant', 'desk-assistant', 'claude-code', 'desk_main', 'completed', text.assistant[0], 1, { content: text.assistant[1], finished: 2.5 });
  call(MAIN, text.main, 'codex', 'desk_main', 'running', text.request, 2);
  const workers = [['call_w1', 'claude-code', 'running'], ['call_w2', 'cursor', 'completed', text.results.cursor, 11], ['call_w3', 'opencode', 'running'],
    ['call_w4', 'kimi', 'completed', text.results.kimi, 14], ['call_w5', 'copilot', 'running'], ['call_w6', 'grok', 'running']];
  workers.forEach(([id, provider, state, content, finished], i) => call(id, text.workers[i][0], provider, MAIN, state, text.workers[i][1], 4 + i * 0.25, { content, finished }));
  text.others.forEach(([name, provider, place, prompt], i) => call(`call_done${i}`, name, provider, 'desk_main', 'completed', prompt, -180 - i * 60, { content: '✓', finished: -150 - i * 60, place }));

  stream(MAIN, [
    { kind: 'message', text: text.plan },
    tool('agents', 'autoswarm agents --json', 'execute', { output: 'claude-code · ready\ncursor · ready\nopencode · ready\nkimi · ready\ncopilot · ready\ngrok · ready' }),
    ...workers.map(([id, provider], i) => tool(`run-${id}`, `autoswarm run --agent ${provider} --name "${text.workers[i][0]}" --background`, 'execute')),
    { kind: 'message', text: text.progress },
    tool('diff', text.diff, 'execute', { output: text.diffOutput }),
    tool('wait', text.wait, 'execute', { status: 'in_progress' })]);
  for (const [id, provider] of [['call_w1', 'claude-code'], ['call_w5', 'copilot'], ['call_w6', 'grok']]) {
    stream(id, [tool(`${id}-read`, provider === 'grok' ? 'npm run build' : 'git status', 'execute'), tool(`${id}-now`, text.activity[provider], provider === 'grok' ? 'execute' : provider === 'copilot' ? 'read' : 'edit', { status: 'in_progress' })]);
  }
  data.requests['call_w3/approval_1'] = { id: 'approval_1', call_id: 'call_w3', pending: true, delegate_to_parent: true, decision: null, resolution: null,
    request: { kind: 'approval_required', created_at: at(18.5), title: text.approval.title, user_required: true, payload: { command: text.approval.command, reason: text.approval.reason }, scopes: ['once', 'session'] } };
  data.messages.msg_1 = { id: 'msg_1', message: { from: 'call_w5', to: 'call_w1', content: text.mail[0], created_at: at(12) }, delivery: { state: 'delivered' } };
  data.messages.msg_2 = { id: 'msg_2', message: { from: 'call_w1', to: 'call_w5', content: text.mail[1], created_at: at(13) }, delivery: { state: 'delivered' } };
  return data;
}
