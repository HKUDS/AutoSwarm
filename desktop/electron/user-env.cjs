'use strict';
/**
 * The Windows user environment as a new login would see it (macOS: see loginShell below).
 *
 * A process keeps the environment it started with. Desk often starts at login and
 * runs for days, so an agent installed later (its folder added to PATH) or an API
 * key set later (ARK_API_KEY=...) would stay invisible to every agent Desk starts.
 * Desk reads the current values from the registry instead: PATH gains the folders
 * it lacks, and a variable Desk's own environment does not have is added. Nothing
 * Desk already has is replaced.
 */
const { execFile, spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

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

/*
 * macOS: an app opened from Finder, the Dock or at login gets launchd's short PATH
 * (/usr/bin:/bin:/usr/sbin:/sbin), without what the user's shell profile adds (Homebrew,
 * ~/.local/bin, nvm) or exports (API keys), so agents that work in Terminal would not be
 * found. Desk asks the user's login shell for its environment instead.
 */
const MARK = '__AUTOSWARM_DESK_ENV__';
// Terminal and shell session details, not settings an agent needs.
const SHELL_SKIP = /^(PWD|OLDPWD|SHLVL|_|COLUMNS|LINES|SSLKEYLOGFILE|TERM|TERM_.*|COLORTERM|ITERM_.*|TMUX.*|STY|WINDOWID)$/;

/** NAME=value entries of `env -0` between two marks (a profile may print its own text around them). */
function parseShell(output) {
  const start = output.indexOf(MARK), end = output.lastIndexOf(MARK);
  if (start < 0 || end <= start) return null;
  const values = {};
  for (const entry of output.slice(start + MARK.length, end).split('\0')) {
    const at = entry.indexOf('=');
    if (at > 0 && /^[A-Za-z_][A-Za-z0-9_]*$/.test(entry.slice(0, at))) values[entry.slice(0, at)] = entry.slice(at + 1);
  }
  return values;
}

/**
 * The login shell's PATH comes first, as in Terminal, and folders only Desk had stay after it.
 * Any other variable is added only when Desk does not have it; nothing Desk has is replaced.
 */
function mergeShell(env, shell) {
  const result = { ...env };
  for (const [name, value] of Object.entries(shell)) {
    if (name === 'PATH' || !value || SHELL_SKIP.test(name) || IDENTITY.test(name) || Object.hasOwn(result, name)) continue;
    result[name] = value;
  }
  if (shell.PATH) result.PATH = [...new Set([...shell.PATH.split(':'), ...String(env.PATH || '').split(':')].filter(Boolean))].join(':');
  return result;
}

/** The environment of the user's login shell, or null (no answer within 5 s, an unusual shell). */
function loginShell(env) {
  const shell = [env.SHELL, os.userInfo().shell, '/bin/zsh'].find(file => typeof file === 'string' && path.isAbsolute(file) && fs.existsSync(file));
  if (!shell) return Promise.resolve(null);
  return new Promise(resolve => {
    let child;
    try { child = spawn(shell, ['-ilc', `printf '%s' ${MARK}; /usr/bin/env -0; printf '%s' ${MARK}`], { env, stdio: ['ignore', 'pipe', 'ignore'] }); }
    catch { resolve(null); return; }
    let output = '';
    const timer = setTimeout(() => child.kill(), 5000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => { output += chunk; if (output.length > 4_000_000) child.kill(); });
    child.on('error', () => { clearTimeout(timer); resolve(null); });
    child.on('close', () => { clearTimeout(timer); resolve(parseShell(output)); });
  });
}

/**
 * The current environment with what a fresh login would add; elsewhere, as is.
 * The registry is read by Desk's Python (autoswarm.desk.environment), which keeps
 * non-ASCII values intact where reg.exe would print them in the console code page.
 */
function current(env, command, prefix = []) {
  if (process.platform === 'darwin' && process.env.AUTOSWARM_DESK_KEEP_ENV !== '1') return loginShell(env).then(shell => shell ? mergeShell(env, shell) : { ...env });
  if (process.platform !== 'win32' || process.env.AUTOSWARM_DESK_KEEP_ENV === '1') return Promise.resolve({ ...env });
  return new Promise(resolve => {
    execFile(command, [...prefix, '-m', 'autoswarm.desk.environment'], { env, windowsHide: true, timeout: 10000, encoding: 'utf8' }, (error, stdout) => {
      let rows = null;
      try { rows = error ? null : JSON.parse(stdout); } catch { /* keep the environment as it is */ }
      resolve(rows ? merge(env, rows.machine || [], rows.user || []) : { ...env });
    });
  });
}

module.exports = { current, merge, mergeShell, parseShell };
