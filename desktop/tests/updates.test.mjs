import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const { createUpdater, newest, compare } = createRequire(import.meta.url)('../electron/updates.cjs');

const BASE = 'https://github.com/HKUDS/AutoSwarm/releases';
const release = (tag, extra = {}) => ({ tag_name: tag, draft: false, prerelease: false, html_url: `${BASE}/tag/${tag}`,
  assets: [{ name: 'latest.yml', browser_download_url: `${BASE}/download/${tag}/latest.yml` }, { name: 'AutoSwarm-Desk-Setup.exe', browser_download_url: `${BASE}/download/${tag}/AutoSwarm-Desk-Setup.exe` }], ...extra });

test('only newer published desk-v releases count, whatever else the repository releases', () => {
  assert.equal(compare('0.10.0', '0.9.9'), 1);
  assert.equal(compare('1.0.0', '1.0.0-beta.1'), 0);
  const list = [release('v2.1.0'), release('desk-v0.9.9', { prerelease: true }), release('desk-v1.0.0', { draft: true }), release('desk-v0.9.8'), release('desk-v0.9.7'), release('desk-v0.9.6')];
  assert.deepEqual(newest(list, '0.9.7'), { version: '0.9.8', page: `${BASE}/tag/desk-v0.9.8`, feed: `${BASE}/download/desk-v0.9.8` });
  assert.equal(newest(list, '0.9.8'), null);
  assert.equal(newest([release('desk-v0.9.8', { assets: [] })], '0.9.7').feed, null);
  assert.equal(newest({ message: 'API rate limit exceeded' }, '0.9.7'), null);
});

function setup({ cache = scratch(), record = path.join(scratch(), 'update-installing.json'), version = '0.9.7', copy = true, releases = [release('desk-v0.9.8')], running = [], ok = true, platform = 'win32' } = {}) {
  let checked = 0;
  const app = Object.assign(new EventEmitter(), { isPackaged: true, getVersion: () => version, quits: 0, quit() { this.quits++; } });
  const engine = Object.assign(new EventEmitter(), { feeds: [], installs: [], autoInstallOnAppQuit: true, installerPath: path.join(cache, 'pending', 'AutoSwarm-Desk-Update.exe'),
    setFeedURL(value) { this.feeds.push(value); },
    async checkForUpdates() { setImmediate(() => this.emit('update-downloaded', { version: '0.9.8' })); return { isUpdateAvailable: true, downloadPromise: Promise.resolve() }; },
    quitAndInstall(...args) { this.installs.push(args); } });
  const net = { async fetch() { return { ok, status: ok ? 200 : 403, json: async () => releases }; } };
  const states = [], hidden = [];
  const log = { info() {}, warn() {}, error() {} };
  const updater = createUpdater({ app, net, log, isInstalled: () => copy, platform, onChange: state => states.push(state.kind), load: () => engine,
    running: async () => { checked++; return (typeof running === 'function' ? running() : running).map(pid => ({ pid, path: 'python.exe' })); }, windows: () => [{ hide: () => hidden.push(1) }], recheck: 0, cache, record });
  return { app, engine, updater, states, hidden, checks: () => checked, cache, record };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const scratches = [];
const scratch = () => { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'desk-updater-')); scratches.push(dir); return dir; };
test.after(() => { for (const dir of scratches) fs.rmSync(dir, { recursive: true, force: true }); });

test('an installed copy downloads the newer version from its release and is then ready', async () => {
  const { engine, updater, states } = setup();
  await updater.check(); await settle();
  assert.deepEqual(engine.feeds, [{ provider: 'generic', url: `${BASE}/download/desk-v0.9.8`, useMultipleRangeRequest: false }]);
  assert.deepEqual(states, ['downloading', 'ready']);
  assert.equal(updater.state().version, '0.9.8');
});

test('the installer handed over is kept for the next differential update once its version runs', async () => {
  const first = setup();
  await first.updater.check(); await settle();
  await first.updater.beforeQuit({ preventDefault() {} });
  assert.deepEqual(JSON.parse(fs.readFileSync(first.record, 'utf8')), { version: '0.9.8', installer: first.engine.installerPath });
  fs.mkdirSync(path.dirname(first.engine.installerPath), { recursive: true });
  fs.writeFileSync(first.engine.installerPath, 'installer 0.9.8');
  fs.writeFileSync(path.join(first.cache, 'installer.exe'), 'installer 0.9.7');
  // The update did not install: the copy stays.
  setup({ cache: first.cache, record: first.record }).updater.start();
  assert.equal(fs.readFileSync(path.join(first.cache, 'installer.exe'), 'utf8'), 'installer 0.9.7');
  assert.equal(fs.existsSync(first.record), false);
  // It did: 0.9.8 keeps its installer.
  fs.writeFileSync(first.record, JSON.stringify({ version: '0.9.8', installer: first.engine.installerPath }));
  setup({ cache: first.cache, record: first.record, version: '0.9.8' }).updater.start();
  assert.equal(fs.readFileSync(path.join(first.cache, 'installer.exe'), 'utf8'), 'installer 0.9.8');
  assert.equal(fs.existsSync(first.record), false);
});

test('a ZIP copy, or a release without update files, only links to the release', async () => {
  for (const options of [{ copy: false }, { releases: [release('desk-v0.9.8', { assets: [] })] }]) {
    const { engine, updater } = setup(options);
    await updater.check();
    assert.equal(updater.state().kind, 'available');
    assert.equal(updater.state().page, `${BASE}/tag/desk-v0.9.8`);
    assert.deepEqual(engine.feeds, []);
  }
});

test('a Mac copy only links to a newer release with a Mac download, and never downloads it', async () => {
  const mac = { name: 'AutoSwarm-Desk-mac-arm64.dmg', browser_download_url: `${BASE}/download/desk-v0.9.9/AutoSwarm-Desk-mac-arm64.dmg` };
  const withMac = release('desk-v0.9.9'); withMac.assets = [...withMac.assets, mac];
  // 0.9.9 has a Mac download, 1.0.0 is Windows only: a Mac is offered 0.9.9.
  const { engine, updater } = setup({ platform: 'darwin', releases: [release('desk-v1.0.0'), withMac] });
  assert.equal(updater.state().installed, false);
  await updater.check();
  assert.equal(updater.state().kind, 'available');
  assert.equal(updater.state().version, '0.9.9');
  assert.deepEqual(engine.feeds, [], 'Nothing is downloaded on a Mac');
  const windowsOnly = setup({ platform: 'darwin', releases: [release('desk-v1.0.0')] });
  await windowsOnly.updater.check();
  assert.equal(windowsOnly.updater.state().kind, 'current');
});

test('a failed check leaves Desk as it was', async () => {
  const { updater, states } = setup({ ok: false });
  await updater.check();
  assert.equal(updater.state().kind, 'idle');
  assert.deepEqual(states, []);
});

test('quitting installs only when nothing else runs from Desk\'s folder', async () => {
  for (const [running, install, looks] of [[[], true, 1], [[4242], false, 4], [() => { throw Error('no Python'); }, false, 1]]) {
    const { app, engine, updater, hidden, checks } = setup({ running });
    await updater.check(); await settle();
    let prevented = false;
    await updater.beforeQuit({ preventDefault: () => { prevented = true; } });
    assert.ok(prevented);
    assert.equal(hidden.length, 1);
    assert.equal(engine.autoInstallOnAppQuit, install);
    assert.equal(app.quits, 1);
    assert.equal(checks(), looks);  // Busy: looked again three times before waiting.
    prevented = false;
    updater.beforeQuit({ preventDefault: () => { prevented = true; } });
    assert.equal(prevented, false);  // The second quit goes through.
  }
});

test('restart now refuses while agents run from Desk\'s folder', async () => {
  let list = [11, 12];
  const { engine, updater } = setup({ running: () => list });
  assert.deepEqual(await updater.installNow(), { state: updater.state() });  // Nothing downloaded yet.
  await updater.check(); await settle();
  assert.deepEqual(await updater.installNow(), { busy: 2 });
  assert.deepEqual(engine.installs, []);
  list = [];
  assert.deepEqual(await updater.installNow(), { installing: true });
  assert.deepEqual(engine.installs, [[true, true]]);
});

test('development runs, other platforms and AUTOSWARM_DESK_UPDATES=off never check', async () => {
  const app = { isPackaged: false, getVersion: () => '0.9.7' };
  assert.equal(createUpdater({ app, net: {}, isInstalled: () => true, platform: 'win32' }).state().kind, 'off');
  assert.equal(createUpdater({ app: { ...app, isPackaged: true }, net: {}, isInstalled: () => true, platform: 'linux' }).state().kind, 'off');
  process.env.AUTOSWARM_DESK_UPDATES = 'off';
  try { assert.equal(createUpdater({ app: { ...app, isPackaged: true }, net: {}, isInstalled: () => true, platform: 'win32' }).state().kind, 'off'); }
  finally { delete process.env.AUTOSWARM_DESK_UPDATES; }
});
