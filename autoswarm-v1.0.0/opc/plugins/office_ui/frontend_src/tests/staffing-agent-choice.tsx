import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { StaffingSelectionPanel } from '../chat/StaffingSelectionPanel'
import { RecruitmentPanel } from '../chat/RecruitmentPanel'
import type { ChatMessageMeta, CheckpointReplyMetadata } from '../types/chat'

const query = new URLSearchParams(location.search)
const recruitment = query.has('recruitment')
const fallback: ChatMessageMeta = {
  checkpoint_id: 'choice-regression',
  checkpoint_type: recruitment ? 'company_recruitment_confirmation' : 'company_staffing_selection',
  staffing_roles: [
    { role_id: 'ceo', role_label: 'CEO', reports_to: 'owner', selected_agent: 'native' },
    { role_id: 'cto', role_label: 'CTO', reports_to: 'ceo', selected_agent: 'jiuwenswarm' },
    { role_id: 'engineer', role_label: 'Engineer', reports_to: 'cto', selected_agent: 'codex' },
    { role_id: 'qa', role_label: 'QA', reports_to: 'cto', selected_agent: 'native' },
  ],
  staffing_pool: { employees: [], templates: [] },
}
const meta: ChatMessageMeta = query.has('evidence')
  ? await fetch('/staffing-evidence.json').then(response => response.json())
  : fallback

function Fixture() {
  const [reply, setReply] = useState<CheckpointReplyMetadata>()
  const onReply = async (_text: string, metadata?: CheckpointReplyMetadata) => {
    setReply(metadata)
    return true
  }
  return <>
    {recruitment
      ? <RecruitmentPanel meta={meta} responded={Boolean(reply)} onReply={onReply} />
      : <StaffingSelectionPanel meta={meta} responded={Boolean(reply)} onReply={onReply} />}
    <pre id="submitted">{JSON.stringify(reply)}</pre>
  </>
}
createRoot(document.getElementById('root')!).render(<Fixture />)
