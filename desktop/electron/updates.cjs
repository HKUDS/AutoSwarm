// Desk updates come from this repository's GitHub Releases tagged desk-v<version>, so Desk releases
// can sit beside other releases. A copy installed by the setup program downloads a newer version in
// the background and installs it when Desk quits; a ZIP copy only links to the new release.
// The installer replaces its whole folder and stops everything running from it, including agents'
// runners and AutoSwarm commands on Desk's bundled Python, so installing waits until none of them run.
// macOS: Desk is not signed with an Apple Developer ID, which installing an update in place requires,
// so a Mac copy only links to a newer release that has a Mac download.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const RELEASES = 'https://api.github.com/repos/HKUDS/AutoSwarm/releases?per_page=30';
const TAG = /^desk-v(\d+\.\d+\.\d+)$/;
const FEED = 'latest.yml';
const FIRST_CHECK = 20 * 1000, CHECK_EVERY = 6 * 3600 * 1000;

const numbers = version => String(version).split(/[-+]/)[0].split('.').map(part => Number(part) || 0);
function compare(a, b) {
  const x = numbers(a), y = numbers(b);
  for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0) ? 1 : -1;
  return 0;
}
const MAC_DOWNLOAD = /^AutoSwarm-Desk-mac-[\w-]+\.dmg$/;
/** The newest published Desk release above the running version, or null; `needs` names a download it must have. */
function newest(releases, current, needs = null) {
  let best = null;
  for (const release of Array.isArray(releases) ? releases : []) {
    const match = !release?.draft && !release?.prerelease && TAG.exec(release?.tag_name || '');
    if (!match || compare(match[1], current) <= 0 || (best && compare(match[1], best.version) <= 0)) continue;
    if (needs && !(release.assets || []).some(asset => needs.test(asset?.name || ''))) continue;
    const feed = (release.assets || []).find(asset => asset?.name === FEED)?.browser_download_url;
    best = { version: match[1], page: /^https?:\/\//.test(release.html_url || '') ? release.html_url : null,
      feed: typeof feed === 'string' ? feed.slice(0, -FEED.length - 1) : null };
  }
  return best;
}
/** Installed by the setup program (which leaves its uninstaller beside the executable), not unzipped. */
const installed = (executable = process.execPath, product = 'AutoSwarm Desk') => fs.existsSync(path.join(path.dirname(executable), `Uninstall ${product}.exe`));

/**
 * Processes started from Desk's folder other than Desk itself and the helpers it started:
 * agents' runners and AutoSwarm commands that use Desk's Python (Skills installed by Desk use it too).
 * Asked of Desk's own Python (autoswarm.desk.processes), which reads them in a fraction of a second.
 */
function othersRunning(folder = path.dirname(process.execPath), own = process.pid, executable = path.basename(process.execPath),
  python = path.join(process.resourcesPath || '', 'python', 'runtime', 'python.exe')) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-I', '-m', 'autoswarm.desk.processes', folder, '--children-of', String(own), '--except-name', executable], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    let output = '';
    const timer = setTimeout(() => child.kill(), 20000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => { output += chunk; });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      try { if (code !== 0) throw Error('Process check failed (' + code + ')'); resolve(JSON.parse(output)); }
      catch (error) { reject(error); }
    });
  });
}

/** Update decisions in Desk's data folder (updates.log, the previous one in updates.log.old). */
function fileLog(file, limit = 512 * 1024) {
  const write = (level, args) => {
    const text = args.map(value => value instanceof Error ? value.message : typeof value === 'string' ? value : JSON.stringify(value)).join(' ');
    try { if (fs.statSync(file).size > limit) fs.renameSync(file, file + '.old'); } catch { /* No log yet. */ }
    try { fs.appendFileSync(file, `${new Date().toISOString()} ${level} ${text}\n`); } catch { /* Logging never stops an update. */ }
  };
  return { info: (...args) => write('info', args), warn: (...args) => write('warn', args), error: (...args) => write('error', args) };
}

/**
 * state.kind: off (development, tests, AUTOSWARM_DESK_UPDATES=off), idle, current, downloading,
 * ready (downloaded, installs when Desk quits), available (a ZIP copy, or the download failed: link only).
 */
function createUpdater({ app, net, onChange = () => {}, log = { info() {}, warn: console.warn, error: console.error }, running = othersRunning, platform = process.platform, isInstalled = installed, load = () => require('electron-updater').autoUpdater, windows = () => require('electron').BrowserWindow.getAllWindows(), recheck = 1000,
  cache = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'autoswarm-desk-updater'),
  record = path.join(app.getPath?.('userData') || os.tmpdir(), 'update-installing.json') }) {
  const source = process.env.AUTOSWARM_DESK_RELEASES || RELEASES;
  // Tests use their own user data and check only against a release list they serve.
  const linkOnly = platform === 'darwin';
  const enabled = app.isPackaged && (platform === 'win32' || linkOnly) && process.env.AUTOSWARM_DESK_UPDATES !== 'off'
    && (!process.env.AUTOSWARM_DESK_USER_DATA || !!process.env.AUTOSWARM_DESK_RELEASES);
  let state = { kind: enabled ? 'idle' : 'off', current: app.getVersion(), installed: !linkOnly && isInstalled() };
  let engine = null, checking = null, deciding = false, decided = false, installing = false;
  const set = patch => { state = { ...state, ...patch }; onChange(state); };

  function updater() {
    if (engine) return engine;
    engine = load();
    engine.logger = { info: message => log.info(message), debug() {}, warn: message => log.warn(message), error: message => log.error(message) };
    engine.autoDownload = true;
    engine.disableWebInstaller = true;  // Desk ships the full installer only.
    engine.autoInstallOnAppQuit = true;
    engine.on('update-downloaded', info => set({ kind: 'ready', version: info.version, error: '' }));
    engine.on('error', error => { if (state.kind === 'downloading') set({ kind: 'available', error: error?.message || String(error) }); });
    return engine;
  }
  async function look() {
    const response = await net.fetch(source, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'AutoSwarm-Desk/' + app.getVersion() } });
    if (!response.ok) throw Error('GitHub releases: HTTP ' + response.status);
    const release = newest(await response.json(), app.getVersion(), linkOnly ? MAC_DOWNLOAD : null);
    log.info(`Checked ${source}: ${release ? 'found ' + release.version : 'nothing newer than ' + app.getVersion()}`);
    // Checked each time: right after an update the installer may still be finishing.
    const copy = !linkOnly && isInstalled();
    if (!release) return set({ kind: 'current', installed: copy, checked_at: Date.now() });
    if (state.kind === 'downloading' && state.version === release.version) return;
    if (!copy || !release.feed) return set({ kind: 'available', installed: copy, version: release.version, page: release.page });
    set({ kind: 'downloading', installed: copy, version: release.version, page: release.page, error: '' });
    const engine = updater();
    // As electron-updater's own GitHub provider: one range per request, so a differential update
    // hits the release download once (later ranges go to the address it redirects to).
    engine.setFeedURL({ provider: 'generic', url: release.feed, useMultipleRangeRequest: false });
    const result = await engine.checkForUpdates();
    result?.downloadPromise?.catch(() => { /* Reported by the error event. */ });
    if (!result?.isUpdateAvailable) set({ kind: 'current', checked_at: Date.now() });
  }
  function check() {
    if (!enabled || installing || state.kind === 'ready') return Promise.resolve(state);
    checking ||= look().catch(error => {
      log.warn('Update check failed:', error.message);
      if (state.kind === 'downloading') set({ kind: 'available', error: error.message });
    }).finally(() => { checking = null; });
    return checking.then(() => state);
  }
  /** How many processes run from Desk's folder (-1: could not tell). */
  function busy() {
    return running().then(list => {
      if (list.length) log.warn('Update waits for:', list.map(item => `${item.path} (${item.pid})`).join(', '));
      return list.length;
    }, error => { log.warn('Update waits: could not check for running processes:', error.message); return -1; });
  }
  // A differential download starts from the setup program's copy of the running version (installer.exe in
  // electron-updater's cache). An installed update leaves the previous version's copy there, so the next
  // update would download the whole installer: Desk notes the installer it hands over and, once that
  // version runs, puts it there.
  function remember() {
    try { fs.writeFileSync(record, JSON.stringify({ version: state.version, installer: engine.installerPath })); } catch { /* Only a larger next download. */ }
  }
  function adopt() {
    let value;
    try { value = JSON.parse(fs.readFileSync(record, 'utf8')); fs.rmSync(record, { force: true }); } catch { return; }
    if (value?.version !== app.getVersion() || typeof value.installer !== 'string' || !fs.existsSync(value.installer)) return;
    try { fs.copyFileSync(value.installer, path.join(cache, 'installer.exe')); log.info(`Kept the ${value.version} installer for the next differential update`); }
    catch (error) { log.warn('Could not keep the installer:', error.message); }
  }
  /** At quit, Desk's own short commands may still be finishing: look again a few times before waiting. */
  async function settled() {
    let count = await busy();
    for (let tries = 0; count > 0 && tries < 3; tries++) {
      await new Promise(resolve => setTimeout(resolve, recheck));
      count = await busy();
    }
    return count;
  }
  // Quitting installs a downloaded version, unless something still runs from Desk's folder:
  // then it waits for a later quit (the download is kept).
  function beforeQuit(event) {
    if (state.kind !== 'ready' || installing || decided) return;
    event.preventDefault();
    if (deciding) return;
    deciding = true;
    for (const window of windows()) window.hide();
    return settled().then(count => {
      engine.autoInstallOnAppQuit = count === 0;
      if (count === 0) remember();
      log.info(count === 0 ? `Quitting: installing ${state.version}` : `Quitting: ${state.version} waits for a later quit`);
      decided = true;
      app.quit();
    });
  }
  /** Restart now: refused while agents run from Desk's folder ({ busy }). */
  async function installNow() {
    if (state.kind !== 'ready') return { state };
    const count = await busy();
    if (count) return { busy: count };
    installing = true;
    remember();
    engine.quitAndInstall(true, true);
    return { installing: true };
  }
  function start() {
    if (!enabled) return;
    adopt();
    setTimeout(check, FIRST_CHECK).unref?.();
    setInterval(check, CHECK_EVERY).unref?.();
    app.on('before-quit', beforeQuit);
  }
  return { start, check, installNow, beforeQuit, state: () => state };
}

module.exports = { createUpdater, fileLog, newest, compare, installed, othersRunning, RELEASES };
