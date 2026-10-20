/**
 * Structural regression test for App.tsx WS handler registrations.
 *
 * Guards the 5 onOrgSaved* callbacks that MUST all be registered in the
 * socket-handlers object. A missing one is silent (TypeScript-level the
 * callback is optional) and results in broken UX: earlier bug report
 * "can't switch saved orgs" was caused by the 3 of these being absent.
 *
 * Also guards:
 * - Toast state wiring
 * - useCallback stability for client.org* calls (no inline arrows in
 *   <OrgTab> props)
 *
 * Runs with `tsx` against node:assert/strict — matches repo convention
 * for zero-framework tests.
 *
 * Usage:
 *   tsx opc/plugins/office_ui/frontend_src/App.test.tsx
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'App.tsx'), 'utf8')

// 1. All five onOrgSaved* handlers registered in the socket-handlers object.
for (const key of [
  'onOrgSavedList',
  'onOrgSavedSaveAs',
  'onOrgSavedCreate',
  'onOrgSavedLoad',
  'onOrgSavedDelete',
]) {
  assert.match(
    src,
    new RegExp(`^\\s+${key}:\\s*\\(payload\\)`, 'm'),
    `App.tsx must register "${key}:" in the socket handlers object`,
  )
}

// 2. Toast state + JSX render present.
assert.match(src, /const \[orgToast, setOrgToast\] = useState/, 'orgToast state must exist')
assert.match(src, /setOrgToast\(null\)/, 'orgToast auto-clear must exist')
assert.match(src, /org-toast org-toast--/, 'org-toast JSX must render class variant')

// 3. versionAtLoad tracking wired in onOrgInfo.
assert.match(
  src,
  /setSavedOrgVersionAtLoad\(prev =>/,
  'onOrgInfo must capture versionAtLoad via functional setState',
)

// 4. useCallback for client.org* — no inline arrows in <OrgTab> props.
assert.doesNotMatch(
  src,
  /onSavedOrg[A-Z][a-zA-Z]*={\(/,
  'OrgTab JSX must not use inline arrow functions for onSavedOrg* props',
)
for (const name of [
  'handleSavedOrgsList',
  'handleSavedOrgSaveAs',
  'handleSavedOrgCreate',
  'handleSavedOrgLoad',
  'handleSavedOrgDelete',
]) {
  assert.match(
    src,
    new RegExp(`const ${name} = useCallback`),
    `App.tsx must declare "${name}" as useCallback`,
  )
}

// 5. onOrgConfigImport narrowing comment present.
assert.match(
  src,
  /Fires only on manual YAML import/,
  'onOrgConfigImport must carry the narrowing comment',
)

// 6. project_index_push is an index-only seed. It must not hydrate chat,
// kanban, or full runtime stores; full runtime state belongs to collab_sync.
assert.match(
  src,
  /const isProjectIndexPush = type === 'project_index_push' \|\| syncScope === 'index'/,
  'project_index_push must be detected by event type and sync_scope',
)
assert.match(
  src,
  /if \(isProjectIndexPush\) \{[\s\S]*?preserveExistingWhenIncomingPartial: true[\s\S]*?clientRef\.current\?\.collabSync\(syncProjectId[\s\S]*?return[\s\S]*?\}\s+const cs2 = chatStoreRef\.current/,
  'project_index_push must preserve existing session detail, request full collab_sync, and return before chat/kanban hydration',
)
assert.doesNotMatch(
  src,
  /preserveTasksWhenIncomingEmpty: isProjectIndexPush/,
  'project_index_push must not call BoardStore.initFromBackend as a partial full-sync workaround',
)

// 7. Runtime tool display has two channels: currentTool is active-only,
// displayTool is the stable "last visible command" shown while the session
// remains running. This prevents a half-second header/composer flash when
// current_tool is cleared by tool_completed.
assert.match(
  src,
  /function runtimeStatusClearsDisplayTool/,
  'App.tsx must centralize terminal status clearing for stable displayTool',
)
assert.match(
  src,
  /boardRuntimePatch\.displayTool = currentTool/,
  'agent_runtime_update must copy a non-empty current_tool into board displayTool',
)
assert.match(
  src,
  /sessionRuntimePatch\.displayTool = currentTool/,
  'agent_runtime_update must copy a non-empty current_tool into session displayTool',
)
assert.match(
  src,
  /runtimeStatusClearsDisplayTool\(payload\.status\)/,
  'agent_runtime_update must clear displayTool only on terminal or idle statuses',
)
assert.match(
  src,
  /toolName \? \{ displayTool: toolName \}/,
  'runtime events carrying a non-empty tool_name must update stable displayTool (empty tool_name keeps the sticky last command)',
)

// 8. A streaming draft is one mounted logical turn. Completion/checkpoint
// events cannot remove it before the matching persisted final is merged.
assert.match(
  src,
  /const turnId = resolveCanonicalTurnId\(data\) \|\| undefined/,
  'runtime deltas must use the shared canonical-turn resolver',
)
assert.match(
  src,
  /const startsNewCanonicalTurn = evt\.type === 'turn_started'[\s\S]*turnId !== activeDraftTurnId[\s\S]*evt\.type === 'turn_failed' \|\| startsNewCanonicalTurn/,
  'only failure or a genuinely new canonical turn may clear an uncommitted draft',
)
assert.doesNotMatch(
  src,
  /evt\.type === 'turn_completed'[^\n]*clearDraft|evt\.type === 'checkpoint_saved'[^\n]*clearDraft/,
  'turn completion and checkpoint persistence must not clear a draft ahead of its final message',
)
assert.match(
  src,
  /detailHasFinalForDraft[\s\S]*terminalAssistantTurnId\(message\) === draftTurnId[\s\S]*ss\.clearDraft\(detailTaskId\)/,
  'session_detail may clear only after merging the matching terminal assistant turn',
)
assert.match(
  src,
  /cs\.addMessageFromBackend\(mapped\)[\s\S]*terminalTurnId = terminalAssistantTurnId\(mapped\)[\s\S]*terminalTurnId === activeDraftTurnId[\s\S]*clearDraft\(taskId\)/,
  'session_message/chat_new_message must merge a matching terminal final before clearing its draft',
)
assert.doesNotMatch(
  src,
  /mapped\.sender !== 'user'\)\s*\{\s*sessionStoreRef\.current\?\.clearDraft/,
  'an unrelated non-user session message must never clear an active draft',
)

// 9. Summary/full pagination and transport-local failures have independent
// lifecycle state. A locally failed Promise never reaches onAck.
assert.match(
  src,
  /void client\.sessionDetail\([\s\S]*?\.then\(\(payload\) => \{[\s\S]*?payload\.ok !== false[\s\S]*?detailLoading: false/,
  'debounced session_detail refresh must clear loading on transport-local failure',
)
assert.match(
  src,
  /mergeSessionDetailHasMore\([\s\S]*?payload\.client_history_page === true[\s\S]*?fullHasMore: detailHasMore[\s\S]*?summaryHasMore: detailHasMore/,
  'session_detail ACK must persist pagination state under its detail policy',
)

console.log('App.test.tsx: OK (org handlers + snapshot boundary + runtime displayTool/draft contract)')
