// Collects one Desk release in dist/release after `npm run dist` (`npm run release` runs both):
//   AutoSwarm-Desk-Setup.exe                     the installer people download (the READMEs link to it)
//   AutoSwarm-Desk-<version>-win-x64.zip         the same app without installing
//   AutoSwarm-Desk-Update.exe, its .blockmap and latest.yml   read only by Desk's automatic update
// The update has its own copy of the installer, so the download statistics count people's downloads
// and automatic updates apart (scripts/collect_downloads.py). Upload all five files to a GitHub
// Release tagged desk-v<version>; Desk looks for updates among releases with that tag form.
const crypto = require('node:crypto'), fs = require('node:fs'), path = require('node:path');

const { version } = require('../package.json');
// The build folder can be given (an electron-builder output other than dist/, e.g. while Desk runs from
// dist/win-unpacked); the release files always go to dist/release.
const dist = path.resolve(process.argv[2] || path.join(__dirname, '..', 'dist')), out = path.join(__dirname, '..', 'dist', 'release');
const files = { update: 'AutoSwarm-Desk-Update.exe', blockmap: 'AutoSwarm-Desk-Update.exe.blockmap', feed: 'latest.yml',
  zip: `AutoSwarm-Desk-${version}-win-x64.zip`, setup: 'AutoSwarm-Desk-Setup.exe' };
const fail = message => { console.error('release: ' + message); process.exit(1); };

for (const key of ['update', 'blockmap', 'feed', 'zip']) if (!fs.existsSync(path.join(dist, files[key]))) fail(`missing ${path.join(dist, files[key])}; run npm run dist first`);
// latest.yml must describe this version's update installer, or installed copies would refuse it.
const feed = fs.readFileSync(path.join(dist, files.feed), 'utf8');
const field = name => feed.match(new RegExp(`^${name}: *'?([^'\\r\\n]+)'?\\s*$`, 'm'))?.[1];
const sha512 = crypto.createHash('sha512').update(fs.readFileSync(path.join(dist, files.update))).digest('base64');
if (field('version') !== version) fail(`latest.yml is for ${field('version')}, package.json is ${version}`);
if (field('path') !== files.update) fail(`latest.yml points to ${field('path')}, expected ${files.update}`);
if (field('sha512') !== sha512) fail('latest.yml checksum does not match ' + files.update);

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
for (const key of ['update', 'blockmap', 'feed', 'zip']) fs.copyFileSync(path.join(dist, files[key]), path.join(out, files[key]));
fs.copyFileSync(path.join(dist, files.update), path.join(out, files.setup));
const size = name => (fs.statSync(path.join(out, name)).size / 1024 / 1024).toFixed(1) + ' MB';
console.log(`AutoSwarm Desk ${version}: upload these files to a GitHub Release tagged desk-v${version}`);
for (const name of fs.readdirSync(out).sort()) console.log(`  ${name}  ${size(name)}`);
