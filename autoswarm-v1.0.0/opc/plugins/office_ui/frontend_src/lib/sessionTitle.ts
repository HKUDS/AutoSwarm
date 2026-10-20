function isCjkCodePoint(codePoint: number): boolean {
  return (
    (codePoint >= 0x3400 && codePoint <= 0x4dbf)
    || (codePoint >= 0x4e00 && codePoint <= 0x9fff)
    || (codePoint >= 0xf900 && codePoint <= 0xfaff)
    || (codePoint >= 0x3040 && codePoint <= 0x30ff)
    || (codePoint >= 0xac00 && codePoint <= 0xd7af)
  )
}

function isCjkChar(ch: string): boolean {
  const codePoint = ch.codePointAt(0)
  return typeof codePoint === 'number' && isCjkCodePoint(codePoint)
}

function isWordChar(ch: string): boolean {
  return !isCjkChar(ch) && /^[\p{L}\p{N}_]$/u.test(ch)
}

export function compactSessionTitle(input: string, maxUnits = 10, fallback = 'New Chat'): string {
  const text = String(input || '').replace(/\s+/g, ' ').trim()
  if (!text || maxUnits <= 0) return fallback

  let units = 0
  let index = 0
  let cutIndex = text.length

  while (index < text.length) {
    const codePoint = text.codePointAt(index)
    if (typeof codePoint !== 'number') break
    const ch = String.fromCodePoint(codePoint)
    if (/\s/.test(ch)) {
      index += ch.length
      continue
    }
    if (isCjkChar(ch)) {
      units += 1
      index += ch.length
      if (units === maxUnits) {
        cutIndex = index
        break
      }
      continue
    }
    if (isWordChar(ch)) {
      while (index < text.length) {
        const innerCodePoint = text.codePointAt(index)
        if (typeof innerCodePoint !== 'number') break
        const innerChar = String.fromCodePoint(innerCodePoint)
        if (!isWordChar(innerChar)) break
        index += innerChar.length
      }
      units += 1
      if (units === maxUnits) {
        cutIndex = index
        break
      }
      continue
    }
    index += ch.length
  }

  const compact = text.slice(0, cutIndex).trim() || fallback
  const hasMoreUnits = Array.from(text.slice(cutIndex)).some(ch => isCjkChar(ch) || isWordChar(ch))
  return hasMoreUnits ? `${compact}...` : compact
}
