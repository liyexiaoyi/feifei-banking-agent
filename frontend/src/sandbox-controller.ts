import type { Receipt } from './sandbox-client'

export type ControllerState = { receipt: Receipt | null; error: string; busy: boolean; needsCheck: boolean }

export type SandboxClient = {
  run(wat: string, args: number[], key: string): Promise<Receipt>
  byKey(key: string): Promise<Receipt>
  list(): Promise<Receipt[]>
}

export type SandboxControllerOptions = {
  client: SandboxClient
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  user: string
  sourceHash: string
  wat: string
  keyFactory?: () => string
  onChange?: (state: ControllerState) => void
}

// Fixed messages only; raw/server error text is never surfaced.
const ERR = {
  user: '会话无效，无法运行',
  input: '请输入安全范围内的十进制整数',
  storage: '本地存储不可用，未提交请求',
  blocked: '存在未确认的请求，请先检查',
  net: '结果未确认，请点击检查，不要重复提交',
  lookup: '暂未确认该请求，请稍后再检查',
  pending: '请求尚未完成，请稍后再检查',
  mismatch: '回执与当前程序不匹配，请稍后再检查',
  history: '历史记录加载失败，请稍后再检查',
}
const KEY_RE = /^[A-Za-z0-9_-]{8,100}$/
const INT_RE = /^-?[0-9]+$/
const TERMINAL = new Set<string>(['SUCCEEDED', 'FAILED'])

function toInt(text: unknown): number | null {
  if (typeof text !== 'string' || !INT_RE.test(text)) return null
  const n = Number(text)
  return Number.isSafeInteger(n) ? n : null
}

function defaultKey(): string {
  return 'sbx-' + globalThis.crypto.randomUUID()
}

export function createSandboxController(o: SandboxControllerOptions) {
  const userOk = o.user === 'demo' || o.user === 'guest'
  const slot = 'ff-sandbox-pending:' + o.user
  const state: ControllerState = { receipt: null, error: userOk ? '' : ERR.user, busy: false, needsCheck: true }
  let disposed = false

  // Fresh snapshot per notification; no aliases of internal state escape.
  const emit = () => {
    if (disposed || !o.onChange) return
    o.onChange({ ...state, receipt: state.receipt ? structuredClone(state.receipt) : null })
  }
  const set = (patch: Partial<ControllerState>) => { Object.assign(state, patch); emit() }

  // Throws on blocked or corrupt storage so callers stay conservative.
  const readPending = (): string | null => {
    const v = o.storage.getItem(slot)
    if (v === null) return null
    if (!KEY_RE.test(v)) throw new Error('corrupt pending key')
    return v
  }
  // Compare-before-remove: never clears a newer, different key.
  const clearOwn = (key: string) => {
    try { if (o.storage.getItem(slot) === key) o.storage.removeItem(slot) } catch { /* key stays; next check retries */ }
  }
  const settle = (key: string, r: Receipt) => {
    if (!r || r.source_sha256 !== o.sourceHash || r.request_key !== key) {
      set({ busy: false, needsCheck: true, error: ERR.mismatch })
    } else if (TERMINAL.has(r.status)) {
      clearOwn(key)
      set({ receipt: r, busy: false, needsCheck: false, error: '' })
    } else {
      // RUNNING/UNKNOWN: read-only, keep key, never show a result.
      set({ receipt: { ...r, result: null }, busy: false, needsCheck: true, error: ERR.pending })
    }
  }

  // Read-only: byKey when a pending key exists, otherwise list. Never POSTs.
  async function refresh(): Promise<void> {
    if (disposed || state.busy || !userOk) return
    state.busy = true
    emit()
    let key: string | null
    try { key = readPending() } catch { set({ busy: false, needsCheck: true, error: ERR.storage }); return }
    if (key !== null) {
      // A 404 or any error does not prove the request was unsent.
      try { settle(key, await o.client.byKey(key)) } catch { set({ busy: false, needsCheck: true, error: ERR.lookup }) }
      return
    }
    try {
      const rows = await o.client.list()
      let latest: Receipt | null = null
      for (const r of Array.isArray(rows) ? rows : []) {
        if (r && r.source_sha256 === o.sourceHash && (!latest || r.created_at > latest.created_at)) latest = r
      }
      if (!latest) set({ receipt: null, busy: false, needsCheck: false, error: '' })
      else if (TERMINAL.has(latest.status)) set({ receipt: latest, busy: false, needsCheck: false, error: '' })
      else {
        const current = readPending()
        if (current !== null && current !== latest.request_key) {
          set({ receipt: null, busy: false, needsCheck: true, error: ERR.blocked })
          return
        }
        o.storage.setItem(slot, latest.request_key)
        if (o.storage.getItem(slot) !== latest.request_key) throw new Error('pending write not verified')
        set({ receipt: { ...latest, result: null }, busy: false, needsCheck: true, error: ERR.pending })
      }
    } catch {
      set({ busy: false, needsCheck: true, error: ERR.history })
    }
  }

  async function run(first: string, second: string): Promise<void> {
    if (disposed || state.busy || !userOk) return
    if (state.needsCheck) { set({ error: ERR.blocked }); return }
    if (state.receipt) return
    const a = toInt(first)
    const b = toInt(second)
    if (a === null || b === null) { set({ error: ERR.input }); return }
    state.busy = true // synchronous lock before any await
    emit()
    try {
      if (readPending() !== null) { set({ busy: false, needsCheck: true, error: ERR.blocked }); return }
    } catch {
      set({ busy: false, needsCheck: true, error: ERR.storage }); return
    }
    let key = ''
    try {
      key = (o.keyFactory ?? defaultKey)()
      if (typeof key !== 'string' || !KEY_RE.test(key)) throw new Error('invalid key')
      o.storage.setItem(slot, key)
      if (o.storage.getItem(slot) !== key) throw new Error('write not verified')
    } catch {
      if (key) clearOwn(key)
      set({ busy: false, error: ERR.storage }); return
    }
    let r: Receipt
    try {
      r = await o.client.run(o.wat, [a, b], key)
    } catch {
      // Outcome unknown: keep key, no automatic GET or POST.
      set({ busy: false, needsCheck: true, error: ERR.net }); return
    }
    settle(key, r)
  }

  function newRound(): boolean {
    const r = state.receipt
    if (disposed || state.busy || state.needsCheck || !r || !TERMINAL.has(r.status)) return false
    try {
      const k = o.storage.getItem(slot)
      if (k !== null) {
        // Different key, e.g. from another tab: do not remove; require check.
        if (k !== r.request_key) { set({ needsCheck: true, error: ERR.blocked }); return false }
        o.storage.removeItem(slot)
      }
    } catch {
      set({ needsCheck: true, error: ERR.storage }); return false
    }
    set({ receipt: null, error: '' })
    return true
  }

  function dispose(): void {
    disposed = true
  }

  return { load: refresh, run, check: refresh, newRound, dispose }
}
