import React from 'react'
import { createRoot } from 'react-dom/client'

import '../index.css'
import { MessageList } from '../chat/MessageList'

const fallback = [{ content: '**Review #1: Evidence**: <think>Read the evidence.\n\n- Verify files.\n- Check sources.\n</think>\n\n## Verification Summary\n\nFiles verified.\n\n| Criterion | Met? |\n| --- | --- |\n| Sources | Yes |\n\n```json\n{"summary":"All criteria met.","review_verdict":"approve"}\n```' }]
const evidence: Array<{ content: string }> = await fetch('/native-markdown-evidence.json').then(response => response.json())
const messages = (evidence.length ? evidence : fallback).map((row, index) => ({
  id: `native-format-${index}`,
  channelId: 'session:native-format',
  sender: 'research-lead',
  senderName: 'Research Lead',
  content: row.content,
  timestamp: Date.UTC(2026, 9, 16, 20, index),
  mentions: [],
  metadata: {},
}))
createRoot(document.getElementById('root')!).render(
  <div className="app-shell" data-theme="midnight" style={{ display: 'flex', padding: 24 }}>
    <MessageList messages={messages} channelName="Native formatting" detailMode="full" scrollPolicy="manual" />
  </div>,
)
