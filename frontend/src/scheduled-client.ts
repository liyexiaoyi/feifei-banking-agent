// LOCAL SIMULATED scheduled transfer client: strict types/parsers only. No UI, storage, logs or retries.
export type ScheduleStatus = 'AUTHORIZING' | 'ACTIVE' | 'CHILD_PENDING' | 'CHILD_IN_FLIGHT' | 'SUCCEEDED' | 'NOT_PAID' | 'CANCELLED'
export type OperationStatus = 'WAITING_CONFIRMATION' | 'WAITING_VERIFICATION' | 'READY' | 'EXECUTING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'EXPIRED' | 'UNKNOWN' | 'MANUAL_REVIEW' | 'REJECTED'
export type ObservedStatus = ScheduleStatus | OperationStatus | 'AUTH_EXPIRED' | 'MISSED_GRACE'
export type Schedule = {
  id: string; status: ScheduleStatus; observed_status: ObservedStatus; version: number; amount_cents: number
  account_id: string; account_name: string; payee_id: string; payee_name: string; payee_phone: string
  due_at: string; grace_until: string; authorization_operation_id: string
  child_operation_id: string | null; child_status: OperationStatus | null; child_mode: string | null
  outcome_code: string | null; reserved_cents: 0; created_at: string; updated_at: string; cancelled_at: string | null
  replayed: boolean; simulated: true; notice: string
}
export type ScheduleIntent = { payee_id: string; amount_cents: number; due_at: string }
export type ScheduleOperation = {
  id: string; type: 'scheduled_transfer_authorize' | 'transfer'; status: OperationStatus; risk: 'RED' | 'YELLOW'
  created_at: number; expires_at: number; payload: Record<string, unknown>; result: Record<string, unknown> | null
  confirmed_at?: number | null; executed_day?: string | null
}
export type Payee = { id: string; name: string; masked_phone: string }
export type ScheduleBundle = { schedules: Schedule[]; payees: Payee[] }
export type RunDueEntry = { schedule_id: string; version: number; status: ScheduleStatus; outcome_code: string | null; child_operation_id: string | null; action: string }
export type RunDueResult = { checked_at: string; simulated: true; processed: RunDueEntry[]; next_due_at: string | null; notice: string }
export type PendingSchedule = { kind: 'create'; request_key: string } | { kind: 'cancel'; request_key: string; schedule_id: string } | { kind: 'drive' }
export type ScheduledApi = (path: string, body?: unknown) => Promise<unknown>

const SCHEDULE_STATUSES = ['AUTHORIZING', 'ACTIVE', 'CHILD_PENDING', 'CHILD_IN_FLIGHT', 'SUCCEEDED', 'NOT_PAID', 'CANCELLED']
const OP_STATUSES = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY', 'EXECUTING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED', 'UNKNOWN', 'MANUAL_REVIEW', 'REJECTED']
const AUTH_TYPE = 'scheduled_transfer_authorize'
const MAX_CENTS = 100000000
const PHONE = /^\d{3}\*{4}\d{4}$/
const CODE = /^[A-Z][A-Z0-9_]{0,63}$/

const fail = (): never => { throw new Error('定时转账数据无效，请只读刷新后核对') }
function obj(v: unknown): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) fail()
  return v as Record<string, unknown>
}
function int(v: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) fail()
  return v as number
}
function str(v: unknown, max = 200): string {
  if (typeof v !== 'string' || v.length < 1 || v.length > max) fail()
  return v as string
}
function oneOf<T extends string>(v: unknown, list: readonly string[]): T {
  if (typeof v !== 'string' || !list.includes(v)) fail()
  return v as T
}
function code(v: unknown): string { const s = str(v, 64); if (!CODE.test(s)) fail(); return s }
function phone(v: unknown): string { const s = str(v, 32); if (!PHONE.test(s)) fail(); return s }
function nul<T>(v: unknown, f: (x: unknown) => T): T | null { return v === null ? null : f(v) }

// Strict +08:00 second-precision ISO; real calendar check via epoch round trip (rejects 2/30, 24:00).
function chinaEpoch(v: unknown): number {
  if (typeof v !== 'string') return fail()
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\+08:00$/.exec(v)
  if (!m) return fail()
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number)
  const t = Date.UTC(y, mo - 1, d, h - 8, mi, s)
  const g = new Date(t + 8 * 3600000)
  if (g.getUTCFullYear() !== y || g.getUTCMonth() !== mo - 1 || g.getUTCDate() !== d || g.getUTCHours() !== h || g.getUTCMinutes() !== mi || g.getUTCSeconds() !== s) fail()
  if (Date.parse(v) !== t || new Date(t).toISOString() !== new Date(Date.parse(v)).toISOString()) fail()
  return t / 1000
}
function iso(v: unknown): string { chinaEpoch(v); return v as string }
// Finite non-negative epoch seconds (floats allowed, e.g. .125).
function ts(v: unknown): number {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) fail()
  return v as number
}
// Receipt ISO: seconds plus optional 1..6 fractional digits, +08:00; calendar via chinaEpoch, fraction added exactly (no rounding).
function receiptEpoch(v: unknown): number {
  if (typeof v !== 'string') return fail()
  const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?\+08:00$/.exec(v)
  if (!m) return fail()
  return chinaEpoch(`${m[1]}+08:00`) + (m[2] ? Number(m[2].padEnd(6, '0')) / 1e6 : 0)
}
const CHILD_MODES = ['AUTO', 'MANUAL']
const RUN_ACTIONS = ['EXECUTED', 'CHILD_PENDING', 'NOT_PAID', 'DEFERRED', 'RECONCILED']
const PAY_KEYS = ['account_id', 'account_name', 'payee_id', 'payee_name', 'payee_phone', 'amount_cents', 'fee_cents']
const EXPIRABLE = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY']

const AUTH_OBSERVED = ['CANCELLED', 'EXPIRED', 'FAILED', 'REJECTED', 'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW'] as const

export function parseSchedule(value: unknown, expectedId?: string): Schedule {
  const r = obj(value)
  const id = str(r.id, 64)
  if (expectedId !== undefined && id !== expectedId) fail()
  const status = oneOf<ScheduleStatus>(r.status, SCHEDULE_STATUSES)
  const observed = oneOf<ObservedStatus>(r.observed_status, [...SCHEDULE_STATUSES, ...OP_STATUSES, 'AUTH_EXPIRED', 'MISSED_GRACE'])
  const due = iso(r.due_at), grace = iso(r.grace_until)
  if (chinaEpoch(grace) !== chinaEpoch(due) + 86400) fail()
  const auth = str(r.authorization_operation_id, 64)
  const childId = nul(r.child_operation_id, x => str(x, 64))
  const childStatus = nul(r.child_status, x => oneOf<OperationStatus>(x, OP_STATUSES))
  const childMode = nul(r.child_mode, x => oneOf<string>(x, CHILD_MODES))
  const n = [childId, childStatus, childMode].filter(x => x === null).length
  if (n !== 0 && n !== 3) fail()
  if (childId !== null && childId === auth) fail()
  if (observed === 'AUTH_EXPIRED') { if (status !== 'AUTHORIZING') fail() }
  else if (observed === 'MISSED_GRACE') { if (status !== 'ACTIVE') fail() }
  // Authorization outcome observed while schedule is still AUTHORIZING (no child); SUCCEEDED is never accepted here.
  else if (status === 'AUTHORIZING' && childStatus === null && (AUTH_OBSERVED as readonly string[]).includes(observed)) { /* ok */ }
  else if (childStatus !== null) { if (observed !== childStatus) fail() }
  else if (observed !== status) fail()
  if (r.reserved_cents !== 0 || r.simulated !== true || typeof r.replayed !== 'boolean') fail()
  return {
    id, status, observed_status: observed, version: int(r.version, 1), amount_cents: int(r.amount_cents, 1, MAX_CENTS),
    account_id: str(r.account_id, 64), account_name: str(r.account_name), payee_id: str(r.payee_id, 64),
    payee_name: str(r.payee_name), payee_phone: phone(r.payee_phone), due_at: due, grace_until: grace,
    authorization_operation_id: auth, child_operation_id: childId, child_status: childStatus, child_mode: childMode,
    outcome_code: nul(r.outcome_code, code), reserved_cents: 0, created_at: iso(r.created_at), updated_at: iso(r.updated_at),
    cancelled_at: nul(r.cancelled_at, iso), replayed: r.replayed as boolean, simulated: true, notice: str(r.notice, 500)
  }
}

function parsePayload(v: unknown, isAuth: boolean): Record<string, unknown> {
  const p = obj(v)
  const allowed = isAuth ? [...PAY_KEYS, 'schedule_id', 'due_at_iso'] : PAY_KEYS
  const keys = Object.keys(p)
  if (keys.length !== allowed.length || keys.some(k => !allowed.includes(k))) fail()
  if (p.fee_cents !== 0) fail()
  const out: Record<string, unknown> = {
    account_id: str(p.account_id, 64), account_name: str(p.account_name), payee_id: str(p.payee_id, 64),
    payee_name: str(p.payee_name), payee_phone: phone(p.payee_phone), amount_cents: int(p.amount_cents, 1, MAX_CENTS), fee_cents: 0
  }
  if (isAuth) { out.schedule_id = str(p.schedule_id, 64); out.due_at_iso = iso(p.due_at_iso) }
  return out
}

export function parseScheduleOperation(value: unknown, expectedSchedule?: Schedule): ScheduleOperation {
  const r = obj(value)
  const type = oneOf<ScheduleOperation['type']>(r.type, [AUTH_TYPE, 'transfer'])
  const isAuth = type === AUTH_TYPE
  if (!isAuth && !expectedSchedule) fail()
  const id = str(r.id, 64)
  const status = oneOf<OperationStatus>(r.status, OP_STATUSES)
  const risk = oneOf<ScheduleOperation['risk']>(r.risk, ['RED', 'YELLOW'])
  if (isAuth && risk !== 'RED') fail()
  const created = ts(r.created_at), expires = ts(r.expires_at)
  if (expires < created) fail()
  const payload = parsePayload(r.payload, isAuth)
  const s = expectedSchedule
  if (s) {
    if (id !== (isAuth ? s.authorization_operation_id : s.child_operation_id)) fail()
    if (payload.amount_cents !== s.amount_cents || payload.payee_id !== s.payee_id || payload.account_id !== s.account_id) fail()
    if (payload.account_name !== s.account_name || payload.payee_name !== s.payee_name || payload.payee_phone !== s.payee_phone) fail()
    if (isAuth && (payload.schedule_id !== s.id || chinaEpoch(payload.due_at_iso) !== chinaEpoch(s.due_at))) fail()
  }
  let result: Record<string, unknown> | null = null
  if (status === 'SUCCEEDED') {
    const x = obj(r.result)
    if (isAuth) {
      const allowed = ['schedule_id', 'status', 'amount_cents', 'due_at', 'simulated', 'message']
      if (Object.keys(x).some(k => !allowed.includes(k))) fail()
      if (x.status !== 'ACTIVE' || x.simulated !== true || x.schedule_id !== payload.schedule_id || x.amount_cents !== payload.amount_cents) fail()
      if (chinaEpoch(x.due_at) !== chinaEpoch(payload.due_at_iso)) fail()
      result = { schedule_id: x.schedule_id, status: 'ACTIVE', amount_cents: x.amount_cents, due_at: x.due_at, simulated: true, message: str(x.message, 500) }
    } else {
      if (x.amount_cents !== payload.amount_cents) fail()
      result = { transaction_id: str(x.transaction_id, 64), amount_cents: x.amount_cents }
      if (x.message !== undefined) result.message = str(x.message, 500)
    }
  } else if (isAuth && r.result !== null && r.result !== undefined) fail()
  const op: ScheduleOperation = { id, type, status, risk, created_at: created, expires_at: expires, payload, result }
  if ('confirmed_at' in r) op.confirmed_at = nul(r.confirmed_at, ts)
  if ('executed_day' in r) op.executed_day = nul(r.executed_day, x => { const d = str(x, 10); if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) fail(); return d })
  return op
}

// Decimal yuan string to integer cents without float multiplication.
export function parseScheduledAmount(text: string): number | null {
  const m = /^(0|[1-9]\d{0,8})(?:\.(\d{1,2}))?$/.exec(typeof text === 'string' ? text.trim() : '')
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return cents >= 1 && cents <= MAX_CENTS ? cents : null
}

// datetime-local (minutes or seconds) read explicitly as Beijing time; host timezone is never used.
export function parseScheduledTime(text: string): string | null {
  const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$/.exec(typeof text === 'string' ? text : '')
  if (!m) return null
  const out = `${m[1]}${m[2] ?? ':00'}+08:00`
  try { chinaEpoch(out); return out } catch { return null }
}

function requestKey(key: unknown): string {
  if (typeof key !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(key) || /^(task-|select-|sched-)/.test(key)) fail()
  return key as string
}
function parseIntent(v: unknown): ScheduleIntent {
  const r = obj(v)
  return { payee_id: str(r.payee_id, 64), amount_cents: int(r.amount_cents, 1, MAX_CENTS), due_at: iso(r.due_at) }
}
const PREPARE_REJECT_STATUS = new Set([400, 403, 404, 409, 422, 423])
const PREPARE_REJECT_CODES = new Set(['INVALID_INPUT', 'INVALID_AMOUNT', 'INVALID_DUE_AT', 'NOT_FOUND', 'SAFETY_HOLD', 'SENSITIVE_LOCKED', 'REQUEST_SUPERSEDED', 'CSRF_REJECTED', 'ORIGIN_REJECTED'])
function isDefinitePrepareReject(e: unknown): boolean {
  if (!(e instanceof Error)) return false
  const r = e as unknown as { status?: unknown; code?: unknown }
  return typeof r.status === 'number' && PREPARE_REJECT_STATUS.has(r.status) && typeof r.code === 'string' && PREPARE_REJECT_CODES.has(r.code)
}
function uniqueIds(ids: string[]): void { if (new Set(ids).size !== ids.length) fail() }

export function createScheduledClient(api: ScheduledApi) {
  async function call(path: string, body?: unknown): Promise<unknown> {
    try { return body === undefined ? await api(path) : await api(path, body) }
    catch (e) {
      const err = new Error('请求未完成，结果未知，请只读核对后再操作')
      // Only the exact create-prepare POST may be marked as a definite pre-create rejection.
      if (path === '/scheduled-transfers' && body !== undefined && isDefinitePrepareReject(e)) {
        Object.defineProperty(err, 'definiteSchedulePrepareRejection', { value: true })
      }
      throw err
    }
  }
  return {
    async session(): Promise<'demo' | 'guest'> {
      const r = obj(await call('/session'))
      const u = r.user_id ?? r.user
      if (r.user_id !== undefined && r.user !== undefined && r.user_id !== r.user) fail()
      return oneOf<'demo' | 'guest'>(u, ['demo', 'guest'])
    },
    async load(): Promise<ScheduleBundle> {
      const [a, b] = await Promise.all([call('/scheduled-transfers'), call('/aa/participants')])
      const ra = obj(a), rb = obj(b)
      if (ra.simulated !== true || rb.simulated !== true || !Array.isArray(ra.schedules) || !Array.isArray(rb.participants)) fail()
      const schedules = (ra.schedules as unknown[]).map(x => parseSchedule(x))
      const payees = (rb.participants as unknown[]).map(x => { const p = obj(x); return { id: str(p.id, 64), name: str(p.name), masked_phone: phone(p.masked_phone) } })
      uniqueIds(schedules.map(x => x.id)); uniqueIds(payees.map(x => x.id))
      return { schedules, payees }
    },
    async prepare(intent: ScheduleIntent, key: string): Promise<{ schedule: Schedule; operation: ScheduleOperation }> {
      const i = parseIntent(intent), k = requestKey(key)
      const r = obj(await call('/scheduled-transfers', { ...i, request_key: k }))
      const schedule = parseSchedule(r.schedule)
      const operation = parseScheduleOperation(r.operation, schedule)
      if (operation.type !== AUTH_TYPE || schedule.payee_id !== i.payee_id || schedule.amount_cents !== i.amount_cents || chinaEpoch(schedule.due_at) !== chinaEpoch(i.due_at)) fail()
      return { schedule, operation }
    },
    async lookup(key: string): Promise<Schedule> {
      return parseSchedule(await call(`/scheduled-transfers/requests/${encodeURIComponent(requestKey(key))}`))
    },
    async get(id: string): Promise<Schedule> {
      const sid = str(id, 64)
      return parseSchedule(await call(`/scheduled-transfers/${encodeURIComponent(sid)}`), sid)
    },
    async cancel(schedule: Schedule, key: string, confirmed: boolean): Promise<Schedule> {
      if (confirmed !== true) fail()
      const s = parseSchedule(schedule), k = requestKey(key)
      const r = parseSchedule(await call(`/scheduled-transfers/${encodeURIComponent(s.id)}/cancel`, { expected_version: s.version, request_key: k, confirmed: true }), s.id)
      if (r.version < s.version || r.amount_cents !== s.amount_cents || r.payee_id !== s.payee_id || r.due_at !== s.due_at || r.authorization_operation_id !== s.authorization_operation_id) fail()
      return r
    },
    async runDue(): Promise<RunDueResult> {
      const r = obj(await call('/scheduled-transfers/run-due', {}))
      if (r.simulated !== true || !Array.isArray(r.processed)) fail()
      const processed = (r.processed as unknown[]).map(x => {
        const e = obj(x)
        return { schedule_id: str(e.schedule_id, 64), version: int(e.version, 1), status: oneOf<ScheduleStatus>(e.status, SCHEDULE_STATUSES), outcome_code: nul(e.outcome_code, code), child_operation_id: nul(e.child_operation_id, x2 => str(x2, 64)), action: oneOf<string>(e.action, RUN_ACTIONS) }
      })
      uniqueIds(processed.map(x => x.schedule_id))
      return { checked_at: iso(r.checked_at), simulated: true, processed, next_due_at: nul(r.next_due_at, iso), notice: str(r.notice, 500) }
    },
    async readOperation(id: string, schedule: Schedule): Promise<ScheduleOperation> {
      const s = parseSchedule(schedule), oid = str(id, 64)
      if (oid !== s.authorization_operation_id && oid !== s.child_operation_id) fail()
      const r = obj(await call(`/operations/${encodeURIComponent(oid)}/receipt`))
      if (r.read_only !== true || r.simulated !== true) fail()
      const checked = receiptEpoch(r.checked_at)
      const recorded = oneOf<OperationStatus>(r.recorded_status, OP_STATUSES)
      const observed = oneOf<OperationStatus>(r.observed_status, OP_STATUSES)
      const op = parseScheduleOperation(r.operation, s)
      if (op.id !== oid || op.status !== recorded) fail()
      if (observed === recorded) return op
      // UI-only expiry view; recorded DB state untouched. Any other divergence (e.g. SUCCEEDED over pending) rejects.
      if (observed === 'EXPIRED' && EXPIRABLE.includes(recorded) && checked >= op.expires_at) return { ...op, status: 'EXPIRED' }
      return fail()
    }
  }
}
