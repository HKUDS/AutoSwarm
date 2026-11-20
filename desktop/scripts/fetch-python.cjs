// The installer ships its own Python, so users need none installed (as DeepSeek's desktop does):
// a pinned, relocatable CPython from python-build-standalone, checked by SHA-256 and cached in build/.
// Runs on the build machine only (npm run pack / dist). AutoSwarm needs nothing beyond the standard library.
const { spawnSync } = require('node:child_process');
const crypto = require('node:crypto'), fs = require('node:fs'), path = require('node:path');
// 3.14 for its standard zstd (compression.zstd): DeepSeek Harness keeps its session records in Zstandard frames.
const PYTHON = { version: '3.14.10', release: '20261026',
  'win32-x64': { target: 'x86_64-pc-windows-msvc', sha256: 'c1876a9795d3a94ee49a1499a69cd91e26ca3e2829593f93fa6e4db90d05402b' },
  'darwin-arm64': { target: 'aarch64-apple-darwin', sha256: '95eb8c87d16af37c54e9765e31d8c56c0d8ffa9968ea0f704f73a9b83f84e9f9' },
  'darwin-x64': { target: 'x86_64-apple-darwin', sha256: '2fd8becd48cf0ff5f0a15df3c0db7cae7aa708dcaacf901bd30d6cdd6229370b' } };
// The architecture to package for, this machine's unless given (a Mac builds the Apple silicon and Intel apps in turn).
const arch = process.argv[2] || process.arch;
const pin = PYTHON[`${process.platform}-${arch}`];
if (!pin) throw Error(`No pinned Python for ${process.platform}-${arch}`);
const build = path.join(__dirname, '..', 'build'), out = path.join(build, 'python'), stamp = path.join(out, '.pinned');
// Packaged, the runtime sits in resources/python/runtime next to resources/python/autoswarm. A .pth file
// (its relative path counts from site-packages) lets `python -m autoswarm` find the package without
// PYTHONPATH, which agents' sandboxes may drop from the commands they run (Codex's on Windows does).
function findAutoSwarm() {
  const sitePackages = [path.join(out, 'Lib', 'site-packages'), path.join(out, 'lib', `python${PYTHON.version.split('.').slice(0, 2).join('.')}`, 'site-packages')].find(dir => fs.existsSync(dir));
  if (!sitePackages) throw Error('No site-packages in the bundled Python');
  fs.writeFileSync(path.join(sitePackages, 'autoswarm-desk.pth'), path.relative(sitePackages, path.dirname(out)) + '\n');
}
if (fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8') === pin.sha256) { findAutoSwarm(); process.exit(0); }
const name = `cpython-${PYTHON.version}+${PYTHON.release}-${pin.target}-install_only_stripped.tar.gz`, archive = path.join(build, name);
const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
fs.mkdirSync(build, { recursive: true });
if (!fs.existsSync(archive) || sha256(archive) !== pin.sha256) {
  const url = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON.release}/${encodeURIComponent(name)}`;
  if (spawnSync('curl', ['-fsSL', '--retry', '3', '-o', archive, url], { stdio: 'inherit' }).status !== 0) throw Error('Python download failed: ' + url);
  if (sha256(archive) !== pin.sha256) { fs.rmSync(archive); throw Error('Python archive checksum mismatch: ' + name); }
}
fs.rmSync(out, { recursive: true, force: true });
// Windows' own bsdtar: Git's GNU tar would read "D:" in a path as a remote host.
const tar = process.platform === 'win32' ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe') : 'tar';
if (spawnSync(tar, ['-xzf', archive, '-C', build], { stdio: 'inherit' }).status !== 0) throw Error('Could not unpack ' + archive);
// Tests, IDLE and Tk are never used.
for (const dir of ['Lib/test', 'Lib/idlelib', 'Lib/tkinter', 'Lib/turtledemo', 'tcl']) fs.rmSync(path.join(out, dir), { recursive: true, force: true });
if (process.platform === 'darwin') {
  const lib = path.join(out, 'lib'), stdlib = path.join(lib, `python${PYTHON.version.split('.').slice(0, 2).join('.')}`);
  for (const dir of ['test', 'idlelib', 'tkinter', 'turtledemo']) fs.rmSync(path.join(stdlib, dir), { recursive: true, force: true });
  for (const dir of fs.readdirSync(lib)) if (/^(tcl|tk|itcl|thread)\d/.test(dir)) fs.rmSync(path.join(lib, dir), { recursive: true, force: true });
}
findAutoSwarm();
fs.writeFileSync(stamp, pin.sha256);
console.log(`Bundled Python ${PYTHON.version} in ${out}`);
