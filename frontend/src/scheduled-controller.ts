import type { Schedule, ScheduleBundle, ScheduleIntent, ScheduleOperation, PendingSchedule } from './scheduled-client.ts'

export interface ScheduledState {
  data: ScheduleBundle | null; pending: PendingSchedule | null; recovered: Schedule | null; operation: ScheduleOperation | null
  busy: boolean; needsCheck: boolean; invalidSession: boolean; error: string; checked: boolean
}
export interface ScheduledClientLike {
  session(): Promise<'demo' | 'guest'>
  load(): Promise<ScheduleBundle>
  prepare(intent: ScheduleIntent, key: string): Promise<{ schedule: Schedule; operation: ScheduleOperation }>
  lookup(key: string): Promise<Schedule>
  get(id: string): Promise<Schedule>
  cancel(schedule: Schedule, key: string, confirmed: true): Promise<Schedule>
  runDue(): Promise<unknown>
  readOperation(id: string, schedule: Schedule): Promise<ScheduleOperation>
}
export interface ScheduledStorageLike { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export interface ScheduledControllerOptions {
  client: ScheduledClientLike; storage: ScheduledStorageLike; user: 'demo' | 'guest'
  runMutation(task: () => Promise<void>): Promise<void>; onChange(state: ScheduledState): void; keyFactory?: () => string
}

const KEY = /^[A-Za-z0-9_-]{8,64}$/
const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\+08:00$/
const CREATE_DONE = ['ACTIVE', 'SUCCEEDED', 'NOT_PAID', 'CANCELLED']
const TERMINAL = ['SUCCEEDED', 'NOT_PAID', 'CANCELLED']
const CANCELABLE = ['AUTHORIZING', 'ACTIVE']
const PENDING_CHILD = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY']
const UNRESOLVED = ['UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW']
const AUTH_ENDED = ['CANCELLED', 'EXPIRED', 'FAILED', 'REJECTED']
const sameMarker = (a: PendingSchedule | null, b: PendingSchedule | null): boolean => !!a && !!b && JSON.stringify(a) === JSON.stringify(b)
const SESSION_MSG = '登录状态已变化，请刷新页面后再操作。'

export function pendingScheduleStorageKey(user: 'demo' | 'guest'): string {
  return 'scheduled-pending:' + encodeURIComponent(user)
}

function validKey(k: unknown): k is string {
  return typeof k === 'string' && KEY.test(k) && !/^(task-|select-|sched-)/.test(k)
}
function exactKeys(o: Record<string, unknown>, keys: string[]): boolean {
  const own = Object.keys(o).sort()
  return own.length === keys.length && keys.slice().sort().every((k, i) => k === own[i])
}
function parseMarker(v: unknown): PendingSchedule {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw Error('marker')
  const o = v as Record<string, unknown>
  if (o.kind === 'create' && exactKeys(o, ['kind', 'request_key']) && validKey(o.request_key)) return { kind: 'create', request_key: o.request_key } as PendingSchedule
  if (o.kind === 'cancel' && exactKeys(o, ['kind', 'request_key', 'schedule_id']) && validKey(o.request_key) && typeof o.schedule_id === 'string' && o.schedule_id.length > 0 && o.schedule_id.length <= 128)
    return { kind: 'cancel', request_key: o.request_key, schedule_id: o.schedule_id } as PendingSchedule
  if (o.kind === 'drive' && exactKeys(o, ['kind'])) return { kind: 'drive' } as PendingSchedule
  throw Error('marker')
}
function validDue(t: unknown): t is string {
  const m = typeof t === 'string' ? ISO.exec(t) : null
  if (!m) return false
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number)
  const dt = new Date(Date.UTC(y, mo - 1, d, h, mi, s))
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d && dt.getUTCHours() === h && dt.getUTCMinutes() === mi && dt.getUTCSeconds() === s
}
function is401(e: unknown): boolean {
  const x = e as { status?: unknown; message?: unknown } | null
  return !!x && (x.status === 401 || (typeof x.message === 'string' && /\b401\b/.test(x.message)))
}

export function createScheduledController(options: ScheduledControllerOptions) {
  const { client, storage, user, runMutation, onChange } = options
  const keyFactory = options.keyFactory ?? (() => crypto.randomUUID())
  const key = pendingScheduleStorageKey(user)
  const empty = (): ScheduledState => ({ data: null, pending: null, recovered: null, operation: null, busy: false, needsCheck: false, invalidSession: false, error: '', checked: false })
  let state: ScheduledState = empty()
  let readGen = 0, disposed = false, invalid = false, writing = false, checked = false, markerBroken = false

  const snapshot = (): ScheduledState => ({
    data: state.data ? structuredClone(state.data) : null, pending: state.pending ? { ...state.pending } : null,
    recovered: state.recovered ? structuredClone(state.recovered) : null, operation: state.operation ? structuredClone(state.operation) : null,
    busy: state.busy, needsCheck: state.needsCheck, invalidSession: state.invalidSession, error: state.error, checked })
  const set = (patch: Partial<ScheduledState>): void => {
    if (disposed) return
    state = { ...state, ...patch }
    if (invalid) state = { ...state, data: null, pending: null, recovered: null, operation: null, invalidSession: true, error: SESSION_MSG }
    try { onChange(snapshot()) } catch { /* listener errors ignored */ }
  }
  const latch = (): void => { invalid = true; readGen++; checked = false; set({}) }
  async function guard(): Promise<boolean> {
    if (invalid || disposed) return false
    let s: unknown = null
    try { s = await client.session() } catch { latch(); return false }
    if (s !== user) { latch(); return false }
    return !invalid && !disposed
  }
  function readMarker(): { ok: boolean; p: PendingSchedule | null } {
    try {
      const raw = storage.getItem(key)
      return raw === null ? { ok: true, p: null } : { ok: true, p: parseMarker(JSON.parse(raw)) }
    } catch { return { ok: false, p: null } }
  }
  const live = (gen: number): boolean => gen === readGen && !disposed && !invalid && !writing

  async function read(): Promise<boolean> {
    if (disposed || invalid || writing) return false
    const gen = ++readGen
    checked = false
    set({ data: null, recovered: null, operation: null, error: '' })
    try {
      if (!await guard() || !live(gen)) return false
      const m = readMarker()
      markerBroken = !m.ok
      if (!m.ok) set({ pending: null, needsCheck: true, error: '原请求记录无法读取，请先核对，暂不能新建。' })
      else set({ pending: m.p, needsCheck: m.p !== null })
      const d = await client.load()
      if (!await guard() || !live(gen)) return false
      set({ data: d })
      if (!m.ok || !m.p) return m.ok
      let rec: Schedule | null = null
      try {
        if (m.p.kind === 'create') rec = await client.lookup(m.p.request_key)
        else if (m.p.kind === 'cancel') rec = await client.get(m.p.schedule_id)
      } catch (e) {
        if (is401(e)) { latch(); return false }
        if (live(gen)) set({ error: '暂未查到原请求结果，请稍后再核对，不会重新发送。' })
        return false
      }
      if (!await guard() || !live(gen)) return false
      checked = true
      set({ recovered: rec, needsCheck: true })
      return true
    } catch (e) {
      if (is401(e)) latch()
      else if (gen === readGen) set({ error: '读取失败，请稍后只读刷新。' })
      return false
    }
  }

  async function write(marker: PendingSchedule, post: () => Promise<boolean>, expected?: PendingSchedule): Promise<boolean> {
    if (writing || disposed || invalid || state.busy || markerBroken) return false
    if (expected ? !(checked && sameMarker(state.pending, expected)) : (state.needsCheck || state.pending)) return false
    writing = true
    readGen++
    set({ busy: true, error: '' })
    let posted = false, ok = false
    try {
      await runMutation(async () => {
        if (disposed || invalid || !await guard()) return
        const m = readMarker()
        if (!m.ok || (expected ? !sameMarker(m.p, expected) : m.p)) { markerBroken = !m.ok; set({ needsCheck: true, error: '存在未核对的原请求，请先核对。' }); return }
        storage.setItem(key, JSON.stringify(marker))
        posted = true
        checked = false
        set({ pending: marker, needsCheck: true })
        if (await post() && !invalid && !disposed) ok = true
      })
      if (!ok && !posted && !state.error) set({ error: '当前无法提交，请稍后再试。' })
    } catch (e) {
      if (is401(e)) latch()
      else if (posted && marker.kind === 'create' && (e as { definiteSchedulePrepareRejection?: unknown } | null)?.definiteSchedulePrepareRejection === true && !disposed && !invalid && await guard()) {
        let cleared = false
        try {
          const m = readMarker()
          if (m.ok && sameMarker(m.p, marker)) { storage.removeItem(key); cleared = true }
        } catch { cleared = false }
        if (cleared) set({ pending: null, needsCheck: false, recovered: null, operation: null, error: '定时转账未保存，请检查时间等信息后重试。' })
        else set({ needsCheck: true, error: '提交结果未确认，请先核对原请求，不会自动重发。' })
      }
      else if (invalid || disposed) { /* session mismatch or disposed: preserve marker and owner latch */ }
      else set(posted ? { needsCheck: true, error: '提交结果未确认，请先核对原请求，不会自动重发。' } : { error: '当前无法提交，请稍后再试。' })
    } finally {
      writing = false
      set({ busy: false })
    }
    return ok
  }

  function ownSchedule(s: Schedule | null | undefined): Schedule | null {
    if (!s || typeof s !== 'object') return null
    const pool = [...(state.data?.schedules ?? []), ...(state.recovered ? [state.recovered] : [])]
    return pool.find(x => x.id === s.id && x.version === s.version) ?? null
  }

  return {
    getState: (): ScheduledState => snapshot(),
    load: (): Promise<boolean> => read(),
    check: (): Promise<boolean> => read(),
    async prepare(intent: ScheduleIntent): Promise<boolean> {
      const i = intent as unknown as Record<string, unknown>
      const d = state.data
      if (!d || !i || typeof i !== 'object' || !exactKeys(i, ['payee_id', 'amount_cents', 'due_at'])) { set({ error: '请完整填写收款人、金额和执行时间。' }); return false }
      const amt = i.amount_cents
      if (typeof i.payee_id !== 'string' || !d.payees.some(p => p.id === i.payee_id) || typeof amt !== 'number' || !Number.isSafeInteger(amt) || amt < 1 || amt > 100000000 || !validDue(i.due_at)) {
        set({ error: '收款人、金额或执行时间不正确。' }); return false
      }
      const clean = { payee_id: i.payee_id, amount_cents: amt, due_at: i.due_at } as ScheduleIntent
      let rk: string
      try { rk = keyFactory() } catch { rk = '' }
      if (!validKey(rk)) { set({ error: '无法生成请求编号，请刷新页面。' }); return false }
      return write({ kind: 'create', request_key: rk } as PendingSchedule, async () => {
        const r = await client.prepare(clean, rk)
        const s = r?.schedule
        if (!s || s.payee_id !== clean.payee_id || s.amount_cents !== clean.amount_cents || s.due_at !== clean.due_at) throw Error('mismatch')
        if (!await guard()) return false
        set({ recovered: s, operation: r.operation ?? null })
        return true
      })
    },
    async cancel(schedule: Schedule, confirmed: boolean): Promise<boolean> {
      if (confirmed !== true) return false
      const own = ownSchedule(schedule)
      const childPending = !!own && own.status === 'CHILD_PENDING' && PENDING_CHILD.includes(own.child_status as string)
      if (!own || !(CANCELABLE.includes(own.status) || childPending)) { set({ error: '该定时转账当前不能取消，请先只读刷新。' }); return false }
      const pend = state.pending, rec = state.recovered
      let expected: PendingSchedule | undefined
      if (pend) {
        if (!(pend.kind === 'create' && checked && !markerBroken && rec && rec.id === own.id && rec.version === own.version && CANCELABLE.includes(own.status) && !UNRESOLVED.includes((own as any).observed_status as string))) { set({ error: '存在未核对的原请求，请先核对。' }); return false }
        expected = { ...pend }
      }
      let rk: string
      try { rk = keyFactory() } catch { rk = '' }
      if (!validKey(rk)) return false
      return write({ kind: 'cancel', request_key: rk, schedule_id: own.id } as PendingSchedule, async () => {
        const r = await client.cancel(own, rk, true)
        if (!r || r.id !== own.id) throw Error('mismatch')
        if (!await guard()) return false
        set({ recovered: r })
        return true
      }, expected)
    },
    async drive(): Promise<boolean> {
      const ok = await write({ kind: 'drive' } as PendingSchedule, async () => { await client.runDue(); return guard() })
      if (!ok) return false
      return read()
    },
    async openOperation(id: string, schedule: Schedule): Promise<ScheduleOperation | null> {
      if (disposed || invalid || writing || typeof id !== 'string') return null
      const own = ownSchedule(schedule)
      if (!own || (id !== own.authorization_operation_id && id !== own.child_operation_id)) return null
      const gen = ++readGen
      set({ operation: null })
      try {
        if (!await guard() || !live(gen)) return null
        const op = await client.readOperation(id, own)
        if (!await guard() || !live(gen)) return null
        set({ operation: op })
        return op
      } catch (e) {
        if (is401(e)) latch(); else if (gen === readGen) set({ error: '无法读取原操作，请稍后再试。' })
        return null
      }
    },
    async startNew(confirmed: boolean): Promise<boolean> {
      if (confirmed !== true || writing || disposed || invalid) return false
      if (!await guard() || writing) return false
      const m = readMarker()
      if (!m.ok || markerBroken) { markerBroken = true; return false }
      const p = m.p, rec = state.recovered
      if (p) {
        if (!checked) return false
        if (p.kind === 'create' && !(rec && ((CREATE_DONE.includes(rec.status) && !UNRESOLVED.includes(rec.child_status as string)) || (rec.status === 'AUTHORIZING' && AUTH_ENDED.includes((rec as any).observed_status as string) && !rec.child_operation_id)))) return false
        if (p.kind === 'cancel' && !(rec && rec.id === p.schedule_id && TERMINAL.includes(rec.status))) return false
        try { storage.removeItem(key) } catch { return false }
      }
      checked = false
      set({ pending: null, needsCheck: false, recovered: null, operation: null, error: '' })
      return true
    },
    dispose(): void {
      readGen++
      state = { ...empty(), invalidSession: invalid }
      disposed = true
    },
  }
}
