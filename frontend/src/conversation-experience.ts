export type DraftDecision = 'ignore' | 'apply' | 'confirm'

export type ScrollMetrics = { scrollHeight: number; scrollTop: number; clientHeight: number }

export const NEAR_LATEST_THRESHOLD = 72
const MAX_METRIC = 1e9

// Decide how a suggested reply interacts with the user's current draft.
// Trimming is used only for comparison; callers keep the original strings.
export function draftDecision(current: string, suggestion: string): DraftDecision {
  const s = typeof suggestion === 'string' ? suggestion.trim() : ''
  if (s === '') return 'ignore'
  const c = typeof current === 'string' ? current.trim() : ''
  if (c === '' || c === s) return 'apply'
  return 'confirm'
}

function validMetric(n: unknown): n is number {
  return typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= MAX_METRIC
}

// True when the scroll container is within NEAR_LATEST_THRESHOLD px of the bottom.
// Invalid input returns false. Overscroll is clamped to the real scroll range.
export function isNearLatest(metrics: ScrollMetrics): boolean {
  if (!metrics || typeof metrics !== 'object') return false
  const { scrollHeight, scrollTop, clientHeight } = metrics
  if (!validMetric(scrollHeight) || !validMetric(scrollTop) || !validMetric(clientHeight)) return false
  if (scrollHeight < 0 || clientHeight < 0) return false
  const maxTop = Math.max(0, scrollHeight - clientHeight)
  const top = Math.min(Math.max(scrollTop, 0), maxTop)
  return maxTop - top <= NEAR_LATEST_THRESHOLD
}
