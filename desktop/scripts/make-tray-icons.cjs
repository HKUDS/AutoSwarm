// Generates the tray icons and taskbar count badges in assets/tray/ from
// assets/logo.png. Run once after changing the logo: node scripts/make-tray-icons.cjs
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('playwright');

const desktop = path.resolve(__dirname, '..');
const out = path.join(desktop, 'assets/tray');
fs.mkdirSync(out, { recursive: true });
const logo = 'data:image/png;base64,' + fs.readFileSync(path.join(desktop, 'assets/logo.png')).toString('base64');
const scales = [[1, ''], [1.5, '@1.5x'], [2, '@2x']];
const badges = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '9+'];

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage();
  const files = await page.evaluate(async ({ logo, scales, badges }) => {
    const image = new Image();
    image.src = logo;
    await image.decode();
    const png = canvas => canvas.toDataURL('image/png').split(',')[1];
    const result = {};
    for (const [scale, suffix] of scales) {
      const size = Math.round(16 * scale);
      for (const attention of [false, true]) {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        const g = canvas.getContext('2d');
        g.imageSmoothingQuality = 'high';
        g.drawImage(image, 0, 0, size, size);
        if (attention) {
          // Amber dot, top right, ringed so it reads on any taskbar colour.
          const r = size * 0.24, x = size - r - size * 0.02, y = r + size * 0.02;
          g.beginPath(); g.arc(x, y, r + size * 0.07, 0, Math.PI * 2); g.fillStyle = '#1b1b1c'; g.fill();
          g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fillStyle = '#f59e0b'; g.fill();
        }
        result[`tray${attention ? '-attention' : ''}${suffix}.png`] = png(canvas);
      }
      for (const label of badges) {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        const g = canvas.getContext('2d');
        g.beginPath(); g.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2); g.fillStyle = '#d97706'; g.fill();
        g.fillStyle = '#ffffff';
        g.font = `700 ${label.length > 1 ? size * 0.5 : size * 0.72}px "Segoe UI", Arial, sans-serif`;
        g.textAlign = 'center'; g.textBaseline = 'middle';
        g.fillText(label, size / 2, size / 2 + size * 0.04);
        result[`badge-${label === '9+' ? '9plus' : label}${suffix}.png`] = png(canvas);
      }
    }
    return result;
  }, { logo, scales, badges });
  for (const [name, data] of Object.entries(files)) fs.writeFileSync(path.join(out, name), Buffer.from(data, 'base64'));
  await browser.close();
  console.log(`wrote ${Object.keys(files).length} icons to ${path.relative(desktop, out)}`);
})();
