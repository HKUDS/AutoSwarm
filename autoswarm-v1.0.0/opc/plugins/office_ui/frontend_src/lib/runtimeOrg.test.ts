import assert from 'node:assert/strict'
import { getRuntimeOrgView, normalizeOrgInfoPayload } from './runtimeOrg'

const payload = normalizeOrgInfoPayload({
  roles: [],
  employees: [],
  channels: [],
  connectors: [],
  company_profile: 'corporate',
  organization_id: 'quantum_harbor',
  organization_name: 'Quantum Harbor Research Studio',
  organization_config_file: 'company_orgs/org_quantum_harbor_config.yaml',
  org_version: 2,
  runtime_topology_version: 2,
  runtime_teams: [
    {
      cell_id: 'cell-2',
      manager_role_id: 'mgr-2',
      member_role_ids: ['role-b'],
      status: 'idle',
    },
  ],
  runtime_seats: [
    {
      role_session_id: 'seat-2',
      role_id: 'role-b',
      employee_id: 'emp-2',
      status: 'cold',
    },
  ],
  work_items: [
    {
      work_item_id: 'wi-2',
      role_id: 'role-b',
      cell_id: 'cell-2',
      title: 'Runtime work',
      kind: 'execute',
      phase: 'ready',
      metadata: {
        adaptive: {
          normalized_state: 'waiting_for_gate',
          blocked_reason: 'Waiting for required signals: implementation_ready',
        },
      },
    },
  ],
  frontier: {
    status: 'paused',
    total_work_items: 1,
  },
  project_run: {
    run_id: 'run-modern',
    lifecycle_status: 'deliverable',
    current_revision: 2,
  },
  project_dossier: {
    latest_deliverable_summary: 'Ship candidate ready',
    open_issues: ['Need QA sign-off'],
  },
  seat_digests: [
    {
      seat_id: 'seat-2',
      team_id: 'cell-2',
      manager_digest: {
        pending_decisions: [{ subject: 'Approve release' }],
      },
    },
  ],
  revision_links: [
    {
      link_id: 'link-1',
      session_id: 'session-new',
      linked_session_id: 'session-old',
      link_type: 'revision_of',
    },
  ],
})

assert.equal(payload.runtime_teams?.[0]?.cell_id, 'cell-2')
assert.equal(payload.runtime_seats?.[0]?.role_session_id, 'seat-2')
assert.equal(payload.work_items?.[0]?.work_item_id, 'wi-2')
assert.equal(payload.frontier?.status, 'paused')
assert.equal(payload.organization_id, 'quantum_harbor')
assert.equal(payload.organization_name, 'Quantum Harbor Research Studio')
assert.equal(payload.organization_config_file, 'company_orgs/org_quantum_harbor_config.yaml')
assert.equal(payload.project_run?.run_id, 'run-modern')
assert.equal(payload.project_dossier?.open_issues?.[0], 'Need QA sign-off')
assert.equal(payload.seat_digests?.[0]?.seat_id, 'seat-2')
assert.equal(payload.revision_links?.[0]?.link_type, 'revision_of')

const view = getRuntimeOrgView(payload)
assert.equal(view.runtimeTeams[0]?.cell_id, 'cell-2')
assert.equal(view.runtimeSeats[0]?.role_session_id, 'seat-2')
assert.equal(view.workItems[0]?.work_item_id, 'wi-2')
assert.equal(view.workItems[0]?.adaptive?.normalized_state, 'waiting_for_gate')
assert.equal(view.frontier.status, 'paused')
assert.equal(view.projectRun?.current_revision, 2)
assert.equal(view.projectDossier?.latest_deliverable_summary, 'Ship candidate ready')
assert.equal(view.seatDigests[0]?.seat_id, 'seat-2')
assert.equal(view.revisionLinks[0]?.link_type, 'revision_of')

console.log('runtimeOrg contract checks passed')
