// Derive Desk's icons from the official AutoSwarm logo (assets/logo.png, the same file as
// docs/assets/autoswarm/AutoSwarm-logo.png): assets/logo-128.png for the interface and
// assets/icon.ico (16-256 px, PNG entries) for the executable and taskbar.
// Run with Electron, which provides the resizer: npx electron scripts/make-icons.cjs
const { app, nativeImage } = require('electron');
const fs = require('node:fs'), path = require('node:path');
const assets = path.join(__dirname, '../assets');
app.whenReady().then(() => {
  const logo = nativeImage.createFromPath(path.join(assets, 'logo.png'));
  const png = size => logo.resize({ width: size, height: size, quality: 'best' }).toPNG();
  fs.writeFileSync(path.join(assets, 'logo-128.png'), png(128));
  const sizes = [16, 24, 32, 48, 64, 128, 256], images = sizes.map(png);
  const header = Buffer.alloc(6 + 16 * sizes.length);
  header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4);
  let offset = header.length;
  sizes.forEach((size, i) => {
    const entry = 6 + 16 * i;
    header.writeUInt8(size % 256, entry); header.writeUInt8(size % 256, entry + 1);  // 0 means 256.
    header.writeUInt16LE(1, entry + 4); header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(images[i].length, entry + 8); header.writeUInt32LE(offset, entry + 12);
    offset += images[i].length;
  });
  fs.writeFileSync(path.join(assets, 'icon.ico'), Buffer.concat([header, ...images]));
  console.log('icons written');
  app.quit();
});
