'use strict';
// Shared by the Electron tests.
const { spawnSync } = require('node:child_process');

/** The interpreter the tests run AutoSwarm with: the one chosen for Desk, else uv's. */
const python = process.env.AUTOSWARM_DESK_PYTHON || spawnSync('uv', ['python', 'find'], { encoding: 'utf8', windowsHide: true }).stdout.trim();

/** Poll until `predicate` (plain or async) returns a value, which is returned. */
async function until(predicate, what, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error('Timed out waiting for ' + what);
}

module.exports = { python, until };
