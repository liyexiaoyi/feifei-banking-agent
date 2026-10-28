import { isAction, isControlKey } from './goal-control-client.ts'
import type { GoalControlAction, GoalControlClient, GoalControlView } from './goal-control-client.ts'

export type GoalControlScope = { owner: string; epoch: number }
export type GoalControlState = { paused: GoalControlView[]; busy: boolean; unknown: boolean; error: string }
export type GoalControlStorage = { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export interface GoalControlOptions {
  client: GoalControlClient; storage: GoalControlStorage; getScope: () => GoalControlScope
  onChange: (s: GoalControlState) => void; newKey?: () => string
}

const PREFIX = 'ff-goal-control-pending:'
// Only definite pre-execution denials may release the marker.
const SAFE = new Set(['VALIDATION_ERROR', 'VERSION_CONFLICT', 'GOAL_NOT_FOCUSED', 'CONFIRMATION_REQUIRED', 'RISK_CHANGED', 'GOAL_PAUSED', 'GOAL_UNKNOWN', 'GOAL_NOT_PAUSED', 'GOAL_TERMINAL', 'RESUME_BLOCKED', 'GOAL_LOCKED'])
const MSG = { load: '暂停的目标暂时读不到，请稍后再试。', unknown: '操作结果待核对，请点“核对”，不要重复操作。', denied: '这次操作没有执行，请刷新后再试。', storage: '无法安全记录操作，本次没有发送。' }
const STALE = new Error('GOAL_CONTROL_SCOPE_CHANGED')

export function createGoalControlController(o: GoalControlOptions) {
  const newKey = o.newKey ?? (() => globalThis.crypto.randomUUID())
  const empty = (): GoalControlState => ({ paused: [], busy: false, unknown: false, error: '' })
  let s = empty(), owned: GoalControlScope | null = null, disposed = false, gen = 0

  const scope = (): GoalControlScope => {
    if (disposed) throw STALE
    const c = o.getScope()
    if (!c || typeof c.owner !== 'string' || c.owner === '' || !Number.isSafeInteger(c.epoch)) throw STALE
    // A new owner/epoch never sees the previous owner's details.
    if (!owned || owned.owner !== c.owner || owned.epoch !== c.epoch) { s = empty(); gen++; owned = { owner: c.owner, epoch: c.epoch } }
    return { owner: c.owner, epoch: c.epoch }
  }
  const same = (a: GoalControlScope): boolean => {
    if (disposed) return false
    try { const c = o.getScope(); return !!c && c.owner === a.owner && c.epoch === a.epoch } catch { return false }
  }
  const guard = (a: GoalControlScope) => { if (!same(a)) throw STALE }
  const publish = (a: GoalControlScope, patch: Partial<GoalControlState>) => {
    if (!same(a)) return
    s = { ...s, ...patch }
    o.onChange({ ...s, paused: s.paused.slice() })
  }
  const slot = (a: GoalControlScope) => PREFIX + a.owner
  const read = (a: GoalControlScope): string | null => {
    const v = o.storage.getItem(slot(a))
    if (v === null) return null
    // A malformed marker means an earlier control's outcome is undeterminable: fail closed.
    if (!isControlKey(v)) throw new Error('GOAL_CONTROL_MARKER')
    return v
  }
  // Throws unless the original marker is verifiably gone; callers then stay unknown.
  const clear = (a: GoalControlScope, key: string) => {
    const k = slot(a)
    if (o.storage.getItem(k) === key) o.storage.removeItem(k)
    if (o.storage.getItem(k) !== null) throw new Error('GOAL_CONTROL_MARKER_RESIDUE')
  }
  const latch = (a: GoalControlScope) => publish(a, { paused: [], busy: false, unknown: true, error: MSG.unknown })

  async function refresh(a: GoalControlScope) {
    const g = ++gen
    try {
      const paused = await o.client.list()
      if (g !== gen) return
      publish(a, { paused, error: '' })
    } catch (e) {
      if (e === STALE || !same(a) || g !== gen) return
      publish(a, { paused: [], error: MSG.load })
    }
  }

  async function verify(a: GoalControlScope, key: string) {
    publish(a, { unknown: true, busy: true })
    try {
      await o.client.lookup(key)
      guard(a)
      clear(a, key)
      publish(a, { unknown: false, busy: false, error: '' })
      await refresh(a)
    } catch (e) {
      // 404, network and malformed results all stay unknown; no inference, no new key.
      publish(a, { unknown: true, busy: false, error: MSG.unknown })
      throw e
    }
  }

  return {
    state: (): GoalControlState => (owned && same(owned) ? { ...s, paused: s.paused.slice() } : empty()),
    async load() {
      const a = scope()
      let marker: string | null
      try { marker = read(a) } catch (e) { latch(a); throw e }
      if (marker) publish(a, { unknown: true, error: MSG.unknown })
      await refresh(a)
      if (marker && same(a)) await verify(a, marker)
    },
    async check() {
      const a = scope()
      if (s.busy) throw new Error('GOAL_CONTROL_BUSY')
      let marker: string | null
      try { marker = read(a) } catch (e) { latch(a); throw e }
      if (!marker) { publish(a, { unknown: false }); return }
      await verify(a, marker)
    },
    async submit(goal: GoalControlView, action: GoalControlAction, confirmed: boolean) {
      if (confirmed !== true || !isAction(action)) throw new Error('GOAL_CONTROL_NOT_CONFIRMED')
      const a = scope()
      if (s.busy || s.unknown) throw new Error('GOAL_CONTROL_PENDING')
      let existing: string | null
      try { existing = read(a) } catch (e) { latch(a); throw e }
      let key: string
      try {
        if (existing !== null) { publish(a, { unknown: true, error: MSG.unknown }); throw new Error('GOAL_CONTROL_PENDING') }
        key = newKey()
        if (!isControlKey(key)) throw new Error('GOAL_CONTROL_KEY')
        o.storage.setItem(slot(a), key)
        if (o.storage.getItem(slot(a)) !== key) throw new Error('GOAL_CONTROL_STORAGE')
      } catch (e) {
        if ((e as Error).message !== 'GOAL_CONTROL_PENDING') publish(a, { error: MSG.storage })
        throw e
      }
      // Busy is set synchronously so a double click cannot reach a second POST.
      s = { ...s, busy: true, unknown: true, error: '' }
      publish(a, {})
      try {
        guard(a)
        await o.client.run(goal, action, key, true)
        guard(a)
        clear(a, key)
        publish(a, { busy: false, unknown: false })
        await refresh(a)
      } catch (e) {
        if (e === STALE || !same(a)) throw e
        const err = e as { code?: unknown; status?: unknown }
        const definite = typeof err.code === 'string' && SAFE.has(err.code) && typeof err.status === 'number' && err.status >= 400 && err.status < 500 && err.status !== 404
        let released = false
        if (definite) { try { clear(a, key); released = true } catch { released = false } }
        if (released) publish(a, { busy: false, unknown: false, error: MSG.denied })
        else publish(a, { busy: false, unknown: true, error: MSG.unknown })
        throw e
      }
    },
    dispose() { disposed = true },
  }
}
