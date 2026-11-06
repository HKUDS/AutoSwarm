import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

for (const entry of ['app', 'tray']) await build({
  absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
  entryPoints: [`renderer/${entry}.mjs`],
  outfile: `renderer/${entry}.js`,
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: 'chrome120',
  minify: true,
  legalComments: 'linked',
  logLevel: 'info'
});
