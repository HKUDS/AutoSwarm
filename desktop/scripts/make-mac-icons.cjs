// macOS icons derived from assets/logo.png, like make-icons.cjs and make-tray-icons.cjs for Windows:
//   assets/tray/mac/trayTemplate.png (+@2x), tray-attentionTemplate.png (+@2x)   menu bar icons. "Template"
//     images are black and transparent; macOS tints them for light and dark menu bars. The attention
//     variant adds a dot at the top right, cut out from the logo so it reads at 18 points.
//   packaging/icon.icns   the app icon (Finder, Dock), with the margin macOS app icons keep around the artwork;
//     outside assets/, so it is not copied into the app (electron-builder reads it, package.json mac.icon).
// Run on a Mac (iconutil), with Electron as the resizer: npx electron scripts/make-mac-icons.cjs
const { app, nativeImage } = require('electron');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');

const assets = path.join(__dirname, '../assets');

/** BGRA pixels of the logo at size x size. */
const pixels = (logo, size) => Buffer.from(logo.resize({ width: size, height: size, quality: 'best' }).toBitmap());

function template(logo, size, attention) {
  const bitmap = pixels(logo, size);
  for (let i = 0; i < bitmap.length; i += 4) bitmap[i] = bitmap[i + 1] = bitmap[i + 2] = 0;  // Black; alpha kept.
  if (attention) {
    const r = size * 0.17, cx = size - r - size * 0.01, cy = r + size * 0.01, ring = r + size * 0.08;
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const d = Math.hypot(x + 0.5 - cx, y + 0.5 - cy), i = (y * size + x) * 4;
      if (d <= r) bitmap[i + 3] = 255;
      else if (d <= ring) bitmap[i + 3] = 0;
    }
  }
  // Template images are drawn black only where a pixel is opaque: firm up the soft edges of the downscaled logo.
  for (let i = 3; i < bitmap.length; i += 4) bitmap[i] = Math.min(255, Math.round(bitmap[i] * 1.15));
  return nativeImage.createFromBitmap(bitmap, { width: size, height: size }).toPNG();
}

/** The logo at 82% of a size x size transparent square, as macOS app icons are drawn. */
function appIcon(logo, size) {
  const inner = Math.round(size * 0.82), offset = Math.round((size - inner) / 2);
  const art = pixels(logo, inner), canvas = Buffer.alloc(size * size * 4);
  for (let y = 0; y < inner; y++) art.copy(canvas, ((y + offset) * size + offset) * 4, y * inner * 4, (y + 1) * inner * 4);
  return nativeImage.createFromBitmap(canvas, { width: size, height: size }).toPNG();
}

app.whenReady().then(() => {
  const logo = nativeImage.createFromPath(path.join(assets, 'logo.png'));
  const tray = path.join(assets, 'tray', 'mac');
  fs.mkdirSync(tray, { recursive: true });
  for (const [scale, suffix] of [[1, ''], [2, '@2x']]) {
    fs.writeFileSync(path.join(tray, `trayTemplate${suffix}.png`), template(logo, 18 * scale, false));
    fs.writeFileSync(path.join(tray, `tray-attentionTemplate${suffix}.png`), template(logo, 18 * scale, true));
  }
  const iconset = fs.mkdtempSync(path.join(os.tmpdir(), 'autoswarm-desk-')) + '/icon.iconset';
  fs.mkdirSync(iconset);
  for (const size of [16, 32, 128, 256, 512]) {
    fs.writeFileSync(path.join(iconset, `icon_${size}x${size}.png`), appIcon(logo, size));
    fs.writeFileSync(path.join(iconset, `icon_${size}x${size}@2x.png`), appIcon(logo, size * 2));
  }
  fs.mkdirSync(path.join(__dirname, '../packaging'), { recursive: true });
  execFileSync('iconutil', ['-c', 'icns', iconset, '-o', path.join(__dirname, '../packaging/icon.icns')]);
  fs.rmSync(path.dirname(iconset), { recursive: true, force: true });
  console.log('macOS icons written');
  app.quit();
});
