// Builds AutoSwarm Desk for macOS (`npm run dist:mac`, or `npm run pack:mac` for the app folders only).
// Each architecture needs its own bundled Python, so the Apple silicon and Intel apps are built in turn:
//   node scripts/dist-mac.cjs [--dir] [arm64] [x64]      (both architectures unless some are named)
// Run on a Mac. The app is signed ad hoc (package.json mac.identity "-"), without an Apple Developer ID:
// the first time, macOS asks people to allow it in System Settings > Privacy & Security.
const { spawnSync } = require('node:child_process');
const path = require('node:path');

if (process.platform !== 'darwin') { console.error('dist-mac: run this on a Mac'); process.exit(1); }
const args = process.argv.slice(2), dir = args.includes('--dir');
const named = args.filter(arg => arg === 'arm64' || arg === 'x64');
const unknown = args.filter(arg => arg !== '--dir' && !named.includes(arg));
if (unknown.length) { console.error('dist-mac: unknown arguments ' + unknown.join(' ')); process.exit(1); }
const desktop = path.join(__dirname, '..');
const builder = path.join(desktop, 'node_modules', '.bin', 'electron-builder');
const run = (command, argv) => {
  const result = spawnSync(command, argv, { cwd: desktop, stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status || 1);
};
for (const arch of named.length ? named : ['arm64', 'x64']) {
  run(process.execPath, [path.join(__dirname, 'fetch-python.cjs'), arch]);
  run(builder, ['--mac', `--${arch}`, ...(dir ? ['--dir'] : []), '--publish', 'never']);
}
