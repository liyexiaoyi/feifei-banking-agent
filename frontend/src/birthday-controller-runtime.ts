import { parseBirthdayPending } from './birthday-client.ts'
import type { BirthdayBundle, BirthdayFlow, BirthdayOperation, BirthdayPending, BirthdayClient } from './birthday-client.ts'
export type { BirthdayIntent, BirthdayAction } from './birthday-client.ts'

export interface BirthdayState {
  data: BirthdayBundle | null; selected: BirthdayFlow | null; operation: BirthdayOperation | null
  pending: BirthdayPending | null; busy: boolean; needsCheck: boolean; invalidSession: boolean
  error: string; notice: string; checked: boolean
}
export type BirthdayClientLike = Pick<BirthdayClient, 'session' | 'load' | 'get' | 'lookup' | 'create' | 'command' | 'readOperation'>
export interface BirthdayStorage { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export interface BirthdayControllerOptions {
  client: BirthdayClientLike; storage: BirthdayStorage; user: 'demo' | 'guest'
  runMutation: (task: () => Promise<void>) => Promise<void>
  onChange: (state: BirthdayState) => void; keyFactory?: () => string
}
export const birthdayPendingKey = (user: 'demo' | 'guest'): string => 'birthday-pending:' + user

const MSG_SESSION = '登录状态已变化，请重新进入生日安排。'
const MSG_MARKER = '待核对的生日安排记录无法读取，请稍后核对。'

const canon = (m: BirthdayPending): string => m.kind === 'create'
  ? JSON.stringify({ kind: m.kind, request_key: m.request_key })
  : JSON.stringify({ kind: m.kind, action: m.action, flow_id: m.flow_id, request_key: m.request_key })

export function createBirthdayRuntime(options: BirthdayControllerOptions) {
  const key = birthdayPendingKey(options.user)
  let state: BirthdayState = { data: null, selected: null, operation: null, pending: null, busy: false, needsCheck: false, invalidSession: false, error: '', notice: '', checked: false }
  let generation = 0
  let writing = false
  let disposed = false
  let invalid = false

  const isLive = (): boolean => !disposed && !invalid
  const getState = (): BirthdayState => structuredClone(state)
  const hide = (): void => { state = { ...state, data: null, selected: null, operation: null } }
  function patch(partial: Partial<BirthdayState>): void {
    if (disposed) return
    state = { ...state, ...structuredClone(partial) }
    if (invalid) state = { ...state, data: null, selected: null, operation: null, invalidSession: true }
    try { options.onChange(getState()) } catch { /* observer errors must never block marker or payment tracking */ }
  }
  async function guard(): Promise<boolean> {
    if (!isLive()) return false
    let ok = false
    try {
      const u: unknown = await options.client.session()
      ok = typeof u === 'string' && u === options.user
    } catch { ok = false }
    if (disposed) return false
    if (!ok && !invalid) { invalid = true; generation++; hide(); patch({ invalidSession: true }) }
    return ok && isLive()
  }
  async function call<T>(work: () => Promise<T>): Promise<T> {
    if (!(await guard())) throw new Error(MSG_SESSION)
    let result: T
    try { result = await work() } catch (e) {
      if (!(await guard())) throw new Error(MSG_SESSION)
      throw e
    }
    if (!(await guard())) throw new Error(MSG_SESSION)
    return result
  }
  function beginRead(): number {
    if (writing || !isLive()) return -1
    return ++generation
  }
  const current = (g: number): boolean => g === generation && isLive()
  function beginWrite(): boolean {
    if (writing || !isLive()) return false
    writing = true
    generation++
    patch({ busy: true })
    return true
  }
  function endWrite(): void {
    writing = false
    patch({ busy: false })
  }
  function marker(): BirthdayPending | null {
    try {
      const raw = options.storage.getItem(key)
      return raw === null ? null : parseBirthdayPending(JSON.parse(raw))
    } catch { throw new Error(MSG_MARKER) }
  }
  function saveMarker(m: BirthdayPending): void {
    const valid = parseBirthdayPending(m)
    options.storage.setItem(key, canon(valid))
  }
  function clearMarker(expected: BirthdayPending): boolean {
    try {
      const raw = options.storage.getItem(key)
      if (raw === null) return false
      const cur = parseBirthdayPending(JSON.parse(raw))
      if (canon(cur) !== canon(parseBirthdayPending(expected))) return false
      options.storage.removeItem(key)
      return true
    } catch { return false }
  }
  function dispose(): void { disposed = true; generation++; hide() }

  return { getState, patch, guard, call, beginRead, current, beginWrite, endWrite, marker, saveMarker, clearMarker, isLive, dispose }
}
