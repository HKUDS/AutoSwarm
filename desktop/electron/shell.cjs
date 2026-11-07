// Desktop integration (P3): tray icon and popup, taskbar badge, system
// notifications for the built-in triggers and the user's trigger rules, and the
// main window's tray life.
// Everything shown here comes from the same watcher frames as the main window;
// decisions still go through the main process' AutoSwarm CLI handlers.
const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, screen } = require('electron');
const path = require('node:path');
const { t } = require('./i18n.cjs');

const TRAY_WIDTH = 380;

function createShell({ assets, preload, trayURL, background, mainWindow, preferences, setPreference, onWake, rules = () => [], onRule = () => {} }) {
  let tray = null, popup = null, attention = null, engine = null, summary = null, summaryHome = null, hiddenAt = 0, quitting = false;
  let popupHeight = 560, hintShown = false, tooltip = 'AutoSwarm Desk';
  const notified = new Set(), live = new Set(), fired = new Map();
  const images = new Map(), image = name => {
    if (!images.has(name)) images.set(name, nativeImage.createFromPath(path.join(assets, 'tray', name + '.png')));
    return images.get(name);
  };
  // macOS menu bar: black-and-transparent "Template" images, which macOS tints for light and dark menu bars.
  const mac = process.platform === 'darwin';
  const trayImage = name => image(mac ? `mac/${name}Template` : name);
  let shown = '';
  const prefs = () => preferences();

  async function setup() {
    attention = await import('./attention.mjs');
    engine = await import('./rules.mjs');
    tray = new Tray(trayImage('tray'));
    tray.setToolTip('AutoSwarm Desk');
    tray.on('click', () => toggle());
    tray.on('right-click', () => tray.popUpContextMenu(menu()));
    app.on('before-quit', () => { quitting = true; });
  }

  function menu() {
    const waiting = summary?.waiting.length || 0, p = prefs();
    return Menu.buildFromTemplate([
      { label: waiting ? t('{count} 件事等你决定…', { count: waiting }) : t('没有等你决定的事'), enabled: waiting > 0, click: () => toggle(true) },
      { type: 'separator' },
      { label: t('打开 AutoSwarm Desk'), click: () => showMain() },
      { label: t('新会话'), click: () => showMain({ compose: true }) },
      { type: 'separator' },
      { label: t('需要我决定时弹出通知'), type: 'checkbox', checked: p.notify !== false, click: item => setPreference('notify', item.checked) },
      { label: t('会话完成或失败时通知'), type: 'checkbox', checked: p.notifyDone !== false, click: item => setPreference('notifyDone', item.checked) },
      { label: t(mac ? '开机时在菜单栏中启动' : '开机时在托盘中启动'), type: 'checkbox', checked: !!p.openAtLogin, click: item => setPreference('openAtLogin', item.checked) },
      { type: 'separator' },
      { label: t('退出 AutoSwarm Desk'), click: () => quit() }
    ]);
  }

  function quit() { quitting = true; app.quit(); }

  function createPopup() {
    popup = new BrowserWindow({ width: TRAY_WIDTH, height: popupHeight, show: false, frame: false, resizable: false, movable: false,
      minimizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true, alwaysOnTop: true,
      title: 'AutoSwarm Desk', backgroundColor: mainWindow()?.getBackgroundColor() || '#ffffff',
      webPreferences: { preload, contextIsolation: true, nodeIntegration: false, sandbox: true } });
    popup.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    popup.webContents.on('will-navigate', event => event.preventDefault());
    popup.on('blur', () => { if (popup.isVisible() && !popup.webContents.isDevToolsOpened()) { popup.hide(); hiddenAt = Date.now(); } });
    popup.on('close', event => { if (!quitting) { event.preventDefault(); popup.hide(); } });
    popup.loadURL(trayURL);
    return popup;
  }

  function place() {
    const bounds = tray?.getBounds() || { x: 0, y: 0, width: 0, height: 0 };
    const display = bounds.width ? screen.getDisplayNearestPoint({ x: bounds.x, y: bounds.y }) : screen.getPrimaryDisplay();
    const area = display.workArea, gap = 8, height = Math.min(popupHeight, area.height - gap * 2);
    let x = bounds.width ? Math.round(bounds.x + bounds.width / 2 - TRAY_WIDTH / 2) : area.x + area.width - TRAY_WIDTH - gap;
    let y = bounds.width && bounds.y < area.y + area.height / 2 ? area.y + gap : area.y + area.height - height - gap;
    if (bounds.width && bounds.x + bounds.width <= area.x) x = area.x + gap; // taskbar on the left
    if (bounds.width && bounds.x >= area.x + area.width) x = area.x + area.width - TRAY_WIDTH - gap; // on the right
    x = Math.min(Math.max(x, area.x + gap), area.x + area.width - TRAY_WIDTH - gap);
    popup.setBounds({ x, y, width: TRAY_WIDTH, height });
  }

  /** Left click on the tray icon. A click that just blurred the popup only closes it. */
  function toggle(show) {
    if (!popup || popup.isDestroyed()) createPopup();
    if (popup.isVisible() && !show) { popup.hide(); return false; }
    if (!show && Date.now() - hiddenAt < 300) return false;
    place(); popup.show(); popup.focus();
    return true;
  }

  function resizePopup(height) {
    if (!Number.isFinite(height)) return;
    popupHeight = Math.round(Math.min(Math.max(height, 220), 640));
    if (popup && !popup.isDestroyed() && popup.isVisible()) place();
  }

  function showMain(target) {
    const win = mainWindow();
    if (!win || win.isDestroyed()) return;
    if (win.isMinimized()) win.restore();
    win.show(); win.focus();
    if (target) win.webContents.send('desk:navigate', target);
    if (popup && !popup.isDestroyed()) popup.hide();
  }

  /** Main window close: stay in the tray unless quitting or the user turned it off. */
  function keepOnClose(event) {
    if (quitting || prefs().closeToTray === false || !tray) return false;
    event.preventDefault();
    mainWindow().hide();
    if (!hintShown && !prefs().trayHintShown) {
      hintShown = true; setPreference('trayHintShown', true);
      notify(mac ? { title: t('AutoSwarm Desk 仍在菜单栏中运行'), body: t('有事需要你决定时会在这里提醒你。右键菜单栏图标可以退出，点 Dock 图标可以打开窗口。'), silent: true }
        : { title: t('AutoSwarm Desk 仍在托盘中运行'), body: t('有事需要你决定时会在这里提醒你。右键托盘图标可以退出。'), silent: true });
    }
    return true;
  }

  function notify({ title, body, silent = false }, target) {
    if (!Notification.isSupported()) return;
    const note = new Notification({ title, body, silent, icon: path.join(assets, 'logo.png') });
    live.add(note); // Keep a reference until it is dismissed so clicks still arrive.
    note.on('click', () => { live.delete(note); showMain(target); });
    note.on('close', () => live.delete(note));
    note.show();
  }

  function badge() {
    if (!tray) return;
    const text = attention.trayText(summary);
    if (text.tooltip === shown) return; // Only touch the shell when the count changes.
    shown = text.tooltip;
    tray.setImage(trayImage(text.waiting ? 'tray-attention' : 'tray'));
    tray.setToolTip(tooltip = text.tooltip);
    // macOS has no taskbar overlay: the count goes on the Dock icon.
    if (mac) { app.dock?.setBadge(text.waiting ? (text.waiting > 9 ? '9+' : String(text.waiting)) : ''); return; }
    const win = mainWindow();
    if (win && !win.isDestroyed()) win.setOverlayIcon(text.waiting ? image(`badge-${text.waiting > 9 ? '9plus' : text.waiting}`) : null, text.waiting ? t('{count} 件等你决定', { count: text.waiting }) : '');
  }

  /** After a language change: redraw the tooltip and badge text (the tray menu is built on each right-click). */
  function relabel() { shown = ''; if (summary) badge(); }

  /** Every frame: forward to the popup, refresh the badge, run the built-in triggers and the rules. */
  function update(frame) {
    if (popup && !popup.isDestroyed()) popup.webContents.send('desk:frame', frame);
    if (!attention || frame.event !== 'snapshot') return;
    const next = attention.summarize(frame.data);
    const baseline = frame.demo || summaryHome !== frame.data.home ? null : summary;
    summary = next; summaryHome = frame.data.home;
    badge();
    if (frame.demo) return;
    const entries = attention.occurrences(baseline, next), p = prefs();
    const { actions, quiet } = engine.plan(rules(), entries, fired);
    const win = mainWindow(), watching = win && !win.isDestroyed() && win.isVisible() && win.isFocused();
    for (const event of attention.notifications(entries.filter(entry => !quiet.has(entry)))) {
      if (event.kind === 'waiting') {
        const keys = event.keys.filter(key => !notified.has(key));
        keys.forEach(key => notified.add(key));
        if (!keys.length || p.notify === false || watching) continue;
      } else if (p.notifyDone === false || watching) continue;
      notify(event, event.target);
    }
    for (const action of actions) {
      if (action.do === 'notify') {
        if (action.entry.on === 'waiting') notified.add(action.entry.key);
        // Like the built-in ones, a rule's notification stays quiet while the user is looking at Desk.
        if (!watching) notify({ title: action.title, body: action.text }, action.target);
        onRule(action, watching ? 'watching' : 'shown');
      } else onRule(action, action.skipped || (action.do === 'silence' ? 'silenced' : 'queued'));
    }
    const finished = entries.filter(entry => entry.scope === 'session' && next.assistant && entry.session.owner === next.assistant.id);
    if (finished.length) onWake(finished.map(entry => entry.session));
  }

  return { setup, update, toggle, showMain, keepOnClose, resizePopup, quit, notify, relabel,
    get summary() { return summary; }, get tooltip() { return tooltip; }, get popup() { return popup; }, get tray() { return tray; }, background };
}

module.exports = { createShell };
