// The README screenshots of Desk (docs/assets/desk/desk-en.png, desk-zh.png): `npm run screenshots`.
// Desk starts in its demo space with scratch preferences; the window then gets the scene of
// scripts/readme-scene.mjs, and the demo space's own banner and labels are taken out of the picture.
'use strict';
const { _electron: electron } = require('playwright');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');

const desktop = path.resolve(__dirname, '..'), output = path.resolve(desktop, '../docs/assets/desk');
const EN = require('../renderer/locale-en.json');
const say = (language, text, values = {}) => {
  let out = language === 'en' && EN[text] ? EN[text] : text;
  if (Array.isArray(out)) out = out[values.count === 1 ? 0 : 1];
  return out.replace(/\{(\w+)\}/g, (all, key) => key in values ? String(values[key]) : all);
};
const WIDTH = 1440, HEIGHT = 1120, SCALE = 2;

(async () => {
  const { readmeScene, MAIN } = await import('./readme-scene.mjs');
  fs.mkdirSync(output, { recursive: true });
  for (const language of ['en', 'zh']) {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'desk-screenshot-'));
    fs.writeFileSync(path.join(scratch, 'preferences.json'), JSON.stringify({ theme: 'light', language, bounds: { width: WIDTH, height: HEIGHT } }));
    const env = { ...process.env, AUTOSWARM_DESK_USER_DATA: scratch, AUTOSWARM_DESK_LANGUAGE: language, USERPROFILE: scratch, HOME: scratch };
    delete env.ELECTRON_RUN_AS_NODE;
    delete env.AUTOSWARM_HOME;
    const application = await electron.launch({ args: [desktop, '--demo', `--force-device-scale-factor=${SCALE}`], env });
    try {
      const page = await application.firstWindow();
      await page.waitForSelector('.session-item', { timeout: 30000 });
      await application.evaluate(({ BrowserWindow }, [width, height]) => BrowserWindow.getAllWindows()[0].setContentSize(width, height), [WIDTH, HEIGHT]);
      const frame = await page.evaluate(() => window.desk.initial());
      frame.data = readmeScene(language);
      await application.evaluate(({ BrowserWindow }, value) => BrowserWindow.getAllWindows()[0].webContents.send('desk:frame', value), frame);
      await page.locator(`aside [data-session="${MAIN}"]`).click();
      const dock = page.locator('.task-dock .dock-heading').first();
      if (await dock.getAttribute('aria-expanded') === 'false') await dock.click();
      await page.waitForTimeout(500);
      const members = frame.data.calls ? Object.values(frame.data.agents).filter(a => a.agent_id === MAIN || a.owner_id === MAIN).length : 0;
      await page.evaluate(({ live, context }) => {
        for (const element of document.querySelectorAll('.demo-banner, .demo-badge')) element.remove();
        for (const element of document.querySelectorAll('.live-label')) element.textContent = live;
        const info = document.querySelector('aside .source-info');
        if (info) { info.querySelector('strong').textContent = context[0]; info.querySelector('small').textContent = context[1]; }
        document.activeElement?.blur();
        const scroll = document.querySelector('.conversation-scroll');
        if (scroll) scroll.scrollTop = scroll.scrollHeight;
      }, { live: say(language, '实时'), context: [say(language, '本会话'), say(language, '{count} 个 Agent · 实时同步', { count: members })] });
      await page.mouse.move(440, HEIGHT - 120);  // A blank spot: no hover highlight in the picture.
      await page.waitForTimeout(300);
      const file = path.join(output, `desk-${language}.png`);
      await page.screenshot({ path: file });
      console.log(`${file} (${(fs.statSync(file).size / 1024).toFixed(0)} KB)`);
    } finally {
      await application.close().catch(() => {});
      fs.rmSync(scratch, { recursive: true, force: true });
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
