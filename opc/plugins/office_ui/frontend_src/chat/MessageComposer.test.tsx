import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MessageComposer } from './MessageComposer'

const savedOrgs = [
  { name: 'vc-investment-firm', organization_name: 'VC Investment Firm', saved_at: 0, roles_count: 21, employees_count: 0 },
  { name: 'game-development-studio', organization_name: 'Game Development Studio', saved_at: 0, roles_count: 5, employees_count: 0 },
]

test('Company offers adaptive formation and fixed organizations before sending', () => {
  const markup = renderToStaticMarkup(<MessageComposer execMode="company" companyProfile="corporate"
    companyTeamStrategy="adaptive" taskPreferredAgent="codex" savedOrgs={savedOrgs}
    onSend={() => undefined} onModeChange={() => undefined} onTaskAgentChange={() => undefined} />)
  assert.match(markup, /value="adaptive" selected=""/)
  assert.match(markup, /label="Fixed organization"/)
  assert.match(markup, /VC Investment Firm/)
  assert.match(markup, /Game Development Studio/)
  assert.doesNotMatch(markup, /Team planning agent|Planner/)
  assert.match(markup, /aria-label="Execution agent"/)
  assert.match(markup, /value="codex" selected=""/)
})

test('Fixed organization uses the same execution agent selector', () => {
  const markup = renderToStaticMarkup(<MessageComposer execMode="org" companyProfile="custom"
    companyTeamStrategy="fixed" selectedOrgId="vc-investment-firm" savedOrgs={savedOrgs}
    taskPreferredAgent="codex" onTaskAgentChange={() => undefined}
    onSend={() => undefined} onModeChange={() => undefined} />)
  assert.match(markup, /value="org:vc-investment-firm" selected=""/)
  assert.doesNotMatch(markup, /aria-label="Team planning agent"/)
  assert.match(markup, /aria-label="Execution agent"/)
  assert.match(markup, /value="codex" selected=""/)
})

test('Locked adaptive chats keep their strategy label after compiling into a custom org', () => {
  const markup = renderToStaticMarkup(<MessageComposer execMode="org" companyProfile="custom"
    companyTeamStrategy="adaptive" selectedOrgId="adaptive_generated" lockedMode
    onSend={() => undefined} onModeChange={() => undefined} />)
  assert.match(markup, /Company \/ Adaptive team/)
  assert.doesNotMatch(markup, /aria-label="Company architecture"/)
  assert.doesNotMatch(markup, /aria-label="Team planning agent"/)
})
