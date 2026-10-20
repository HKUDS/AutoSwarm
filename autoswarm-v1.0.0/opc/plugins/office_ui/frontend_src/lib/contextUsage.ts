export interface ContextUsageLike {
  contextTokens?: number | null
  contextWindow?: number | null
  contextRemainingPct?: number | null
}

export interface ContextUsageMetrics {
  usedPct?: number
  remainingPct?: number
  usedTokens?: number
  windowTokens?: number
}

function asFiniteNumber(value: number | null | undefined): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return value
}

function clampPct(value: number): number {
  return Math.max(0, Math.min(Math.round(value), 100))
}

export function getContextUsageMetrics(
  value: ContextUsageLike | null | undefined,
): ContextUsageMetrics {
  const contextTokens = asFiniteNumber(value?.contextTokens)
  const contextWindow = asFiniteNumber(value?.contextWindow)
  const contextRemainingPct = asFiniteNumber(value?.contextRemainingPct)

  const normalizedTokens = typeof contextTokens === 'number' ? Math.max(0, Math.round(contextTokens)) : undefined
  const normalizedWindow = typeof contextWindow === 'number' && contextWindow > 0
    ? Math.max(1, Math.round(contextWindow))
    : undefined
  const normalizedRemainingPct = typeof contextRemainingPct === 'number'
    ? clampPct(contextRemainingPct)
    : undefined

  if (typeof normalizedTokens === 'number' && typeof normalizedWindow === 'number') {
    const usedTokens = Math.min(normalizedTokens, normalizedWindow)
    const usedPct = clampPct((usedTokens / normalizedWindow) * 100)
    return {
      usedPct,
      remainingPct: 100 - usedPct,
      usedTokens,
      windowTokens: normalizedWindow,
    }
  }

  if (typeof normalizedRemainingPct === 'number' && typeof normalizedWindow === 'number') {
    const usedPct = 100 - normalizedRemainingPct
    return {
      usedPct,
      remainingPct: normalizedRemainingPct,
      usedTokens: Math.round((usedPct / 100) * normalizedWindow),
      windowTokens: normalizedWindow,
    }
  }

  if (typeof normalizedWindow === 'number') {
    return {
      usedPct: 0,
      remainingPct: 100,
      usedTokens: 0,
      windowTokens: normalizedWindow,
    }
  }

  if (typeof normalizedRemainingPct === 'number') {
    // No usable window: the used/max ratio is undefined. Upstream reports an
    // unknown window as remaining_pct=0, so deriving usedPct here would render
    // a misleading 100% (or 0% at turn start). Surface remaining for any text
    // display but do not drive the ring — the ring hides without a usedPct.
    return { remainingPct: normalizedRemainingPct }
  }

  if (typeof normalizedTokens === 'number') {
    return { usedTokens: normalizedTokens }
  }

  return {}
}
