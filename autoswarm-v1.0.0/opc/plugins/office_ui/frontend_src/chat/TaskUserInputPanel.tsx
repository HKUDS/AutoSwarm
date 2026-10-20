import React, { useCallback, useMemo, useState } from 'react'
import type {
  ChatMessageMeta,
  CheckpointReplyMetadata,
  TaskUserInputAnswer,
  TaskUserInputQuestion,
} from '../types/chat'
import { MarkdownBody } from './MarkdownBody'

interface TaskUserInputPanelProps {
  meta: ChatMessageMeta
  onReply: (text: string, metadata?: Partial<CheckpointReplyMetadata>) => void
  responded: boolean
}

interface QuestionState {
  selectedOptionId?: string
  freeformText: string
}

const OTHER_OPTION_ID = '__other__'
const OPTION_LETTERS = ['A', 'B', 'C']

function cleanText(value: unknown): string {
  return String(value ?? '').trim()
}

function normalizeQuestion(raw: TaskUserInputQuestion, index: number): TaskUserInputQuestion | null {
  const question = cleanText(raw.question)
  const header = cleanText(raw.header)
  if (!question && !header) return null
  const options = (raw.options ?? [])
    .slice(0, 3)
    .map((option, optionIndex) => ({
      id: cleanText(option.id) || String.fromCharCode(97 + optionIndex),
      label: cleanText(option.label),
      description: cleanText(option.description),
    }))
    .filter((option) => option.label)
  return {
    id: cleanText(raw.id) || `question_${index + 1}`,
    header,
    question: question || header,
    options,
    allow_freeform: raw.allow_freeform !== false,
    required: raw.required !== false,
  }
}

export const TaskUserInputPanel = React.memo(function TaskUserInputPanel({
  meta, onReply, responded,
}: TaskUserInputPanelProps) {
  const [reply, setReply] = useState('')
  const [answers, setAnswers] = useState<Record<string, QuestionState>>({})
  const isResponded = responded

  const title = String(meta.work_item_projection_title ?? meta.work_item_projection_id ?? 'Input Needed').trim() || 'Input Needed'
  const summary = String(meta.summary ?? '').trim()
  const prompt = String(meta.prompt ?? '').trim()
  const contextNote = String(meta.context_note ?? '').trim()
  const resumeHint = String(meta.resume_hint ?? '').trim()
  const questions = useMemo(
    () => (meta.questions ?? []).map((item) => String(item).trim()).filter(Boolean),
    [meta.questions],
  )
  const inputQuestions = useMemo(
    () => (meta.input_questions ?? [])
      .map((item, index) => normalizeQuestion(item, index))
      .filter((item): item is TaskUserInputQuestion => item !== null),
    [meta.input_questions],
  )
  const usesChoiceMode = inputQuestions.some((question) => (question.options ?? []).length > 0)
  const requiredFields = useMemo(
    () => (meta.required_fields ?? []).map((item) => String(item).trim()).filter(Boolean),
    [meta.required_fields],
  )
  const activeSubagents = useMemo(
    () => (meta.active_subagents ?? []).filter((item) => !!item && typeof item === 'object'),
    [meta.active_subagents],
  )
  const permissionRequests = useMemo(
    () => (meta.permission_requests ?? []).filter((item) => !!item && typeof item === 'object'),
    [meta.permission_requests],
  )
  const worktreePath = String(meta.worktree_path ?? '').trim()
  const requestingRoleId = String(meta.requesting_role_id ?? '').trim()
  const requestingTaskId = String(meta.requesting_task_id ?? '').trim()
  const requestingWorkItemId = String(meta.requesting_work_item_id ?? '').trim()
  const seatId = String(meta.seat_id ?? '').trim()
  const hasRequesterState = !!requestingRoleId || !!requestingTaskId || !!requestingWorkItemId || !!seatId
  const hasRuntimeState = hasRequesterState || activeSubagents.length > 0 || permissionRequests.length > 0 || !!worktreePath

  const setSelectedOption = useCallback((questionId: string, optionId: string) => {
    setAnswers((current) => ({
      ...current,
      [questionId]: {
        freeformText: current[questionId]?.freeformText ?? '',
        selectedOptionId: optionId,
      },
    }))
  }, [])

  const setFreeformAnswer = useCallback((questionId: string, value: string) => {
    setAnswers((current) => ({
      ...current,
      [questionId]: {
        selectedOptionId: current[questionId]?.selectedOptionId,
        freeformText: value,
      },
    }))
  }, [])

  const questionComplete = useCallback((question: TaskUserInputQuestion) => {
    if (question.required === false) return true
    const state = answers[question.id]
    const selected = state?.selectedOptionId
    if (selected && selected !== OTHER_OPTION_ID) return true
    if (question.allow_freeform !== false && cleanText(state?.freeformText)) return true
    return false
  }, [answers])

  const canSubmitStructured = usesChoiceMode && inputQuestions.every(questionComplete)

  const handleSubmitLegacy = useCallback(() => {
    const text = reply.trim()
    if (isResponded || !text) return
    onReply(text)
  }, [isResponded, onReply, reply])

  const handleSubmitStructured = useCallback(() => {
    if (isResponded || !canSubmitStructured) return
    const answerMetadata: Record<string, TaskUserInputAnswer> = {}
    const lines: string[] = []
    inputQuestions.forEach((question) => {
      const state = answers[question.id] ?? { freeformText: '' }
      const selectedOption = (question.options ?? []).find((option) => option.id === state.selectedOptionId)
      const freeformText = cleanText(state.freeformText)
      const label = selectedOption?.label ?? ''
      const answerText = [label, freeformText].filter(Boolean).join('; ')
      answerMetadata[question.id] = {
        question_id: question.id,
        question: question.question,
        ...(selectedOption ? {
          selected_option_id: selectedOption.id,
          selected_label: selectedOption.label,
        } : {}),
        ...(freeformText ? { freeform_text: freeformText } : {}),
        answer_text: answerText,
      }
      const displayQuestion = cleanText(question.header) || cleanText(question.question) || question.id
      lines.push(`- ${displayQuestion}: ${answerText || '(no answer)'}`)
    })
    onReply(lines.join('\n'), { user_input_answers: answerMetadata })
  }, [answers, canSubmitStructured, inputQuestions, isResponded, onReply])

  return (
    <div className="ckpt-panel ckpt-user-input">
      <div className="ckpt-header">
        <div className="ckpt-icon ckpt-icon-user-input">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3.5h10v6H6.5L3 13V3.5Z" />
            <path d="M5.5 6h5" />
            <path d="M5.5 8h3.5" />
          </svg>
        </div>
        <div className="ckpt-title">{title}</div>
        <span className="ckpt-badge ckpt-badge-scope">awaiting input</span>
        {isResponded && <span className="ckpt-badge ckpt-badge-responded">Responded</span>}
      </div>

      {summary && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Summary</div>
          <MarkdownBody content={summary} className="ckpt-markdown" />
        </div>
      )}
      {prompt && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Request</div>
          <MarkdownBody content={prompt} className="ckpt-markdown" />
        </div>
      )}

      {usesChoiceMode ? (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Questions</div>
          <div className="ckpt-choice-list">
            {inputQuestions.map((question) => {
              const state = answers[question.id] ?? { freeformText: '' }
              const options = question.options ?? []
              const showOther = question.allow_freeform !== false
              const showOtherInput = showOther && (options.length === 0 || state.selectedOptionId === OTHER_OPTION_ID)
              return (
                <div className="ckpt-choice-question" key={question.id}>
                  {question.header && <div className="ckpt-question-header">{question.header}</div>}
                  <MarkdownBody content={question.question} className="ckpt-markdown" />
                  {options.length > 0 && (
                    <div className="ckpt-choice-grid">
                      {options.map((option, optionIndex) => {
                        const selected = state.selectedOptionId === option.id
                        return (
                          <button
                            key={option.id}
                            className={`ckpt-choice-option${selected ? ' is-selected' : ''}`}
                            onClick={() => setSelectedOption(question.id, option.id)}
                          >
                            <span className="ckpt-choice-letter">{OPTION_LETTERS[optionIndex] ?? '?'}</span>
                            <span className="ckpt-choice-copy">
                              <span className="ckpt-choice-label">{option.label}</span>
                              {option.description && <span className="ckpt-choice-desc">{option.description}</span>}
                            </span>
                          </button>
                        )
                      })}
                      {showOther && (
                        <button
                          className={`ckpt-choice-option${state.selectedOptionId === OTHER_OPTION_ID ? ' is-selected' : ''}`}
                          onClick={() => setSelectedOption(question.id, OTHER_OPTION_ID)}
                        >
                          <span className="ckpt-choice-letter">D</span>
                          <span className="ckpt-choice-copy">
                            <span className="ckpt-choice-label">Other</span>
                            <span className="ckpt-choice-desc">Enter a custom answer</span>
                          </span>
                        </button>
                      )}
                    </div>
                  )}
                  {showOtherInput && (
                    <textarea
                      className="ckpt-feedback-input ckpt-other-field"
                      placeholder="Type your answer..."
                      value={state.freeformText}
                      onChange={(event) => setFreeformAnswer(question.id, event.target.value)}
                      rows={3}
                    />
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : questions.length > 0 && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Questions</div>
          <ul className="ckpt-task-list">
            {questions.map((question) => <li key={question}>{question}</li>)}
          </ul>
        </div>
      )}

      {requiredFields.length > 0 && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Required Fields</div>
          <div className="ckpt-task-tags">
            {requiredFields.map((field) => <span key={field} className="ckpt-field-tag">{field}</span>)}
          </div>
        </div>
      )}

      {contextNote && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Context</div>
          <MarkdownBody content={contextNote} className="ckpt-markdown ckpt-markdown-muted" />
        </div>
      )}
      {resumeHint && <div className="ckpt-escalation-hint">{resumeHint}</div>}

      {hasRuntimeState && (
        <details className="ckpt-runtime-details">
          <summary>Runtime State</summary>
          <div className="ckpt-runtime-body">
            {requestingRoleId && <div>Requester: <code>{requestingRoleId}</code></div>}
            {requestingWorkItemId && <div>Work item: <code>{requestingWorkItemId}</code></div>}
            {requestingTaskId && <div>Task: <code>{requestingTaskId}</code></div>}
            {seatId && <div>Seat: <code>{seatId}</code></div>}
            {worktreePath && <div>Worktree: <code>{worktreePath}</code></div>}
            {activeSubagents.length > 0 && <div>Active subagents: {activeSubagents.length}</div>}
            {permissionRequests.length > 0 && <div>Pending permission records: {permissionRequests.length}</div>}
          </div>
        </details>
      )}

      {!isResponded && (
        <div className="ckpt-feedback-area">
          {usesChoiceMode ? null : (
            <textarea
              className="ckpt-feedback-input"
              placeholder="Reply with the missing input to continue..."
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              rows={3}
            />
          )}
          <div className="ckpt-feedback-btns">
            <button
              className="ckpt-btn ckpt-btn-approve"
              onClick={usesChoiceMode ? handleSubmitStructured : handleSubmitLegacy}
              disabled={usesChoiceMode ? !canSubmitStructured : !reply.trim()}
            >
              Send Reply
            </button>
          </div>
        </div>
      )}
    </div>
  )
})
