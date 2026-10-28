import type { GenerationView } from './codegen-client.ts'

export type CodegenState = { view: GenerationView | null; error: string; busy: boolean; needsCheck: boolean }

export type CodegenControllerClient = {
  generate(description: string, args: number[], key: string, consent: boolean): Promise<GenerationView>
  byKey(key: string): Promise<GenerationView>
  list(): Promise<GenerationView[] | { generations: GenerationView[] }>
}

export type CodegenControllerOptions = {
  client: CodegenControllerClient
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  user: string
  keyFactory?: () => string
  onChange?: (state: CodegenState) => void
}

// Fixed messages only; raw client/server/storage error text is never surfaced.
const ERR = {
  user: '会话无效，无法生成',
  consent: '请先勾选同意本次调用AI生成并运行计算程序',
  desc: '请输入1到300字的计算描述',
  input: '参数须为0到8个安全范围内的十进制整数',
  key: '无法创建请求标识，未提交请求',
  storage: '本地存储不可用，未提交请求',
  blocked: '存在未确认的请求，请先核对结果',
  done: '本次结果已显示，如需重新计算请点击开始新计算',
  net: '结果未确认，请点击核对结果，不要重复提交',
  lookup: '暂未确认该请求，请稍后再核对',
  pending: '请求尚未完成，请稍后再核对',
  mismatch: '回执与当前请求不匹配，请稍后再核对',
  history: '历史记录加载失败，请稍后再核对',
}
const KEY_RE = /^[A-Za-z0-9_-]{8,100}$/
const INT_RE = /^-?[0-9]+$/
const isTerminal = (s: unknown) => s === 'SUCCEEDED' || s === 'FAILED'

// Rejects lone surrogates so the description is valid UTF-8 when encoded.
function wellFormed(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    if (c >= 0xd800 && c <= 0xdbff) {
      const n = s.charCodeAt(i + 1)
      if (!(n >= 0xdc00 && n <= 0xdfff)) return false
      i++
    } else if (c >= 0xdc00 && c <= 0xdfff) return false
  }
  return true
}

// Chinese comma, English comma or whitespace separated decimal safe integers, 0..8 items.
function parseArgs(text: unknown): number[] | null {
  if (typeof text !== 'string') return null
  const t = text.trim()
  if (t === '') return []
  const parts = t.split(/[,，\s]+/)
  if (parts.length > 8) return null
  const out: number[] = []
  for (const p of parts) {
    if (!INT_RE.test(p)) return null
    const n = Number(p)
    if (!Number.isSafeInteger(n)) return null
    out.push(n === 0 ? 0 : n)
  }
  return out
}

function defaultKey(): string {
  return 'cgen-' + globalThis.crypto.randomUUID()
}

export function createCodegenController(o: CodegenControllerOptions) {
  const userOk = o.user === 'demo' || o.user === 'guest'
  const slot = 'ff-codegen-pending:' + o.user
  const state: CodegenState = { view: null, error: userOk ? '' : ERR.user, busy: false, needsCheck: true }
  let disposed = false

  // Deep copies only; no alias of internal state escapes.
  const copy = (): CodegenState => ({ ...state, view: state.view ? structuredClone(state.view) : null })
  const emit = () => {
    if (disposed || !o.onChange) return
    try { o.onChange(copy()) } catch { /* listener errors never break the controller */ }
  }
  const set = (patch: Partial<CodegenState>) => { Object.assign(state, patch); emit() }

  // Throws on blocked or corrupt storage so callers stay conservative.
  const readPending = (): string | null => {
    const v = o.storage.getItem(slot)
    if (v === null) return null
    if (typeof v !== 'string' || !KEY_RE.test(v)) throw new Error('corrupt pending key')
    return v
  }
  // Compare-before-remove: a late reply never clears a newer, different key.
  const clearOwn = (key: string) => {
    try { if (o.storage.getItem(slot) === key) o.storage.removeItem(slot) } catch { /* key stays; next check retries */ }
  }

  const settle = (key: string, raw: GenerationView) => {
    if (disposed) return
    const g = raw && raw.generation
    if (!g || g.generation_key !== key) { set({ busy: false, needsCheck: true, error: ERR.mismatch }); return }
    const v = structuredClone(raw)
    const succeeded = g.status === 'SUCCEEDED' && !!v.run && v.run.status === 'SUCCEEDED'
    if (succeeded || g.status === 'FAILED') {
      clearOwn(key)
      const view = succeeded ? v : ({ ...v, run: null } as GenerationView)
      set({ view, busy: false, needsCheck: false, error: '' })
    } else {
      // Pending/UNKNOWN: read-only, keep original key, never upgrade or show a run result.
      set({ view: { ...v, run: null } as GenerationView, busy: false, needsCheck: true, error: ERR.pending })
    }
  }

  // Read-only: byKey when a pending key exists, otherwise latest from list. Never POSTs.
  async function refresh(): Promise<void> {
    if (disposed || state.busy || !userOk) return
    set({ busy: true })
    let key: string | null
    try { key = readPending() } catch { set({ busy: false, needsCheck: true, error: ERR.storage }); return }
    if (key !== null) {
      // 404 or any failure does not prove the request was unsent: keep key.
      try { settle(key, await o.client.byKey(key)) } catch { if (!disposed) set({ busy: false, needsCheck: true, error: ERR.lookup }) }
      return
    }
    try {
      const res = await o.client.list()
      if (disposed) return
      const rows = Array.isArray(res) ? res : res && Array.isArray(res.generations) ? res.generations : null
      if (!rows) throw new Error('invalid list')
      let latest: GenerationView | null = null
      for (const r of rows) {
        if (r && r.generation && (!latest || r.generation.created_at > latest.generation.created_at)) latest = r
      }
      if (!latest) { set({ view: null, busy: false, needsCheck: false, error: '' }); return }
      const k = latest.generation.generation_key
      if (typeof k !== 'string' || !KEY_RE.test(k)) throw new Error('invalid key')
      if (!isTerminal(latest.generation.status)) {
        const current = readPending()
        if (current !== null && current !== k) { set({ busy: false, needsCheck: true, error: ERR.blocked }); return }
        o.storage.setItem(slot, k)
        if (o.storage.getItem(slot) !== k) throw new Error('pending write not verified')
      }
      settle(k, latest)
    } catch {
      if (!disposed) set({ busy: false, needsCheck: true, error: ERR.history })
    }
  }

  async function start(description: string, argsText: string, consent: boolean): Promise<void> {
    if (disposed || state.busy || !userOk) return
    state.busy = true // synchronous lock before any validation or await
    const fail = (error: string, needsCheck = state.needsCheck) => set({ busy: false, error, needsCheck })
    if (state.needsCheck || state.view) { fail(state.view && !state.needsCheck ? ERR.done : ERR.blocked); return }
    if (consent !== true) { fail(ERR.consent); return }
    const d = typeof description === 'string' ? description.trim() : ''
    if (d.length < 1 || d.length > 300 || !wellFormed(d)) { fail(ERR.desc); return }
    const args = parseArgs(argsText)
    if (!args) { fail(ERR.input); return }
    let key = ''
    try { key = (o.keyFactory ?? defaultKey)() } catch { key = '' }
    if (typeof key !== 'string' || !KEY_RE.test(key)) { fail(ERR.key); return }
    try {
      if (readPending() !== null) { fail(ERR.blocked, true); return }
      o.storage.setItem(slot, key)
      if (o.storage.getItem(slot) !== key) throw new Error('pending write not verified')
    } catch { fail(ERR.storage, true); return }
    set({ error: '' })
    let v: GenerationView
    try { v = await o.client.generate(d, args, key, true) } catch {
      if (!disposed) set({ busy: false, needsCheck: true, error: ERR.net })
      return
    }
    try { settle(key, v) } catch { if (!disposed) set({ busy: false, needsCheck: true, error: ERR.net }) }
  }

  // Explicit only; allowed solely after a known terminal receipt.
  function newRound(): boolean {
    if (disposed || state.busy || !userOk || state.needsCheck || !state.view) return false
    const g = state.view.generation
    if (!g || !isTerminal(g.status)) return false
    clearOwn(g.generation_key)
    set({ view: null, error: '', needsCheck: false })
    return true
  }

  return {
    load: refresh,
    check: refresh,
    start,
    newRound,
    dispose() { disposed = true },
    snapshot: copy,
  }
}
