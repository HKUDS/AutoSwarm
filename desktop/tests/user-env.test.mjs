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
