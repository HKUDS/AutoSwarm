import { useEffect, useMemo, useState } from 'react'
import type { OrgCreateMemberInput, OrgSavedCreatePayload } from '../types/visual'

interface OrgCreateResult extends OrgSavedCreatePayload {
  nonce: number
}

interface OrgCreateModalProps {
  open: boolean
  pending?: boolean
  result?: OrgCreateResult | null
  onClose: () => void
  onCreate: (organizationName: string, members: OrgCreateMemberInput[]) => void
}

type MemberDraft = {
  name: string
  responsibility: string
  prompt: string
  reportsToIndex: number | null
}

const INITIAL_MEMBERS: MemberDraft[] = [
  { name: '', responsibility: '', prompt: '', reportsToIndex: null },
  { name: '', responsibility: '', prompt: '', reportsToIndex: 0 },
]

function slugLabel(value: string): string {
  return value.trim() || 'Member'
}

export function OrgCreateModal({ open, pending, result, onClose, onCreate }: OrgCreateModalProps) {
  const [step, setStep] = useState(1)
  const [organizationName, setOrganizationName] = useState('')
  const [members, setMembers] = useState<MemberDraft[]>(INITIAL_MEMBERS)
  const [localError, setLocalError] = useState('')

  useEffect(() => {
    if (!open) return
    setStep(1)
    setOrganizationName('')
    setMembers(INITIAL_MEMBERS)
    setLocalError('')
  }, [open])

  useEffect(() => {
    if (!open || !result) return
    if (result.ok) {
      onClose()
      return
    }
    setLocalError(result.error || 'Failed to create organization')
  }, [open, result, onClose])

  const organizationValid = organizationName.trim().length > 0
  const validMembers = useMemo(
    () => members.map((member, index) => ({ member, index })).filter(item => item.member.name.trim()),
    [members],
  )
  const originalIndexToCreateIndex = useMemo(
    () => new Map(validMembers.map((item, createIndex) => [item.index, createIndex])),
    [validMembers],
  )
  const membersValid = validMembers.length >= 2
  const canCreate = organizationValid && membersValid && !pending

  const previewMembers = useMemo(
    () => validMembers.map(({ member, index }, createIndex) => {
      const mappedParent = member.reportsToIndex == null ? null : originalIndexToCreateIndex.get(member.reportsToIndex)
      return {
        ...member,
        roleName: slugLabel(member.name),
        managerName: mappedParent != null && mappedParent < createIndex
          ? slugLabel(validMembers[mappedParent]?.member.name || '')
          : 'Owner',
        index,
      }
    }),
    [originalIndexToCreateIndex, validMembers],
  )

  if (!open) return null

  const updateMember = (index: number, patch: Partial<MemberDraft>) => {
    setMembers(prev => prev.map((member, idx) => idx === index ? { ...member, ...patch } : member))
  }

  const addMember = () => {
    setMembers(prev => [...prev, { name: '', responsibility: '', prompt: '', reportsToIndex: 0 }])
  }

  const removeMember = (index: number) => {
    setMembers(prev => {
      const next = prev.filter((_, idx) => idx !== index)
      return next.map((member, idx) => ({
        ...member,
        reportsToIndex: member.reportsToIndex == null
          ? null
          : member.reportsToIndex >= index
            ? Math.max(0, member.reportsToIndex - 1)
            : member.reportsToIndex,
      })).map((member, idx) => idx === 0 ? { ...member, reportsToIndex: null } : member)
    })
  }

  const submit = () => {
    if (!canCreate) return
    setLocalError('')
    onCreate(
      organizationName.trim(),
      validMembers.map(({ member }, createIndex) => {
        const mappedParent = member.reportsToIndex == null ? null : originalIndexToCreateIndex.get(member.reportsToIndex)
        return {
          name: member.name.trim(),
          responsibility: member.responsibility.trim(),
          prompt: member.prompt.trim(),
          reports_to_index: mappedParent != null && mappedParent < createIndex
            ? mappedParent
            : createIndex === 0 ? null : 0,
        }
      }),
    )
  }

  return (
    <div className="org-create-backdrop" role="presentation" onMouseDown={onClose}>
      <div className="org-create-modal" role="dialog" aria-modal="true" aria-labelledby="org-create-title" onMouseDown={e => e.stopPropagation()}>
        <div className="org-create-header">
          <div>
            <span className="org-create-eyebrow">New organization</span>
            <h3 id="org-create-title" className="org-create-title">Create a saved org</h3>
          </div>
          <button type="button" className="org-create-close" onClick={onClose} aria-label="Close">x</button>
        </div>

        <div className="org-create-steps" aria-label="Create organization steps">
          {[
            ['1', 'Name'],
            ['2', 'Members'],
            ['3', 'Review'],
          ].map(([id, label]) => (
            <span key={id} className={`org-create-step${step === Number(id) ? ' org-create-step--active' : step > Number(id) ? ' org-create-step--done' : ''}`}>
              <span>{id}</span>{label}
            </span>
          ))}
        </div>

        {step === 1 && (
          <div className="org-create-panel">
            <label className="org-create-field">
              <span>Organization name</span>
              <input
                value={organizationName}
                onChange={e => setOrganizationName(e.target.value)}
                placeholder="HKU Research Lab"
                autoFocus
              />
            </label>
          </div>
        )}

        {step === 2 && (
          <div className="org-create-panel">
            <div className="org-create-member-list">
              {members.map((member, index) => (
                <div className="org-create-member-row" key={index}>
                  <input
                    value={member.name}
                    onChange={e => updateMember(index, { name: e.target.value })}
                    placeholder={index === 0 ? 'Lead role' : 'Member role'}
                  />
                  <input
                    value={member.responsibility}
                    onChange={e => updateMember(index, { responsibility: e.target.value })}
                    placeholder="Responsibility"
                  />
                  <select
                    value={member.reportsToIndex == null ? 'owner' : String(member.reportsToIndex)}
                    onChange={e => updateMember(index, { reportsToIndex: e.target.value === 'owner' ? null : Number(e.target.value) })}
                    disabled={index === 0}
                    aria-label="Reports to"
                  >
                    <option value="owner">Owner</option>
                    {members.slice(0, index).map((candidate, candidateIndex) => (
                      <option key={candidateIndex} value={candidateIndex}>
                        {slugLabel(candidate.name)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="org-create-icon-btn"
                    onClick={() => removeMember(index)}
                    disabled={members.length <= 2}
                    title="Remove member"
                    aria-label="Remove member"
                  >
                    -
                  </button>
                  <textarea
                    value={member.prompt}
                    onChange={e => updateMember(index, { prompt: e.target.value })}
                    placeholder="Prompt optional"
                    aria-label={`${index === 0 ? 'Lead role' : 'Member role'} prompt optional`}
                  />
                </div>
              ))}
            </div>
            <button type="button" className="org-create-add" onClick={addMember}>+ Add member</button>
          </div>
        )}

        {step === 3 && (
          <div className="org-create-panel">
            <div className="org-create-review">
              <div className="org-create-review-head">
                <span>{organizationName.trim()}</span>
                <b>{validMembers.length} members</b>
              </div>
              {previewMembers.map(member => (
                <div className="org-create-review-row" key={member.index}>
                  <strong>{member.roleName}</strong>
                  <span>
                    {member.managerName}
                    {member.prompt.trim() ? <em>Prompt</em> : null}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {localError && <div className="org-create-error">{localError}</div>}

        <div className="org-create-actions">
          <button type="button" className="btn btn-ghost" onClick={step === 1 ? onClose : () => setStep(step - 1)}>
            {step === 1 ? 'Cancel' : 'Back'}
          </button>
          {step < 3 ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setStep(step + 1)}
              disabled={step === 1 ? !organizationValid : !membersValid}
            >
              Next
            </button>
          ) : (
            <button type="button" className="btn btn-primary" onClick={submit} disabled={!canCreate}>
              {pending ? 'Creating...' : 'Create organization'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
