'use strict';
/**
 * The Windows user environment as a new login would see it.
 *
 * A process keeps the environment it started with. Desk often starts at login and
 * runs for days, so an agent installed later (its folder added to PATH) or an API
 * key set later (ARK_API_KEY=...) would stay invisible to every agent Desk starts.
 * Desk reads the current values from the registry instead: PATH gains the folders
 * it lacks, and a variable Desk's own environment does not have is added. Nothing
 * Desk already has is replaced.
 */
const { execFile } = require('node:child_process');

// Per-process or per-session values that a login computes itself.
const SKIP = new Set(['PATH', 'TEMP', 'TMP', 'USERNAME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE', 'HOMEPATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS',
  // A TLS key log is a debugging switch that writes secrets; only a process that already has it keeps it.
  'SSLKEYLOGFILE']);

// Agent identity is never inherited (main.cjs removes it), a registry copy neither.
const IDENTITY = /^(AUTOSWARM_|CODEX_THREAD_ID$|CLAUDECODE$|CLAUDE_SESSION_ID$)/i;
const lookup = (env, name) => Object.entries(env).find(([key]) => key.toUpperCase() === name.toUpperCase())?.[1];
const expand = (value, env) => value.replace(/%([^%]+)%/g, (all, name) => lookup(env, name) ?? all);

/** Merge [[name, type, value]] rows (machine first, then user) into a copy of env. */
function merge(env, machine, user) {
  const result = { ...env };
  const pathKey = Object.keys(result).find(key => key.toUpperCase() === 'PATH') || 'Path';
  const present = String(result[pathKey] || '').split(';').filter(Boolean);
  const seen = new Set(present.map(p => p.toLowerCase().replace(/\\+$/, '')));
  const added = [];
  for (const rows of [machine, user]) {
    for (const [name, type, raw] of rows) {
      const value = type === 'REG_EXPAND_SZ' ? expand(raw, result) : raw;
      if (name.toUpperCase() === 'PATH') {
        for (const part of value.split(';').map(p => p.trim()).filter(Boolean)) {
          const key = part.toLowerCase().replace(/\\+$/, '');
          if (!seen.has(key)) { seen.add(key); added.push(part); }
        }
      } else if (!SKIP.has(name.toUpperCase()) && !IDENTITY.test(name) && lookup(result, name) === undefined && value) {
        result[name] = value;
      }
    }
  }
  if (added.length) result[pathKey] = [...present, ...added].join(';');
  return result;
}

/**
 * The current environment with what a fresh login would add; off Windows, as is.
 * The registry is read by Desk's Python (autoswarm.desk.environment), which keeps
 * non-ASCII values intact where reg.exe would print them in the console code page.
 */
function current(env, command, prefix = []) {
  if (process.platform !== 'win32' || process.env.AUTOSWARM_DESK_KEEP_ENV === '1') return Promise.resolve({ ...env });
  return new Promise(resolve => {
    execFile(command, [...prefix, '-m', 'autoswarm.desk.environment'], { env, windowsHide: true, timeout: 10000, encoding: 'utf8' }, (error, stdout) => {
      let rows = null;
      try { rows = error ? null : JSON.parse(stdout); } catch { /* keep the environment as it is */ }
      resolve(rows ? merge(env, rows.machine || [], rows.user || []) : { ...env });
    });
  });
}

module.exports = { current, merge };
