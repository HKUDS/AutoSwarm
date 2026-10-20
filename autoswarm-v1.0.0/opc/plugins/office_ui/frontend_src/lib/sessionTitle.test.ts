import assert from 'node:assert/strict'
import { compactSessionTitle } from './sessionTitle'

assert.equal(
  compactSessionTitle('one two three four five six seven eight nine ten eleven'),
  'one two three four five six seven eight nine ten...',
)

assert.equal(
  compactSessionTitle('请你帮我设计实现一个后端管理系统'),
  '请你帮我设计实现一个...',
)

assert.equal(
  compactSessionTitle('Build API 请实现登录流程 and tests now'),
  'Build API 请实现登录流程 and...',
)

assert.equal(compactSessionTitle('Short title'), 'Short title')
assert.equal(compactSessionTitle('   '), 'New Chat')

console.log('sessionTitle.test.ts: OK (session titles compact to 10 mixed units)')
