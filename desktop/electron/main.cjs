const { app, BrowserWindow, Menu, ipcMain, dialog, shell, nativeTheme, clipboard, net, session: electronSession } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');
const { pathToFileURL, fileURLToPath } = require('node:url');

const { createShell } = require('./shell.cjs');

if (process.env.AUTOSWARM_DESK_USER_DATA) app.setPath('userData', process.env.AUTOSWARM_DESK_USER_DATA);
// One Desk per records profile: a second launch (e.g. from the Start menu while
// it sits in the tray) brings the running window forward instead.
if (!app.requestSingleInstanceLock()) { app.quit(); return; }
app.on('second-instance', () => desktop?.showMain());
if (process.platform === 'win32') app.setAppUserModelId(app.isPackaged ? 'org.autoswarm.desk' : process.execPath);
// macOS: clicking the Dock icon while the window sits in the menu bar brings it back.
if (process.platform === 'darwin') app.on('activate', () => desktop?.showMain());

let win, desktop, watcher, probe, generation = 0, frame, preferences = {}, demo = process.argv.includes('--demo');
// Started at login: tray only. macOS starts login items without arguments, so it says so itself (see whenReady).
let background = process.argv.includes('--background');
// captureAll: also keep a copy of Claude Code / Codex / Cursor process events (they are read from the agents' own records).
const i18n = require('./i18n.cjs');
const userEnv = require('./user-env.cjs');
const updates = require('./updates.cjs');
let updater;
// Page zoom of the main window (Ctrl+= / Ctrl+- / Ctrl+0); the tray popup is another page and keeps 100%.
const ZOOMS = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
const SHELL_PREFERENCES = { notify: true, notifyDone: true, closeToTray: true, openAtLogin: false, wakeAssistant: false, captureAll: false };
let preferencesPath;
let runtime;
let lastSnapshot, controlHome, controlPromise, controlError = '', sending = false;
let flushing = false;
// The agents Desk knows: the same list the window shows (renderer/agents.json).
const AGENT_NAMES = require('../renderer/agents.json').names, PROVIDERS = Object.keys(AGENT_NAMES);
const MODEL_SOURCE_NAMES = { chatgpt: 'ChatGPT', claude: 'Claude', deepseek: 'DeepSeek', 'provider:opencode': 'OpenCode 模型服务' };
function modelSourceKey(agent, id, localModels = preferences.detectedLocalModels || []) {
  const value = String(id || '').trim();
  if (!value) return '';
  const slash = value.indexOf('/');
  const namespace = slash > 0 ? value.slice(0, slash).toLowerCase() : '';
  const model = slash > 0 ? value.slice(slash + 1) : value;
  const local = (Array.isArray(localModels) ? localModels : []).find(item => String(item).toLowerCase() === model.toLowerCase()
    || (model.toLowerCase().endsWith(':latest') && String(item).toLowerCase() === model.slice(0, -7).toLowerCase()));
  if (local && (!namespace || namespace.startsWith('ollama'))) return 'local:' + String(local).toLowerCase();
  if (namespace.startsWith('ollama')) return 'local:' + model.toLowerCase();
  if (/^deepseek(?:-|$)/i.test(model)) return 'deepseek';
  if (/^claude-/i.test(model)) return 'claude';
  if (/^(gpt-|o[134](?:-|$))/i.test(model)) return 'chatgpt';
  if (namespace === 'openai' || (agent === 'codex' && /^(gpt-|o[134](?:-|$))/i.test(value))) return 'chatgpt';
  if (namespace === 'anthropic' || namespace === 'claude' || (agent === 'claude-code' && /^claude-/i.test(value))) return 'claude';
  if (namespace === 'deepseek' || /^deepseek[-/]/i.test(value)) return 'deepseek';
  if (namespace) return 'provider:' + namespace;
  return 'agent:' + agent;
}
function modelSourceLabel(key, source) {
  if (key.startsWith('local:')) {
    const model = source.models?.[0] || key.slice(6);
    const where = source.installed ? '（本机）' : '（Ollama）';
    return /^deepseek/i.test(model) ? `DeepSeek · ${model}${where}` : `${model}${source.installed ? '（Ollama 本机模型）' : '（Ollama 模型）'}`;
  }
  if (MODEL_SOURCE_NAMES[key]) return MODEL_SOURCE_NAMES[key];
  if (key.startsWith('provider:')) {
    const value = key.slice(9);
    return value ? value[0].toUpperCase() + value.slice(1) : value;
  }
  return key.slice(6);
}
function modelSourceCatalog(localModels) {
  const groups = new Map();
  const add = (key, model, agent, installed = false) => {
    if (!key) return;
    const group = groups.get(key) || { key, models: [], agents: [], installed: false };
    if (model && !group.models.includes(model)) group.models.push(model);
    if (agent && !group.agents.includes(agent)) group.agents.push(agent);
    group.installed ||= installed;
    groups.set(key, group);
  };
  for (const model of localModels || []) add('local:' + String(model).toLowerCase(), model, '', true);
  for (const [agent, entry] of Object.entries(preferences.catalogs || {})) {
    for (const model of entry?.value?.models || []) {
      const display = String(model.id || '').split('/').at(-1);
      add(modelSourceKey(agent, model.id, localModels), display, agent);
    }
  }
  const disabled = new Set(Array.isArray(preferences.disabledModelSources) ? preferences.disabledModelSources : []);
  return [...groups.values()].map(group => ({ ...group, name: modelSourceLabel(group.key, group), enabled: !disabled.has(group.key) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
function assertModelSourceEnabled(agent, model) {
  const catalog = preferences.catalogs?.[agent]?.value;
  const id = model || catalog?.models?.find(item => item.default)?.id || '';
  const key = modelSourceKey(agent, id);
  if (key && (Array.isArray(preferences.disabledModelSources) ? preferences.disabledModelSources : []).includes(key)) {
    const source = modelSourceLabel(key, { models: [id.includes('/') ? id.slice(id.indexOf('/') + 1) : id] });
    throw Error(`${source} 已在 AutoSwarm Desk 设置中关闭。请在“模型与服务”中重新开启，或为 ${AGENT_NAMES[agent] || agent} 选择其他模型。`);
  }
}
function visibleModelCatalog(agent, catalog) {
  const disabled = new Set(Array.isArray(preferences.disabledModelSources) ? preferences.disabledModelSources : []);
  return { ...catalog, models: (catalog.models || []).filter(model => !disabled.has(modelSourceKey(agent, model.id))) };
}
function publishVisibleModelCatalogs() {
  if (!win || win.isDestroyed()) return;
  for (const [provider, entry] of Object.entries(preferences.catalogs || {})) {
    if (entry?.value) win.webContents.send('desk:catalog', { provider, value: visibleModelCatalog(provider, { ...entry.value, read_at: entry.at }) });
  }
}
// Installed-CLI check per records home (executable --version only, no model calls).
const availability = new Map();
const inputPump = setInterval(async () => {
  if (flushing || demo || !controlHome || !runtime) return;
  const queued = Object.values(frame?.data?.messages || {}).some(m => m.message.kind === 'user_message' && !frame.data.agents[m.message.to]?.call_id && (!m.delivery || m.delivery.state === 'queued'));
  if (!queued) return;
  flushing = true;
  try { await cli(['desk','flush'], '', controlHome, settingsFor(controlHome).binding?.controller_id); }
  catch { /* Durable input remains in the selected home's records. */ }
  finally { flushing = false; }
}, 3000);
inputPump.unref();
const readers = new Set();
const cliHome = process.argv.find(a => a.startsWith('--home='))?.slice(7);
// Desk's own permission mode per agent (never the agent's config). AutoSwarm fills
// in each agent's default for the rest and validates the modes.
const permissionArgs = () => ['--permissions', JSON.stringify(preferences.permissions || {})];
// attention.mjs and rules.mjs reuse renderer modules, which read renderer/i18n.mjs (a separate copy).
const syncLanguage = () => import('../renderer/i18n.mjs').then(module => module.setLanguage(i18n.language()), () => {});
const zoomFactor = () => ZOOMS.includes(preferences.zoom) ? preferences.zoom : 1;
function setZoom(step) {
  const index = ZOOMS.indexOf(zoomFactor());
  preferences.zoom = step === 0 ? 1 : ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, index + step))]; save();
  win?.webContents.setZoomFactor(preferences.zoom); refresh();
  return preferences.zoom;
}
function save() {
  fs.mkdirSync(path.dirname(preferencesPath), { recursive: true });
  fs.writeFileSync(preferencesPath, JSON.stringify(preferences, null, 2));
}
const isAssistant = agent => typeof agent?.name === 'string' && (agent.name === 'desk-assistant' || agent.name.startsWith('desk-assistant.'));
function publish(value) {
  if (value.event === 'snapshot') lastSnapshot = value;
  const settings = preferences.desks?.[value.data?.home || controlHome] || {};
  if (!demo && value.data && settings.binding?.controller_id) {
    // One assistant conversation per agent (desk-assistant, desk-assistant.<agent>); the one shown is the chosen agent's.
    const id = settings.binding.controller_id;
    const assistants = Object.values(value.data.agents).filter(a => a.owner_id === id && isAssistant(a))
      .map(a => ({ agent: a, call: value.data.calls[a.call_id] })).filter(({ agent, call }) => call?.agent_id === agent.agent_id && call.task.controller_id === id);
    let chosen = assistants.find(({ call }) => call.task.provider === (settings.provider || 'claude-code'));
    // Until the user picks an agent, the assistant conversation used most recently is the one shown.
    if (!chosen && !settings.providerChosen && assistants.length) {
      chosen = assistants.reduce((a, b) => (b.call.task.created_at || '') > (a.call.task.created_at || '') ? b : a);
      settings.provider = chosen.call.task.provider;
    }
    settings.binding = { ...settings.binding, assistant_agent_id: chosen?.agent.agent_id || null, assistant_call_id: chosen?.call.id || null,
      assistants: assistants.map(({ agent, call }) => ({ agent_id: agent.agent_id, provider: call.task.provider, workspace: call.task.workspace, title: agent.title || '' })) };
    if (chosen) settings.workspace = chosen.call.task.workspace;
  }
  if (!demo && value.data) value = { ...value, data: { ...value.data, desk: settings.binding } };
  const session = settings.session || {};
  const sessionDefaults = { provider: session.provider || settings.provider || 'claude-code', model: session.model || '', effort: session.effort || '', workspace: session.workspace || settings.workspace || '' };
  const shellPreferences = Object.fromEntries(Object.entries(SHELL_PREFERENCES).map(([key, fallback]) => [key, preferences[key] ?? fallback]));
  frame = { ...value, demo, theme: preferences.theme || 'system', python: preferences.python || i18n.t('自动检测'), assistantSettings: { provider: 'claude-code', ...settings, ready: !!controlHome && controlHome === value.data?.home && !controlError, busy: sending, error: controlError }, sessionDefaults, available: availability.get(value.data?.home) || null, skills: skillState.get(value.data?.home) || null, permissions: preferences.permissions || {}, language: i18n.language(), zoom: zoomFactor(), shell: { ...shellPreferences, packaged: app.isPackaged }, update: updater?.state() || null,
    rules: { path: preferencesPath ? rulesPath() : '', exists: ruleSet.exists, active: ruleSet.rules.length, disabled: ruleSet.disabled, errors: ruleSet.errors, log: ruleLog.slice(0, 12) } };
  if (win && !win.isDestroyed()) win.webContents.send('desk:frame', frame);
  desktop?.update(frame);
  pumpWake();
}
/**
 * The AutoSwarm Skill for every agent, installed with the standard setup when missing or broken:
 * on launch for Claude Code, Codex and Cursor, for any other agent the first time it is picked as a
 * main agent. Copies that belong to this record directory are brought up to date on launch.
 */
const SKILL_HOSTS = PROVIDERS, LAUNCH_INSTALL = new Set(['claude-code', 'codex', 'cursor']);
const skillState = new Map();  // home -> { hosts, installing, errors }
function setSkills(home, patch) { skillState.set(home, { ...skillState.get(home), ...patch }); if (home === controlHome) refresh(); }
const refreshSkills = home => cli(['skills'], '', home).then(value => setSkills(home, { hosts: value.hosts }));
// One setup at a time: agents share skill folders (~/.agents/skills), and the switch shows one install.
let skillQueue = Promise.resolve();
function ensureSkills(home, only) {
  const run = skillQueue.then(() => runSkills(home, only));
  skillQueue = run.catch(() => {});
  return run;
}
async function runSkills(home, only) {
  const errors = { ...(skillState.get(home)?.errors || {}) };
  await refreshSkills(home);
  for (const host of only ? [only] : SKILL_HOSTS) {
    // A Claude Code or Codex copy installed just before may already serve Cursor.
    const known = skillState.get(home)?.hosts?.[host];
    if (!known) continue;  // An older AutoSwarm that does not know this agent.
    // A working Skill whose own copy of AutoSwarm is older is brought up to date; its launcher stays as installed.
    if (known.state === 'ready' && known.outdated) { await cli(['skills', '--host', host, '--refresh'], '', home).catch(() => {}); await refreshSkills(home); continue; }
    if (known.state === 'ready' || availability.get(home)?.[host]?.available !== true || (!only && !LAUNCH_INSTALL.has(host))) continue;
    delete errors[host];
    setSkills(home, { installing: host, errors });
    try {
      await cli(['setup', '--host', host, '--no-agents'], '', home);
      const checked = (await cli(['skills', '--host', host, '--verify'], '', home)).hosts[host];
      if (checked.state !== 'ready') errors[host] = { message: i18n.t('安装后检查没有通过'), detail: (checked.problems || []).join('\n') };
    } catch (error) { errors[host] = { message: error.message, detail: [error.diagnostic, error.output].filter(Boolean).join('\n').slice(-2000) }; }
    await refreshSkills(home);
  }
  setSkills(home, { installing: '', errors });
}
function installPrompt(host, failure) {
  const { t } = i18n;
  return [t('请帮我为 {agent} 安装 AutoSwarm 技能，AutoSwarm Desk 自动安装没有成功。', { agent: AGENT_NAMES[host] }),
    ...(failure ? ['', t('错误：{message}', { message: failure.message }), ...(failure.detail ? ['```', failure.detail, '```'] : [])] : []), '',
    t('用本会话「AutoSwarm communication」里的 CLI argv 运行 setup --host {host}，找出并解决失败原因，再运行 skills --host {host} --verify，直到 state 是 ready。', { host }),
    t('只改动和 AutoSwarm 技能有关的文件；需要安装软件，或修改登录、代理、其他 Agent 的配置时，先问我。最后用两三句话告诉我结果。')].join('\n');
}
/** Tray/notification/login preferences; the login item is the OS's own record. */
function setPreference(key, value) {
  if (!(key in SHELL_PREFERENCES) && key !== 'trayHintShown') throw Error(i18n.t('未知的设置'));
  if (typeof value !== 'boolean') throw Error(i18n.t('设置值无效'));
  preferences[key] = value; save();
  if (key === 'openAtLogin') applyLoginItem();
  if (key === 'captureAll' && controlHome && !demo) cli(['config', '--capture-events', value ? 'on' : 'auto'], '', controlHome).catch(error => console.error('Capture setting failed:', error.message));
  refresh();
}
/**
 * The ZIP build has no installer shortcut, so Windows would title toasts with
 * the raw app ID. This per-user key gives them the app's name and icon.
 */
function registerToastIdentity() {
  if (process.platform !== 'win32' || !app.isPackaged || process.env.AUTOSWARM_DESK_USER_DATA) return;
  const icon = path.join(app.getPath('userData'), 'toast-icon.png');
  try { if (!fs.existsSync(icon)) fs.copyFileSync(path.join(__dirname, '../assets/logo.png'), icon); } catch { return; }
  const key = 'HKCU\\Software\\Classes\\AppUserModelId\\org.autoswarm.desk';
  for (const [name, value] of [['DisplayName', 'AutoSwarm Desk'], ['IconUri', icon]]) {
    spawn('reg', ['add', key, '/v', name, '/t', 'REG_SZ', '/d', value, '/f'], { windowsHide: true, stdio: 'ignore' }).on('error', () => {});
  }
}
function applyLoginItem() {
  // Started at login: tray only, the window opens from the tray or a notification.
  const args = [...(app.isPackaged ? [] : [app.getAppPath()]), '--background'];
  app.setLoginItemSettings({ openAtLogin: !!preferences.openAtLogin, path: process.execPath, args });
}
// Optional trigger (off by default, each wake-up spends a model call): when
// sessions the assistant handed out have all returned, ask it to collect them.
// Trigger rules that hand something to the assistant share the same queue, so
// whatever piled up while it was busy arrives as one turn.
const wakeQueue = new Map(), ruleQueue = [];
function queueWake(sessions) {
  if (!preferences.wakeAssistant || demo) return;
  for (const session of sessions) wakeQueue.set(session.id, session.name);
  pumpWake();
}
async function pumpWake() {
  if ((!wakeQueue.size && !ruleQueue.length) || sending || demo || !controlHome || controlError) return;
  const assistant = desktop?.summary?.assistant;
  if (assistant?.state === 'running') return; // Retried on the next frame.
  // Waking needs an assistant conversation; a rule may also start the first one.
  if (!assistant) wakeQueue.clear();
  if (!wakeQueue.size && !ruleQueue.length) return;
  const names = [...wakeQueue.values()]; wakeQueue.clear();
  const rules = ruleQueue.splice(0);
  const list = names.map(name => i18n.t('「{name}」', { name })).join(i18n.t('、'));
  const parts = [...(names.length ? [WAKE_PREFIX + i18n.t('你交出的会话已全部返回：{names}。请用 receive 收取结果，用几句话告诉我结论，并列出需要我决定的事。', { names: list })] : []), ...rules.map(item => item.text)];
  try { await sendAssistant(parts.join('\n\n')); rules.forEach(item => logRule(item.entry, 'sent')); }
  catch (error) {
    console.error('Assistant hand-off failed:', error.message);
    rules.forEach(item => logRule(item.entry, 'failed', error.message));
  }
}
const WAKE_PREFIX = '[Desk 自动唤醒] ';

// Trigger rules (P4): triggers.json in Desk's data folder, reloaded on save.
let ruleSet = { rules: [], errors: [], disabled: 0, exists: false };
const ruleLog = [];
const rulesPath = () => path.join(app.getPath('userData'), 'triggers.json');
async function loadRules() {
  const { parseRules } = await import('./rules.mjs');
  let source = null, problem = '';
  try { source = fs.readFileSync(rulesPath(), 'utf8'); }
  catch (error) { if (error.code !== 'ENOENT') problem = i18n.t('无法读取规则文件：{message}', { message: error.message }); }
  ruleSet = source !== null ? { ...parseRules(source), exists: true } : { rules: [], errors: problem ? [problem] : [], disabled: 0, exists: !!problem };
  refresh();
}
/** One line per rule firing, newest first, for the settings page. */
function logRule(entry, status, error = '') {
  Object.assign(entry, { status, ...(error ? { error } : {}) });
  if (!ruleLog.includes(entry)) { ruleLog.unshift(entry); ruleLog.length = Math.min(ruleLog.length, 30); }
  refresh();
}
function runRule(action, status) {
  const entry = { at: new Date().toISOString(), rule: action.rule, do: action.do, event: action.ctx.event, session: action.ctx.session || action.ctx.title || '' };
  if (status !== 'queued') return logRule(entry, status);
  if (action.do === 'assistant') {
    if (demo || !controlHome || controlError || !settingsFor(controlHome).workspace) return logRule(entry, 'failed', i18n.t('私人助理还没有设置工作目录'));
    ruleQueue.push({ text: action.text, entry }); logRule(entry, 'queued'); pumpWake();
  } else if (action.do === 'message') {
    const target = action.ctx.sessionId;
    if (!frame?.data?.agents?.[target]) return logRule(entry, 'failed', i18n.t('找不到这个会话的主 Agent'));
    logRule(entry, 'queued');
    cli(['desk','message',target,'--mode','after_turn','--input','-'], action.text, controlHome, settingsFor(controlHome).binding?.controller_id)
      .then(() => logRule(entry, 'sent'), error => logRule(entry, 'failed', error.message));
  }
}

async function sendAssistant(text) {
  if (demo || !controlHome || controlError) throw Error(controlError || i18n.t('请先连接本地记录目录'));
  if (sending) throw Error(i18n.t('正在发送，请稍候'));
  const home = controlHome, settings = settingsFor(home);
  if (!settings.workspace) throw Error(i18n.t('请先选择私人助理的工作目录'));
  assertModelSourceEnabled(settings.provider, settings.model);
  sending = true; refresh();
  try {
    const result = await cli(['desk','assistant','--agent',settings.provider,'--cwd',settings.workspace,'--input','-', ...(settings.model ? ['--model',settings.model] : []), ...(settings.effort ? ['--effort',settings.effort] : []), ...permissionArgs()], text, home, settings.binding.controller_id);
    settings.binding = result.desk || await cli(['desk','status'],'',home,settings.binding.controller_id);
    save(); return result;
  } finally { sending = false; refresh(); }
}
function stop() {
  generation++;
  if (probe) { probe.kill(); probe = null; }
  if (watcher) { watcher.kill(); watcher = null; }
  for (const reader of readers) reader.kill();
  readers.clear(); runtime = null;
  controlHome = null; controlPromise = null; controlError = ''; lastSnapshot = null;
}
function settingsFor(home) {
  preferences.desks ||= {};
  preferences.desks[home] ||= { provider: 'claude-code', model: '', effort: '', workspace: '' };
  return preferences.desks[home];
}
function refresh() { if (lastSnapshot) publish(lastSnapshot); }
function cli(args, input = '', home = controlHome, controller) {
  if (!runtime || !home) return Promise.reject(Error(i18n.t('请先连接本地记录目录')));
  const chosen = runtime;
  return new Promise((resolve, reject) => {
    const child = spawn(chosen.command, [...chosen.prefix, '-m', 'autoswarm', '--home', home, ...(controller ? ['--controller', controller] : []), ...args], { env: chosen.env, windowsHide: true, stdio: ['pipe','pipe','pipe'] });
    readers.add(child);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    let output = '', diagnostic = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-2000); });
    child.on('error', reject); child.stdin.on('error', () => {});
    child.stdin.end(input);
    child.on('close', code => {
      readers.delete(child);
      let response = null;
      try { response = JSON.parse(output); } catch { /* reported below */ }
      if (response && !code && response.ok) return resolve(response.data);
      // Keep the CLI's error code and the tail of stderr: an Agent repairing a failed setup needs them.
      reject(Object.assign(Error(response ? response.error?.message || i18n.t('AutoSwarm 操作失败') : output ? i18n.t('AutoSwarm 返回了无法解析的结果') : diagnostic || i18n.t('AutoSwarm 进程已退出')),
        { code: response?.error?.code || '', diagnostic, exitCode: code, output: response ? '' : output.slice(-2000) }));
    });
  });
}
async function initializeControl(home, token) {
  if (demo || controlPromise || controlHome === home) return;
  controlPromise = (async () => {
    const settings = settingsFor(home);
    const binding = await cli(['desk', 'init'], '', home, settings.binding?.controller_id);
    if (token !== generation) return;
    settings.binding = binding;
    // The chosen agent's assistant conversation keeps its workspace; with none chosen yet, the latest one is shown.
    const own = (binding.assistants || []).find(a => a.provider === settings.provider) || (!settings.providerChosen && binding.assistant);
    if (own) { settings.provider = own.provider; settings.workspace = own.workspace; }
    if (!settings.workspace) {
      const call = Object.values(lastSnapshot?.data?.calls || {}).find(c => c.task.workspace && fs.existsSync(c.task.workspace));
      settings.workspace = call?.task.workspace || (app.isPackaged ? '' : path.resolve(__dirname, '../..'));
    }
    // Claude Code, Codex and Cursor are read from their own session records; only other agents need a copy.
    await cli(['config', '--capture-events', preferences.captureAll ? 'on' : 'auto'], '', home);
    if (token !== generation) return;
    controlHome = home; controlError = ''; save(); refresh();
    const agents = availability.has(home) ? Promise.resolve() : cli(['agents'], '', home).then(result => {
      availability.set(home, Object.fromEntries((result.agents || []).map(a => [a.provider, { available: !!a.available, version: a.version || '' }])));
      if (token === generation) refresh();
    }).catch(() => { /* Unknown availability keeps every provider selectable. */ });
    agents.then(() => token === generation && ensureSkills(home)).catch(() => { /* Unknown status leaves the switch usable. */ });
  })();
  try { await controlPromise; }
  catch (error) { if (token === generation) { controlHome = home; controlError = error.message; refresh(); } }
  finally { if (token === generation) controlPromise = null; }
}
function locate(command, args, env) {
  return new Promise(resolve => {
    const child = spawn(command, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    probe = child;
    let output = '';
    const timer = setTimeout(() => child.kill(), 5000);
    child.stdout.on('data', chunk => { output = (output + chunk.toString('utf8')).slice(-8000); });
    const finish = code => {
      clearTimeout(timer);
      if (probe === child) probe = null;
      const filename = output.trim().split(/\r?\n/).at(-1);
      resolve(code === 0 && filename && fs.existsSync(filename) ? filename : null);
    };
    child.on('error', () => finish(-1));
    child.on('close', finish);
  });
}
async function connect() {
  stop();
  const token = generation;
  if (demo) {
    const { createDemo } = await import('./sample.mjs');
    if (token !== generation) return;
    publish({ event: 'snapshot', data: createDemo({ showcase: true }), changes: [] });
    return;
  }
  publish({ event: 'connecting', home: preferences.home });
  const source = app.isPackaged ? path.join(process.resourcesPath, 'python') : path.resolve(__dirname, '../../src');
  const args = ['-u', '-m', 'autoswarm.desk.watch', ...(preferences.home ? ['--home', preferences.home] : [])];
  const base = { ...process.env, PYTHONPATH: source, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' };
  // Read a chosen home only; inherited agent identity never participates.
  for (const key of Object.keys(base)) if (key.startsWith('AUTOSWARM_') || ['CODEX_THREAD_ID', 'CLAUDECODE', 'CLAUDE_SESSION_ID'].includes(key)) delete base[key];
  const candidates = [];
  // The installer ships its own Python (scripts/fetch-python.cjs); a chosen interpreter still wins.
  const bundled = app.isPackaged ? path.join(process.resourcesPath, 'python', 'runtime', process.platform === 'win32' ? 'python.exe' : 'bin/python3') : '';
  if (preferences.python || process.env.AUTOSWARM_DESK_PYTHON) candidates.push([preferences.python || process.env.AUTOSWARM_DESK_PYTHON, []]);
  else if (bundled && fs.existsSync(bundled)) candidates.push([bundled, []]);
  else {
    // Resolve launchers to an actual interpreter. Killing py/uv alone on
    // Windows would otherwise leave a descendant watcher running after exit.
    // macOS has python3 rather than python, and its own /usr/bin/python3 can be older than 3.10, so older ones are skipped there.
    const newer = 'import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)';
    const probes = process.platform === 'win32'
      ? [['python', ['-c', 'import sys; print(sys.executable)']], ['py', ['-3', '-c', 'import sys; print(sys.executable)']], ['uv', ['python', 'find', '--offline']]]
      : [['python3', ['-c', newer]], ['python', ['-c', newer]], ['uv', ['python', 'find', '--offline', '>=3.10']]];
    for (const [command, probeArgs] of probes) {
      const executable = await locate(command, probeArgs, base);
      if (token !== generation) return;
      if (executable) candidates.push([executable, []]);
    }
  }
  let diagnostic = '';
  async function attempt(index) {
    if (token !== generation) return;
    if (index >= candidates.length) {
      publish({ event: 'error', message: i18n.t('无法启动 Python 监听进程。请在设置中选择 Python 3.10+，然后重新连接。'), detail: diagnostic });
      return;
    }
    const [command, prefix] = candidates[index];
    // Agents installed, or API keys set, after Desk started are seen without signing out (user-env.cjs).
    const env = await userEnv.current(base, command, prefix);
    if (token !== generation) return;
    runtime = { command, prefix, env, source };
    const child = spawn(command, [...prefix, ...args], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    watcher = child;
    let received = false, finished = false;
    const timer = setTimeout(() => { if (!received) child.kill(); }, 12000);
    child.stderr.on('data', data => { diagnostic = (diagnostic + data.toString('utf8')).slice(-4000); });
    const lines = readline.createInterface({ input: child.stdout });
    lines.on('line', line => {
      if (token !== generation) return;
      try {
        const value = JSON.parse(line);
        if (value.event === 'snapshot') { received = true; clearTimeout(timer); publish(value); initializeControl(value.data.home, token); }
      } catch { diagnostic = i18n.t('监听进程返回了无法解析的数据。'); }
    });
    function ended() {
      if (finished) return;
      finished = true; clearTimeout(timer); lines.close();
      if (token !== generation) return;
      if (!received) attempt(index + 1);
      else publish({ event: 'error', message: i18n.t('监听连接已断开，显示的数据可能已过期。请重新连接。'), detail: diagnostic });
    }
    child.on('error', error => { diagnostic = error.message; ended(); });
    child.on('close', ended);
  }
  attempt(0);
}

// Links in replies open in Desk's side panel. Pages there may navigate the web and local files;
// pop-ups open in the same panel, and they get no permissions (camera, notifications, ...).
const PREVIEW_PARTITION = 'desk-preview';
app.on('web-contents-created', (_, contents) => {
  if (contents.getType() !== 'webview') return;
  contents.setWindowOpenHandler(({ url }) => { if (/^(https?|file):/i.test(url)) contents.loadURL(url); return { action: 'deny' }; });
  contents.on('will-navigate', (event, url) => { if (!/^(https?|file|about):/i.test(url)) event.preventDefault(); });
});
const PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024;
const PREVIEW_KINDS = { markdown: ['.md', '.markdown', '.mdx'], html: ['.html', '.htm', '.svg'], pdf: ['.pdf'], image: ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.avif'] };
// Opened with the system's default app only when the user asks; never anything that would run.
const RUNNABLE = /\.(exe|com|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|msi|msp|scr|lnk|reg|hta|cpl|jar|appref-ms)$/i;
/** What a link in a reply points to: a file's kind and, for Markdown and text, its contents. */
async function previewTarget(target, base) {
  let file = /^file:/i.test(target) ? fileURLToPath(target) : target;
  file = path.resolve(typeof base === 'string' && base ? base : os.homedir(), file);
  const name = path.basename(file), stat = await fs.promises.stat(file).catch(() => null);
  if (!stat) return { kind: 'missing', path: file, name };
  if (stat.isDirectory()) return { kind: 'folder', path: file, name };
  const ext = path.extname(file).toLowerCase();
  let kind = Object.keys(PREVIEW_KINDS).find(key => PREVIEW_KINDS[key].includes(ext)) || 'text';
  const value = { kind, path: file, name, url: pathToFileURL(file).href, size: stat.size };
  if (kind === 'markdown' || kind === 'text') {
    const handle = await fs.promises.open(file, 'r');
    try {
      const buffer = Buffer.alloc(Math.min(stat.size, PREVIEW_TEXT_LIMIT));
      await handle.read(buffer, 0, buffer.length, 0);
      if (buffer.subarray(0, 8192).includes(0)) value.kind = 'binary';
      else Object.assign(value, { text: buffer.toString('utf8'), truncated: stat.size > PREVIEW_TEXT_LIMIT });
    } finally { await handle.close(); }
  }
  return value;
}
app.whenReady().then(async () => {
  // macOS always shows an application menu; Electron's default one has developer items (reload, DevTools).
  // This one keeps the standard app, edit (copy and paste in text fields) and window commands.
  if (process.platform === 'darwin' && app.getLoginItemSettings().wasOpenedAtLogin) background = true;
  // The app's name stays the package name (autoswarm-desk: Desk's data folder is named after it), so the menu
  // and the About panel are given the product name.
  if (process.platform === 'darwin') {
    const name = 'AutoSwarm Desk';
    app.setAboutPanelOptions({ applicationName: name, applicationVersion: app.getVersion(), version: '', copyright: 'AutoSwarm contributors · MIT License' });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { label: name, submenu: [{ role: 'about', label: `About ${name}` }, { type: 'separator' }, { role: 'services' }, { type: 'separator' },
        { role: 'hide', label: `Hide ${name}` }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }, { role: 'quit', label: `Quit ${name}` }] },
      { role: 'fileMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }]));
  }
  preferencesPath = path.join(app.getPath('userData'), 'preferences.json');
  try { preferences = JSON.parse(fs.readFileSync(preferencesPath, 'utf8')); } catch { /* First start. */ }
  if (cliHome || process.env.AUTOSWARM_HOME) preferences.home = cliHome || process.env.AUTOSWARM_HOME;
  // Desk's own language; English unless chosen (AUTOSWARM_DESK_LANGUAGE presets the first start).
  i18n.setLanguage(preferences.language || process.env.AUTOSWARM_DESK_LANGUAGE || 'en');
  await syncLanguage();
  await loadRules();
  // Editors save in several ways (rewrite, rename); polling the path sees all of them.
  fs.watchFile(rulesPath(), { interval: 1000 }, () => loadRules().catch(() => {}));
  app.on('will-quit', () => fs.unwatchFile(rulesPath()));
  const bounds = preferences.bounds || {};
  nativeTheme.themeSource = ['light','dark'].includes(preferences.theme) ? preferences.theme : 'system';
  const surface = () => nativeTheme.shouldUseDarkColors ? '#151517' : '#ffffff';
  // macOS keeps its own window buttons (top left) over the same custom titlebar; Windows draws its own.
  const chrome = process.platform === 'darwin' ? { titleBarStyle: 'hidden', trafficLightPosition: { x: 14, y: 14 } } : { frame: false };
  win = new BrowserWindow({ width: Math.max(800, bounds.width || 1320), height: Math.max(600, bounds.height || 900),
    minWidth: 540, minHeight: 500, title: 'AutoSwarm Desk', backgroundColor: surface(), show: !background,
    icon: path.join(__dirname, '../assets', process.platform === 'win32' ? 'icon.ico' : 'logo.png'), ...chrome,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: true, plugins: true }
  });
  // plugins: Chromium's PDF viewer, for PDFs in the side panel (an iframe; a webview cannot draw them).
  // The side panel's embedded browser: no preload or Node, sandboxed, its own in-memory session, web and local files only.
  win.webContents.on('will-attach-webview', (event, webPreferences, params) => {
    delete webPreferences.preload;
    Object.assign(webPreferences, { nodeIntegration: false, nodeIntegrationInSubFrames: false, contextIsolation: true, sandbox: true, webSecurity: true, plugins: true });
    params.partition = PREVIEW_PARTITION;
    if (!/^(https?:|file:|about:blank)/i.test(params.src || 'about:blank')) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  // The window is frameless with no menu, so zoom keys are handled here.
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown' || !(input.control || input.meta) || input.alt) return;
    const step = { '=': 1, '+': 1, '-': -1, '_': -1, '0': 0 }[input.key];
    if (step !== undefined) { event.preventDefault(); setZoom(step); }
  });
  win.webContents.on('did-finish-load', () => win.webContents.setZoomFactor(zoomFactor()));
  // macOS hides its window buttons in full screen: the titlebar then gives their space back.
  if (process.platform === 'darwin') {
    const fullScreen = value => () => { if (!win.isDestroyed()) win.webContents.send('desk:fullscreen', value); };
    win.on('enter-full-screen', fullScreen(true)); win.on('leave-full-screen', fullScreen(false));
    win.webContents.on('did-finish-load', () => fullScreen(win.isFullScreen())());
  }
  win.webContents.on('will-navigate', event => event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(false));
  electronSession.fromPartition(PREVIEW_PARTITION).setPermissionRequestHandler((_, permission, callback) => callback(permission === 'fullscreen'));
  const rendererURL = pathToFileURL(path.join(__dirname, '../renderer/index.html')).href;
  const trayURL = pathToFileURL(path.join(__dirname, '../renderer/tray.html')).href;
  desktop = createShell({ assets: path.join(__dirname, '../assets'), preload: path.join(__dirname, 'preload.cjs'), trayURL, background,
    mainWindow: () => win, preferences: () => preferences, setPreference, onWake: queueWake, rules: () => ruleSet.rules, onRule: runRule });
  await desktop.setup();
  // Tests drive the tray through this handle; Electron cannot enumerate tray icons.
  if (process.env.AUTOSWARM_DESK_USER_DATA) globalThis.deskShell = desktop;
  if (preferences.openAtLogin) applyLoginItem(); // Keep the login entry pointing at this executable.
  registerToastIdentity();
  // New Desk versions (electron/updates.cjs); the window shows one when it is ready or available.
  updater = updates.createUpdater({ app, net, onChange: () => refresh(), log: updates.fileLog(path.join(app.getPath('userData'), 'updates.log')) });
  updater.start();
  // The main window and the tray popup are the only trusted renderers.
  function trusted(event) {
    return (event.sender === win.webContents && event.senderFrame?.url === rendererURL) ||
      (desktop.popup && !desktop.popup.isDestroyed() && event.sender === desktop.popup.webContents && event.senderFrame?.url === trayURL);
  }
  function handle(channel, fn) { ipcMain.handle(channel, (event, ...args) => { if (!trusted(event)) throw Error('Invalid sender'); return fn(...args); }); }
  handle('desk:initial', () => frame);
  handle('desk:choose-home', async () => {
    const result = await dialog.showOpenDialog(win, { title: i18n.t('选择 AUTOSWARM_HOME 记录目录'), properties: ['openDirectory'], defaultPath: preferences.home });
    if (!result.canceled) { preferences.home = result.filePaths[0]; demo = false; save(); await connect(); }
  });
  handle('desk:choose-python', async () => {
    // Interpreters on macOS have no extension (python3) and may sit inside an app bundle (Python.app).
    const pick = process.platform === 'darwin' ? { properties: ['openFile', 'treatPackageAsDirectory'] } : { properties: ['openFile'], filters: [{ name: 'Python', extensions: ['exe'] }] };
    const result = await dialog.showOpenDialog(win, { title: i18n.t('选择 Python 3.10+ 解释器'), ...pick });
    if (!result.canceled) { preferences.python = result.filePaths[0]; save(); if (!demo) await connect(); }
  });
  handle('desk:copy', text => {
    if (typeof text !== 'string' || text.length > 5_000_000) throw Error(i18n.t('无法复制此内容'));
    clipboard.writeText(text);
    return true;
  });
  handle('desk:preview', (target, base) => {
    if (typeof target !== 'string' || !target || target.length > 4096) throw Error(i18n.t('无法打开这个链接'));
    return previewTarget(target, base);
  });
  handle('desk:open-external', url => {
    if (typeof url !== 'string' || !/^(https?|mailto):/i.test(url)) throw Error(i18n.t('无法打开这个链接'));
    return shell.openExternal(url);
  });
  handle('desk:open-path', async file => {
    if (typeof file !== 'string' || !path.isAbsolute(file)) throw Error(i18n.t('无法打开这个文件'));
    if (RUNNABLE.test(file)) { shell.showItemInFolder(file); return 'shown'; }
    const failure = await shell.openPath(file);
    if (failure) throw Error(failure);
    return 'opened';
  });
  handle('desk:show-path', file => {
    if (typeof file !== 'string' || !path.isAbsolute(file)) throw Error(i18n.t('无法打开这个文件'));
    shell.showItemInFolder(file);
  });
  handle('desk:zoom', step => {
    if (![-1, 0, 1].includes(step)) throw Error(i18n.t('无效的缩放'));
    return setZoom(step);
  });
  handle('desk:language', async value => {
    if (!['en', 'zh'].includes(value)) throw Error(i18n.t('无效的语言'));
    preferences.language = i18n.setLanguage(value); save();
    await syncLanguage();
    desktop?.relabel?.(); refresh();
    return value;
  });
  handle('desk:theme', value => {
    if (!['system','light','dark'].includes(value)) throw Error(i18n.t('无效的外观设置'));
    preferences.theme = value; save();
    nativeTheme.themeSource = value;
    win.setBackgroundColor(surface());
    if (frame) frame = { ...frame, theme: value };
    return value;
  });
  handle('desk:demo', async () => { demo = true; await connect(); });
  handle('desk:reconnect', async () => { demo = false; await connect(); });
  function localModels() {
    const installed = () => {
      const library = path.join(process.env.OLLAMA_MODELS || path.join(os.homedir(), '.ollama', 'models'), 'manifests', 'registry.ollama.ai', 'library');
      try {
        return fs.readdirSync(library, { withFileTypes: true }).filter(item => item.isDirectory()).flatMap(item =>
          fs.readdirSync(path.join(library, item.name), { withFileTypes: true }).filter(tag => tag.isFile()).map(tag => item.name + ':' + tag.name));
      } catch { return []; }
    };
    const fallback = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Ollama', 'ollama.exe');
    const executable = process.platform === 'win32' && fs.existsSync(fallback) ? fallback : 'ollama';
    return new Promise(resolve => {
      const child = spawn(executable, ['list'], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
      let output = '';
      const timer = setTimeout(() => child.kill(), 5000);
      child.stdout.on('data', chunk => { output = (output + chunk.toString('utf8')).slice(0, 32768); });
      child.on('error', () => { clearTimeout(timer); resolve(installed()); });
      child.on('close', code => {
        clearTimeout(timer);
        resolve(code === 0 ? output.split(/\r?\n/).slice(1).map(line => line.trim().split(/\s+/)[0]).filter(Boolean) : installed());
      });
    });
  }
  async function listAgentSwitches() {
    const home = preferences.home || controlHome;
    if (demo) return { agents: [], localModels: [], home: home || null, demo: true };
    if (!home) throw Error(i18n.t('请先连接本地记录目录'));
    const inventory = await cli(['agents'], '', home);
    const agents = (inventory.agents || []).filter(agent => agent.available || agent.configured).map(agent => {
      const provider = agent.provider;
      return { provider, name: AGENT_NAMES[provider] || provider, available: !!agent.available, version: agent.version || '', enabled: agent.enabled !== false, manageable: true, error: agent.error || '' };
    });
    const models = await localModels();
    if (JSON.stringify(preferences.detectedLocalModels || []) !== JSON.stringify(models)) { preferences.detectedLocalModels = models; save(); refresh(); }
    return { agents, localModels: models, modelSources: modelSourceCatalog(models), home, demo: false };
  }
  handle('desk:agent-switches', () => listAgentSwitches());
  handle('desk:agent-toggle', async provider => {
    if (typeof provider !== 'string' || !/^[a-z][a-z0-9-]{0,40}$/.test(provider)) throw Error(i18n.t('无效的 Agent'));
    if (demo) throw Error(i18n.t('请先连接本地记录目录'));
    const before = await listAgentSwitches();
    const agent = before.agents.find(item => item.provider === provider);
    if (!agent) throw Error('Agent ' + provider + ' is not detected in this AutoSwarm home');
    const enabled = !agent.enabled;
    await cli(['config', '--agent', provider, '--enabled', enabled ? 'on' : 'off'], '', before.home);
    await connect();
    return listAgentSwitches();
  });
  handle('desk:model-source-toggle', async key => {
    if (typeof key !== 'string' || key.length > 240) throw Error(i18n.t('无效的模型服务'));
    const state = await listAgentSwitches();
    if (!state.modelSources.some(source => source.key === key)) throw Error('Model source is not currently detected in AutoSwarm Desk');
    const disabled = new Set(Array.isArray(preferences.disabledModelSources) ? preferences.disabledModelSources : []);
    if (disabled.has(key)) disabled.delete(key); else disabled.add(key);
    preferences.disabledModelSources = [...disabled];
    save(); publishVisibleModelCatalogs(); refresh();
    return modelSourceCatalog(state.localModels);
  });
  handle('desk:choose-workspace', async () => {
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    const settings = settingsFor(controlHome);
    if (settings.binding?.assistant_agent_id) throw Error(i18n.t('这个 Agent 的助理会话已有工作目录；换一个 Agent 可以用新的目录'));
    const result = await dialog.showOpenDialog(win, { title: i18n.t('选择私人助理的工作目录'), properties: ['openDirectory'], defaultPath: settings.workspace || undefined });
    if (!result.canceled) { settings.workspace = result.filePaths[0]; save(); refresh(); }
  });
  handle('desk:assistant-settings', value => {
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    const settings = settingsFor(controlHome);
    if (!value || !PROVIDERS.includes(value.provider)) throw Error(i18n.t('请选择有效厂商'));
    for (const key of ['provider','model','effort']) if (typeof value[key] !== 'string' || value[key].length > 200) throw Error(i18n.t('配置值无效'));
    // Switching agent switches to its own assistant conversation (or a new one); the others are kept and listed.
    const own = (settings.binding?.assistants || []).find(a => a.provider === value.provider.trim());
    if (own) settings.workspace = own.workspace;
    for (const key of ['provider','model','effort']) settings[key] = value[key].trim();
    settings.providerChosen = true;
    save(); refresh();
  });
  handle('desk:assistant-send', async text => {
    if (typeof text !== 'string' || !text.trim()) throw Error(i18n.t('请输入要交给私人助理的事项'));
    return sendAssistant(text);
  });
  handle('desk:preferences', value => {
    if (!value || typeof value !== 'object') throw Error(i18n.t('设置值无效'));
    for (const [key, item] of Object.entries(value)) setPreference(key, item);
    return true;
  });
  handle('desk:show', target => {
    const clean = target && typeof target === 'object' ? Object.fromEntries(['session','request','worker'].filter(k => typeof target[k] === 'string' && target[k].length < 300).map(k => [k, target[k]])) : undefined;
    desktop.showMain(target?.compose === true ? { compose: true } : clean);
  });
  handle('desk:tray', value => {
    if (value === 'hide') desktop.popup?.hide();
    else if (value && typeof value.height === 'number') desktop.resizePopup(value.height);
  });
  handle('desk:quit', () => desktop.quit());
  handle('desk:update', action => {
    if (action === 'install') return updater.installNow();
    if (action === 'check') return updater.check();
    throw Error(i18n.t('当前无法处理此请求'));
  });
  // Models and efforts each agent reports itself (autoswarm models). The last list is shown at once and read
  // again in the background: the first time it is needed in each Desk run, when it is an hour old, when the
  // agent's CLI version changed, and when the user asks. A new list is sent to the window and replaces the old.
  // Version 2 asks every agent (ACP agents, Claude Code's initialize); older entries are read again.
  const catalogReads = new Map(), readThisRun = new Set(), CATALOG_VERSION = 2, CATALOG_FRESH = 3600 * 1000;
  function readCatalog(provider) {
    if (catalogReads.has(provider)) return catalogReads.get(provider);
    const home = controlHome;
    const run = cli(['models', '--agent', provider], '', home).then(value => {
      const entry = { at: Date.now(), value, version: CATALOG_VERSION, agent: availability.get(home)?.[provider]?.version || '' };
      preferences.catalogs = { ...(preferences.catalogs || {}), [provider]: entry }; save();
      readThisRun.add(provider);
      const fresh = { ...value, read_at: entry.at };
      const visible = visibleModelCatalog(provider, fresh);
      if (win && !win.isDestroyed()) win.webContents.send('desk:catalog', { provider, value: visible });
      return visible;
    }).finally(() => catalogReads.delete(provider));
    catalogReads.set(provider, run);
    return run;
  }
  handle('desk:models', async (provider, options) => {
    if (!PROVIDERS.includes(provider)) throw Error(i18n.t('未知的 Agent'));
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    const stored = preferences.catalogs?.[provider];
    const cached = stored?.version === CATALOG_VERSION ? stored : null;
    if (!cached || options?.refresh === true) {
      try { return await readCatalog(provider); }
      catch (error) { if (cached) return visibleModelCatalog(provider, { ...cached.value, read_at: cached.at, refresh_error: error.message }); throw error; }
    }
    const agent = availability.get(controlHome)?.[provider]?.version || '';
    if (!readThisRun.has(provider) || Date.now() - cached.at > CATALOG_FRESH || (agent && cached.agent && cached.agent !== agent)) readCatalog(provider).catch(() => { /* The shown list stays. */ });
    return visibleModelCatalog(provider, { ...cached.value, read_at: cached.at });
  });
  handle('desk:rules-open', async () => {
    const file = rulesPath(), created = !fs.existsSync(file);
    if (created) {
      const { template } = await import('./rules.mjs');
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, template(), { flag: 'wx' });
      await loadRules();
    }
    // No editor registered for .json: show the file instead.
    if (await shell.openPath(file)) shell.showItemInFolder(file);
    return { created, path: file };
  });
  handle('desk:session-workspace', async () => {
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    const settings = settingsFor(controlHome), session = settings.session || {};
    const result = await dialog.showOpenDialog(win, { title: i18n.t('选择新会话的工作目录'), properties: ['openDirectory'], defaultPath: session.workspace || settings.workspace || undefined });
    if (result.canceled) return null;
    settings.session = { ...session, workspace: result.filePaths[0] }; save(); refresh();
    return result.filePaths[0];
  });
  handle('desk:session-start', async value => {
    if (demo || !controlHome || controlError) throw Error(controlError || i18n.t('请先连接本地记录目录'));
    if (!value || typeof value !== 'object' || !PROVIDERS.includes(value.provider)) throw Error(i18n.t('请选择有效厂商'));
    if (typeof value.prompt !== 'string' || !value.prompt.trim() || value.prompt.length > 200000) throw Error(i18n.t('请描述要交给主 Agent 的工作'));
    for (const key of ['model','effort']) if (typeof value[key] !== 'string' || value[key].length > 200) throw Error(i18n.t('模型或思考强度无效'));
    if (value.permission !== undefined && !(typeof value.permission === 'string' && /^[\w-]{1,40}$/.test(value.permission))) throw Error(i18n.t('权限模式无效'));
    if (typeof value.workspace !== 'string' || !value.workspace || !fs.existsSync(value.workspace) || !fs.statSync(value.workspace).isDirectory()) throw Error(i18n.t('请选择存在的工作目录'));
    const home = controlHome, settings = settingsFor(home);
    const model = value.model.trim(), effort = value.effort.trim();
    assertModelSourceEnabled(value.provider, model);
    settings.session = { provider: value.provider, model, effort, workspace: value.workspace }; save();
    return cli(['desk','start','--agent',value.provider,'--cwd',value.workspace,'--input','-', ...(model ? ['--model',model] : []), ...(effort ? ['--effort',effort] : []), ...(typeof value.skill === 'boolean' ? ['--skill', value.skill ? 'on' : 'off'] : []), ...permissionArgs(), ...(value.permission ? ['--permission', value.permission] : [])], value.prompt, home, settings.binding.controller_id);
  });
  handle('desk:skills', async () => {
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    return refreshSkills(controlHome);
  });
  // An agent picked as the main agent gets its Skill now (the same setup as on launch).
  handle('desk:skill-install', async host => {
    if (demo || !controlHome || controlError) throw Error(controlError || i18n.t('请先连接本地记录目录'));
    if (!SKILL_HOSTS.includes(host)) throw Error(i18n.t('未知的 Agent'));
    await ensureSkills(controlHome, host);
    return skillState.get(controlHome);
  });
  // When the automatic install failed, an agent this machine can run installs it in a new conversation.
  handle('desk:skill-agent', async host => {
    if (demo || !controlHome || controlError) throw Error(controlError || i18n.t('请先连接本地记录目录'));
    if (!SKILL_HOSTS.includes(host)) throw Error(i18n.t('未知的 Agent'));
    const home = controlHome, known = availability.get(home) || {};
    const helper = [host, ...SKILL_HOSTS].find(id => known[id]?.available === true);
    if (!helper) throw Error(i18n.t('本机没有可用的 Agent'));
    const started = await cli(['desk','start','--agent',helper,'--cwd',os.homedir(),'--input','-', ...permissionArgs()], installPrompt(host, skillState.get(home)?.errors?.[host]), home, settingsFor(home).binding.controller_id);
    return { ...started, helper };
  });
  handle('desk:cancel', async target => {
    if (demo || !controlHome || !runtime) throw Error(i18n.t('请先连接本地记录目录'));
    if (typeof target !== 'string' || !/^(?:call|main)_[a-f0-9]{32}$/.test(target) || !frame?.data?.agents?.[target]) throw Error(i18n.t('找不到要停止的会话'));
    return cli(['desk','cancel',target],'',controlHome,settingsFor(controlHome).binding.controller_id);
  });
  handle('desk:permissions', (provider, mode) => {
    if (!PROVIDERS.includes(provider) || typeof mode !== 'string' || !/^[\w-]{1,40}$/.test(mode)) throw Error(i18n.t('权限模式无效'));
    preferences.permissions = { ...(preferences.permissions || {}), [provider]: mode }; save(); refresh();
  });
  // One conversation's own mode, from its next turn.
  handle('desk:permission', async (agentId, mode) => {
    if (demo || !controlHome || !runtime) throw Error(i18n.t('请先连接本地记录目录'));
    if (typeof agentId !== 'string' || !frame?.data?.agents?.[agentId] || typeof mode !== 'string' || !/^[\w-]{1,40}$/.test(mode)) throw Error(i18n.t('会话或权限模式无效'));
    return cli(['desk','permission',agentId,mode],'',controlHome,settingsFor(controlHome).binding.controller_id);
  });
  handle('desk:decide', async (call, request, decision, scope = 'once') => {
    if (demo || !controlHome || !['approve','deny'].includes(decision) || !['once','session'].includes(scope)) throw Error(i18n.t('当前无法处理此请求'));
    const entry = frame?.data?.requests?.[call + '/' + request];
    if (!entry?.pending || entry.request.kind !== 'approval_required') throw Error(i18n.t('请求已结束或需要回答问题'));
    return cli([decision,call,request,'--by','user','--scope',scope]);
  });
  handle('desk:answer', async (call, request, response) => {
    if (demo || !controlHome) throw Error(i18n.t('请先连接本地记录目录'));
    const entry = frame?.data?.requests?.[call + '/' + request];
    if (!entry?.pending || entry.request.kind !== 'interaction_required' || !response || typeof response !== 'object') throw Error(i18n.t('此问题已结束或回答格式无效'));
    return cli(['answer',call,request,'--by','user','--input','-'],JSON.stringify(response));
  });
  handle('desk:message', async (target, text, mode) => {
    if (demo || !controlHome || !runtime) throw Error(i18n.t('请先连接本地记录目录'));
    if (typeof target !== 'string' || !frame?.data?.agents?.[target] || typeof text !== 'string' || !text.trim() || text.length > 200000 || !['after_turn','immediate'].includes(mode)) throw Error(i18n.t('消息目标或内容无效'));
    return cli(['desk','message',target,'--mode',mode,'--input','-'],text,controlHome,settingsFor(controlHome).binding.controller_id);
  });
  handle('desk:reveal', id => {
    const record = frame?.data?.calls?.[id];
    if (!demo && record) shell.showItemInFolder(path.join(record.directory, 'task.json'));
  });
  handle('desk:history', (id, before) => new Promise((resolve, reject) => {
    const call = frame?.data?.calls?.[id];
    if (demo || !runtime || !call || (before !== undefined && (!Number.isSafeInteger(before) || before < 0))) return reject(Error(i18n.t('无法读取此调用的历史')));
    const child = spawn(runtime.command, [...runtime.prefix, '-m', 'autoswarm.desk.watch', '--home', frame.data.home, '--events', id, ...(before === undefined ? [] : ['--before', String(before)])], { env: runtime.env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    readers.add(child);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    let output = '', diagnostic = '';
    const timer = setTimeout(() => child.kill(), 15000);
    child.stdout.on('data', chunk => { output += chunk.toString('utf8'); });
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk.toString('utf8')).slice(-2000); });
    child.on('error', reject);
    child.on('close', code => {
      clearTimeout(timer); readers.delete(child);
      try { if (code) throw Error(diagnostic || i18n.t('历史读取中断')); resolve(JSON.parse(output)); }
      catch (error) { reject(error); }
    });
  }));
  handle('desk:native', agentId => new Promise((resolve, reject) => {
    if (demo || !runtime || typeof agentId !== 'string' || !frame?.data?.native_conversations?.[agentId]) return reject(Error(i18n.t('无法读取此会话的原生记录')));
    const child = spawn(runtime.command, [...runtime.prefix, '-m', 'autoswarm.desk.watch', '--home', frame.data.home, '--native', agentId], { env: runtime.env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    readers.add(child);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    let output = '', diagnostic = '';
    const timer = setTimeout(() => child.kill(), 20000);
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-2000); });
    child.on('error', reject);
    child.on('close', code => {
      clearTimeout(timer); readers.delete(child);
      try { if (code) throw Error(diagnostic || i18n.t('原生记录读取中断')); resolve(JSON.parse(output)); }
      catch (error) { reject(error); }
    });
  }));
  ipcMain.on('desk:window', (event, action) => {
    if (!trusted(event)) return;
    if (action === 'minimize') win.minimize();
    if (action === 'maximize') win.isMaximized() ? win.unmaximize() : win.maximize();
    if (action === 'close') win.close();
  });
  win.on('close', event => {
    preferences.bounds = win.getNormalBounds(); save();
    if (desktop.keepOnClose(event)) return; // Still watching from the tray.
    stop();
  });
  await win.loadURL(rendererURL);
  await connect();
});
app.on('window-all-closed', () => { stop(); app.quit(); });
app.on('before-quit', stop);
