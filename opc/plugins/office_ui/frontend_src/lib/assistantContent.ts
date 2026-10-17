export interface AssistantContentPart {
  kind: 'markdown' | 'thinking'
  content: string
  complete: boolean
}

/** Recognise provider reasoning tags without interpreting examples inside code. */
export function splitAssistantContent(content: string): AssistantContentPart[] {
  const parts: AssistantContentPart[] = []
  const tokens = /^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)|`+|<\/?think>/gim
  let fence = ''
  let start = 0
  let thinking = false
  let match: RegExpExecArray | null
  while ((match = tokens.exec(content))) {
    if (match[1]) {
      const marker = match[1]
      if (!fence) fence = marker
      else if (marker[0] === fence[0] && marker.length >= fence.length
        && match[0].slice(match[0].indexOf(marker) + marker.length).trim() === '') fence = ''
      continue
    }
    if (fence) continue
    if (match[0][0] === '`') {
      const delimiter = match[0]
      const closing = /`+/g
      closing.lastIndex = tokens.lastIndex
      let end: RegExpExecArray | null
      while ((end = closing.exec(content))) {
        if (end[0] === delimiter) {
          tokens.lastIndex = closing.lastIndex
          break
        }
      }
      continue
    }
    const opening = match[0].toLowerCase() === '<think>'
    if (opening === thinking) continue
    if (match.index > start) {
      parts.push({ kind: thinking ? 'thinking' : 'markdown', content: content.slice(start, match.index), complete: true })
    }
    thinking = opening
    start = tokens.lastIndex
  }
  if (start < content.length || thinking) {
    parts.push({ kind: thinking ? 'thinking' : 'markdown', content: content.slice(start), complete: !thinking })
  }
  return parts
}
