import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { TaskUserInputPanel } from './TaskUserInputPanel'

const markup = renderToStaticMarkup(
  React.createElement(TaskUserInputPanel, {
    meta: {
      checkpoint_type: 'task_user_input',
      checkpoint_id: 'cp-input',
      task_id: 'task-1',
      work_item_projection_title: 'Engineer Input',
      summary: 'Need one missing decision.',
      prompt: 'Please answer:\n\n- Which provider?\n- Which tier?\n\n```txt\nstripe\n```',
      questions: ['Which provider should be used?'],
      required_fields: ['provider'],
      context_note: 'Known context:\n\n- User wants checkout',
      requesting_role_id: 'engineer',
      requesting_task_id: 'task-1',
      requesting_work_item_id: 'work-item-1',
      seat_id: 'seat::team::engineering::engineer',
      active_subagents: [{ id: 'sub-1' }],
      permission_requests: [{ id: 'perm-1' }],
      worktree_path: '/tmp/work',
    },
    onReply: () => undefined,
    responded: false,
  }),
)

assert.match(markup, /Engineer Input/)
assert.match(markup, /<li>Which provider\?<\/li>/)
assert.match(markup, /<code class="language-txt">/)
assert.match(markup, /<summary>Runtime State<\/summary>/)
assert.match(markup, /Requester: <code>engineer<\/code>/)
assert.match(markup, /Work item: <code>work-item-1<\/code>/)
assert.match(markup, /Active subagents: 1/)

const choiceMarkup = renderToStaticMarkup(
  React.createElement(TaskUserInputPanel, {
    meta: {
      checkpoint_type: 'task_user_input',
      checkpoint_id: 'cp-choice',
      task_id: 'task-2',
      work_item_projection_title: 'Deployment Input',
      summary: 'Need a deployment decision.',
      prompt: 'Choose a region before continuing.',
      questions: ['Which deployment region should be used?'],
      input_questions: [
        {
          id: 'deployment_region',
          header: 'Deployment region',
          question: 'Which deployment region should I target?\n\n- Pick one if there is a clear preference.',
          options: [
            { id: 'a', label: 'US East', description: 'Use us-east-1' },
            { id: 'b', label: 'EU West', description: 'Use eu-west-1' },
            { id: 'c', label: 'Asia', description: 'Use ap-east-1' },
          ],
          allow_freeform: true,
          required: true,
        },
      ],
      required_fields: ['deployment_region'],
    },
    onReply: () => undefined,
    responded: false,
  }),
)

assert.match(choiceMarkup, /Deployment region/)
assert.match(choiceMarkup, /ckpt-choice-option/)
assert.match(choiceMarkup, /US East/)
assert.match(choiceMarkup, /EU West/)
assert.match(choiceMarkup, /Asia/)
assert.match(choiceMarkup, /Other/)
assert.match(choiceMarkup, /disabled=""/)

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'TaskUserInputPanel.tsx'), 'utf8')
assert.doesNotMatch(src, /localResponded|setLocalResponded/, 'panel must wait for server checkpoint metadata before showing responded state')
assert.match(src, /user_input_answers/, 'structured answers must be forwarded to the backend')

const messageListSrc = readFileSync(join(here, 'MessageList.tsx'), 'utf8')
const timelineIndex = messageListSrc.indexOf('{processed.map(row => (')
const progressIndex = messageListSrc.indexOf('{showProgressBlock && (')
const endIndex = messageListSrc.indexOf('<div className="msg-end-anchor" />')
assert.ok(timelineIndex !== -1 && progressIndex !== -1 && endIndex !== -1)
assert.ok(timelineIndex < progressIndex && progressIndex < endIndex)
assert.doesNotMatch(messageListSrc, /kind: 'pending-section'/, 'pending cards must not be moved into a second tail section')
assert.match(
  messageListSrc,
  /Checkpoint cards never leave the chronological transcript/,
  'task-user-input cards must remain at their creation position',
)

console.log('TaskUserInputPanel.test.tsx: OK (markdown and choice checkpoint panel)')
