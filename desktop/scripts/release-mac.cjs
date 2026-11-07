// Collects the Mac files of one Desk release in dist/release-mac after `npm run dist:mac`
// (`npm run release:mac` runs both):
//   AutoSwarm-Desk-mac-arm64.dmg, AutoSwarm-Desk-mac-x64.dmg      what people download (Apple silicon, Intel)
//   AutoSwarm-Desk-<version>-mac-arm64.zip, ...-mac-x64.zip        the same apps without a disk image
// They go to the same GitHub Release as the Windows files (tagged desk-v<version>, drafted by the Desk
// release workflow): a release published as latest must keep the Windows files, which the READMEs and
// Windows' automatic update read from releases/latest. Mac copies look for a release with a .dmg.
const fs = require('node:fs'), path = require('node:path');

const { version } = require('../package.json');
const dist = path.join(__dirname, '..', 'dist'), out = path.join(dist, 'release-mac');
const files = ['arm64', 'x64'].flatMap(arch => [`AutoSwarm-Desk-mac-${arch}.dmg`, `AutoSwarm-Desk-${version}-mac-${arch}.zip`]);
const missing = files.filter(name => !fs.existsSync(path.join(dist, name)));
if (missing.length) { console.error(`release-mac: missing ${missing.join(', ')} in ${dist}; run npm run dist:mac first`); process.exit(1); }

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
for (const name of files) fs.copyFileSync(path.join(dist, name), path.join(out, name));
const size = name => (fs.statSync(path.join(out, name)).size / 1024 / 1024).toFixed(1) + ' MB';
console.log(`AutoSwarm Desk ${version} for macOS: add these files to the GitHub Release tagged desk-v${version}`);
for (const name of files) console.log(`  ${name}  ${size(name)}`);
console.log(`For example: gh release upload desk-v${version} dist/release-mac/*`);
