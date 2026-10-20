import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { splitAssistantContent } from '../lib/assistantContent'
import { MarkdownBody } from './MarkdownBody'
import { parseProjectUpdatePayload } from './MessageList'

const reasoning = 'Verify the files.\n\n1. Read the evidence.\n2. Check the criteria.'
const narrative = '## Verification Summary\n\nFiles verified on disk.\n\n- Evidence checked.\n- Sources checked.\n\n| Criterion | Met? |\n| --- | --- |\n| Sources | Yes |'
const json = JSON.stringify({
  summary: 'All criteria met.\n\nReady for delivery.',
  review_verdict: 'approve',
  acceptance_status: [{ criterion: 'Sources', met: true }],
}, null, 2)
const nativeReply = `<think>\n${reasoning}\n</think>\n\n${narrative}\n\n\`\`\`json\n${json}\n\`\`\``
const render = (content: string, className?: string) => renderToStaticMarkup(
  <MarkdownBody content={content} className={className} collapseMode="never" />,
)

test('Native report/review prefix remains a short title; preceding Markdown remains narrative', () => {
  for (const prefix of ['Review', 'Report']) {
    const payload = parseProjectUpdatePayload(`**${prefix} #1: Evidence review**: ${nativeReply}`)
    assert.equal(payload?.title, `${prefix} #1: Evidence review`)
    assert.equal(payload?.narrative, nativeReply.slice(0, nativeReply.lastIndexOf('```json')).trim())
    assert.equal(payload?.summary, 'All criteria met.\n\nReady for delivery.')
    assert.equal(payload?.verdict, 'approve')
    assert.equal(payload?.acceptanceSummary, '1/1 acceptance checks met')
  }
})

test('a Native reply without a report label never promotes reasoning into the title', () => {
  const payload = parseProjectUpdatePayload(nativeReply)
  assert.ok(payload)
  assert.equal(payload.title, undefined)
  assert.ok(payload.narrative?.includes('## Verification Summary'))
})

test('braces, other code examples, and the word review inside reasoning do not shift the JSON boundary', () => {
  const content = nativeReply.replace(reasoning, `${reasoning}\n\nReview example: {not the payload}.\n\n\`\`\`js\nconst x = { summary: 'example' }\n\`\`\``)
  const payload = parseProjectUpdatePayload(content)
  assert.equal(payload?.title, undefined)
  assert.match(payload?.narrative ?? '', /const x =/)
  assert.equal(payload?.summary, 'All criteria met.\n\nReady for delivery.')
})

test('unfenced JSON and existing direct/prefixed structured updates still parse', () => {
  for (const content of [json, `**Review #1: Evidence**: ${json}`, `Review #1: Evidence: ${json}`]) {
    assert.equal(parseProjectUpdatePayload(content)?.summary, 'All criteria met.\n\nReady for delivery.')
  }
  const payload = parseProjectUpdatePayload(`<think>${reasoning}</think>\n${json}`)
  assert.equal(payload?.title, undefined)
  assert.equal(payload?.narrative, `<think>${reasoning}</think>`)
  assert.equal(parseProjectUpdatePayload('A regular review of {not JSON}.'), null)
})

test('Native tagged reasoning is expandable Markdown; answer paragraphs, lists and tables stay structured', () => {
  const payload = parseProjectUpdatePayload(nativeReply)!
  const html = render([payload.narrative, payload.summary].join('\n\n'), 'msg-project-update-summary')
  assert.match(html, /class="msg-content-agent msg-project-update-summary"/)
  assert.match(html, /<details class="msg-thinking"><summary>/)
  assert.doesNotMatch(html, /<details[^>]* open/)
  assert.match(html, /<ol>\n<li>Read the evidence\.<\/li>\n<li>Check the criteria\.<\/li>/)
  assert.match(html, /<h2>Verification Summary<\/h2>/)
  assert.match(html, /<ul>\n<li>Evidence checked\.<\/li>\n<li>Sources checked\.<\/li>/)
  assert.match(html, /<table>/)
  assert.match(html, /<p>All criteria met\.<\/p>\n<p>Ready for delivery\.<\/p>/)
  assert.doesNotMatch(html, /&lt;\/?think&gt;/)
})

test('reasoning never consumes the preview budget of the final answer', () => {
  const html = renderToStaticMarkup(<MarkdownBody content={`<think>${'Check evidence. '.repeat(400)}</think>\n\nFinal answer.`} />)
  assert.match(html, /<\/details><p>Final answer\.<\/p>/)
  assert.doesNotMatch(html, /Show more/)
})

test('streaming and repeated reasoning blocks retain their boundary and content', () => {
  assert.deepEqual(splitAssistantContent('Start.\n<think>First.</think>\nMiddle.\n<think>Second.'), [
    { kind: 'markdown', content: 'Start.\n', complete: true },
    { kind: 'thinking', content: 'First.', complete: true },
    { kind: 'markdown', content: '\nMiddle.\n', complete: true },
    { kind: 'thinking', content: 'Second.', complete: false },
  ])
  assert.match(render('<think>Waiting for the rest.'), /Thinking…/)
})

test('literal tags inside inline, fenced and incomplete code are preserved', () => {
  for (const code of [
    'Use `<think>` and `</think>` literally.',
    'Use `` `<think>` `` literally.',
    '```xml\n<think>Example only.</think>\n```',
    '~~~~html\n<think>Example only.</think>\n~~~~~',
    '```xml\n<think>Unfinished code example.',
  ]) {
    assert.deepEqual(splitAssistantContent(code), [{ kind: 'markdown', content: code, complete: true }])
    assert.doesNotMatch(render(code), /<details/)
  }
})

test('Codex/Claude style Markdown retains paragraphs, code and safe escaped HTML', () => {
  const markdown = 'First paragraph.\n\nSecond paragraph.\n\n- One\n- Two\n\n```js\nconsole.log("hello")\n```'
  assert.deepEqual(splitAssistantContent(markdown), [{ kind: 'markdown', content: markdown, complete: true }])
  const html = render(markdown)
  assert.match(html, /<p>First paragraph\.<\/p>\n<p>Second paragraph\.<\/p>/)
  assert.match(html, /console\.log\(&quot;hello&quot;\)/)
  assert.doesNotMatch(html, /<details/)
  assert.doesNotMatch(render('<think>Check.</think>\n\n<script>alert(1)</script>'), /<script>/)
})
