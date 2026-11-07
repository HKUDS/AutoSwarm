import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { merge } = createRequire(import.meta.url)('../electron/user-env.cjs');

test('the registry adds what a new login would see and replaces nothing Desk has', () => {
  const env = { Path: 'C:\\a;C:\\b', X: '1', USERPROFILE: 'C:\\Users\\me', SystemRoot: 'C:\\Windows' };
  const machine = [['Path', 'REG_EXPAND_SZ', '%SystemRoot%\\system32;C:\\a']];
  const user = [['Path', 'REG_SZ', 'C:\\new\\bin;C:\\b\\;C:\\用户\\工具'], ['ARK_API_KEY', 'REG_SZ', 'k'], ['X', 'REG_SZ', '2'],
    ['AUTOSWARM_HOME', 'REG_SZ', 'h'], ['TEMP', 'REG_EXPAND_SZ', '%USERPROFILE%\\tmp'], ['K2', 'REG_EXPAND_SZ', '%USERPROFILE%\\k'], ['EMPTY', 'REG_SZ', '']];
  const result = merge(env, machine, user);
  // New folders are appended once (C:\b\ is C:\b), in the order a login lists them.
  assert.equal(result.Path, 'C:\\a;C:\\b;C:\\Windows\\system32;C:\\new\\bin;C:\\用户\\工具');
  assert.equal(result.ARK_API_KEY, 'k', 'A variable set after Desk started is added');
  assert.equal(result.X, '1', 'A value Desk already has stays');
  assert.equal(result.K2, 'C:\\Users\\me\\k', 'REG_EXPAND_SZ is expanded');
  assert.equal(result.TEMP, undefined, 'Per-session values are left to the process');
  assert.equal(result.AUTOSWARM_HOME, undefined, 'Agent identity never comes from the registry');
  assert.equal('EMPTY' in result, false);
  assert.deepEqual(env.Path, 'C:\\a;C:\\b', 'The input is not changed');
});

test('PATH is found whatever its case, and kept when nothing is new', () => {
  assert.deepEqual(merge({ PATH: 'C:\\a' }, [['Path', 'REG_SZ', 'c:\\A\\']], []), { PATH: 'C:\\a' });
  assert.equal(merge({}, [], [['Path', 'REG_SZ', 'C:\\x']]).Path, 'C:\\x');
});

const { mergeShell, parseShell } = createRequire(import.meta.url)('../electron/user-env.cjs');

test('macOS: the login shell environment is read between the marks, whatever the profile prints', () => {
  const mark = '__AUTOSWARM_DESK_ENV__';
  const output = `Welcome!\n${mark}PATH=/opt/homebrew/bin:/usr/bin\0KEY=a=b\0MULTI=one\ntwo\0bad name=x\0${mark}bye`;
  assert.deepEqual(parseShell(output), { PATH: '/opt/homebrew/bin:/usr/bin', KEY: 'a=b', MULTI: 'one\ntwo' });
  assert.equal(parseShell('no marks here'), null, 'An unfinished answer keeps the environment as it is');
});

test('macOS: the shell PATH comes first, other variables are only added', () => {
  const env = { PATH: '/usr/bin:/bin:/desk/only', X: '1' };
  const shell = { PATH: '/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin:/bin', X: '2', ANTHROPIC_API_KEY: 'k', PWD: '/tmp', SHLVL: '2',
    TERM: 'xterm-256color', AUTOSWARM_HOME: 'h', CLAUDECODE: '1', EMPTY: '' };
  const result = mergeShell(env, shell);
  assert.equal(result.PATH, '/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin:/bin:/desk/only');
  assert.equal(result.X, '1', 'A value Desk already has stays');
  assert.equal(result.ANTHROPIC_API_KEY, 'k', 'A key exported in the profile is added');
  for (const name of ['PWD', 'SHLVL', 'TERM', 'AUTOSWARM_HOME', 'CLAUDECODE', 'EMPTY']) assert.equal(name in result, false, name);
  assert.equal(env.PATH, '/usr/bin:/bin:/desk/only', 'The input is not changed');
});
