// Stable frontend entry for the birthday HTTP client. Read-only SDK: no storage, no logs,
// no confirm/verify, no retries, no polling. DTO parsing is delegated to peer modules.
import {
  parseBirthdayFlow,
  parseBirthdayItem,
  parseBirthdayOptions,
  parseBirthdayIntent,
  parseBirthdayCommand,
  parseBirthdayKey,
} from './birthday-data.ts'
import type { BirthdayFlow, BirthdayIntent, BirthdayAction } from './birthday-data.ts'
import { parseBirthdayReceipt } from './birthday-operation.ts'
import type { BirthdayOperation } from './birthday-operation.ts'

export * from './birthday-data.ts'
export * from './birthday-operation.ts'

export type BirthdayApi = (path: string, body?: unknown) => Promise<unknown>
export type BirthdayUser = 'demo' | 'guest'
export type BirthdayLookupAction = 'create' | 'advance' | 'decide_cake' | 'select_item' | 'pause' | 'resume' | 'cancel'
export interface BirthdayLookupResult {
  action: BirthdayLookupAction
  flow: BirthdayFlow
  simulated: true
  merchant_connected: false
}
export interface BirthdayMutationResult { flow: BirthdayFlow; notice: string }
export interface BirthdayLoadResult {
  flows: BirthdayFlow[]
  items: ReturnType<typeof parseBirthdayItem>[]
  accounts: ReturnType<typeof parseBirthdayOptions>['accounts']
  business_date: string
}
export interface BirthdayClient {
  session(): Promise<BirthdayUser>
  load(): Promise<BirthdayLoadResult>
  get(id: string): Promise<BirthdayFlow>
  lookup(key: string): Promise<BirthdayLookupResult>
  create(intent: unknown, key: string): Promise<BirthdayMutationResult>
  command(flow: BirthdayFlow, action: BirthdayAction, args: unknown, key: string): Promise<BirthdayMutationResult>
  readOperation(id: string, flow: BirthdayFlow): Promise<BirthdayOperation>
}

const MSG_NETWORK = '生日安排暂时无法确认结果，请稍后核对。'
const MSG_REJECTED = '生日安排请求未被接受，请检查后重试。'
const MSG_PARSE = '生日安排返回内容无法识别，请稍后核对。'

const LOOKUP_ACTIONS: readonly string[] = ['create', 'advance', 'decide_cake', 'select_item', 'pause', 'resume', 'cancel']
const DEFINITE_STATUS: readonly number[] = [400, 403, 404, 409, 422, 423]
const DEFINITE_CODES: readonly string[] = [
  'DATE_OUT_OF_RANGE', 'NOT_FOUND', 'VERSION_CONFLICT', 'CSRF_REJECTED', 'ORIGIN_REJECTED',
  'SENSITIVE_LOCKED', 'SAFETY_HOLD', 'REQUEST_SUPERSEDED', 'NOT_READY', 'CHOICE_LOCKED',
  'BUDGET_EXCEEDED', 'QUOTE_CHANGED',
]
const NEVER_DEFINITE: readonly string[] = ['KEY_CONFLICT', 'IDEMPOTENCY_CONFLICT', 'REQUEST_PENDING', 'RESULT_PENDING']
const FINANCE_IDX: readonly number[] = [0, 4, 5]

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function fail(message: string, definite = false): Error {
  const e = new Error(message) as Error & { definiteBirthdayRejection?: boolean }
  if (definite) e.definiteBirthdayRejection = true
  return e
}

// Only an authoritative pre-commit/whole-transaction rejection is marked definite.
function isDefinite(err: unknown): boolean {
  if (!(err instanceof Error)) return false
  const status = (err as { status?: unknown }).status
  const raw = (err as { code?: unknown }).code
  if (typeof status !== 'number' || !DEFINITE_STATUS.includes(status)) return false
  if (typeof raw !== 'string' || raw.length === 0) return false
  const code = raw.toUpperCase()
  if (NEVER_DEFINITE.includes(code)) return false
  return DEFINITE_CODES.includes(code) || /^INVALID_[A-Z0-9_]+$/.test(code)
}

// Parse after HTTP success; any failure is a plain unknown-result error, never a rejection.
function parsed<T>(fn: () => T): T {
  try { return fn() } catch { throw fail(MSG_PARSE) }
}

function requireFlags(root: Record<string, unknown>): void {
  if (root.simulated !== true || root.merchant_connected !== false) throw new Error('flags')
}

function parseWrapped(raw: unknown): { flow: BirthdayFlow; notice: string } {
  if (!isRecord(raw)) throw new Error('root')
  requireFlags(raw)
  const notice = typeof raw.notice === 'string' ? raw.notice : ''
  return { flow: parseBirthdayFlow(raw.flow), notice }
}

function uniqueBy<T>(list: T[], key: (v: T) => unknown): void {
  const seen = new Set<unknown>()
  for (const v of list) {
    const k = key(v)
    if (seen.has(k)) throw new Error('duplicate')
    seen.add(k)
  }
}

function sameIntent(flow: BirthdayFlow, ref: BirthdayIntent | BirthdayFlow): boolean {
  return flow.recipient_label === ref.recipient_label && flow.event_date === ref.event_date &&
    flow.account_id === ref.account_id && flow.budget_cents === ref.budget_cents
}

export function createBirthdayClient(api: BirthdayApi): BirthdayClient {
  async function call(path: string, body?: unknown): Promise<unknown> {
    try {
      return body === undefined ? await api(path) : await api(path, body)
    } catch (err) {
      const definite = isDefinite(err)
      throw fail(definite ? MSG_REJECTED : MSG_NETWORK, definite)
    }
  }

  async function session(): Promise<BirthdayUser> {
    const raw = await call('/session')
    return parsed(() => {
      if (!isRecord(raw)) throw new Error('root')
      const a = raw.user_id, b = raw.user
      if (a !== undefined && b !== undefined && a !== b) throw new Error('mismatch')
      const u = a ?? b
      if (u !== 'demo' && u !== 'guest') throw new Error('user')
      return u
    })
  }

  async function load(): Promise<BirthdayLoadResult> {
    const [fr, cr, or] = await Promise.all([
      call('/birthday/flows'), call('/birthday/catalog'), call('/birthday/options'),
    ])
    return parsed(() => {
      if (!isRecord(fr) || !Array.isArray(fr.flows)) throw new Error('flows')
      requireFlags(fr)
      if (!isRecord(cr) || !Array.isArray(cr.items)) throw new Error('catalog')
      const flows = fr.flows.map((f) => parseBirthdayFlow(f))
      const items = cr.items.map((i) => parseBirthdayItem(i))
      const opts = parseBirthdayOptions(or)
      uniqueBy(flows, (f) => f.id)
      uniqueBy(items, (i) => i.item_id)
      uniqueBy(opts.accounts, (a) => a.id)
      return { flows, items, accounts: opts.accounts, business_date: opts.business_date }
    })
  }

  async function get(id: string): Promise<BirthdayFlow> {
    if (typeof id !== 'string' || id.length === 0 || id.length > 64) throw fail(MSG_REJECTED)
    const raw = await call('/birthday/flows/' + encodeURIComponent(id))
    return parsed(() => {
      const { flow } = parseWrapped(raw)
      if (flow.id !== id) throw new Error('id')
      return flow
    })
  }

  async function lookup(key: string): Promise<BirthdayLookupResult> {
    const k = parseBirthdayKey(key)
    const raw = await call('/birthday/requests/' + encodeURIComponent(k))
    return parsed(() => {
      if (!isRecord(raw)) throw new Error('root')
      requireFlags(raw)
      if (typeof raw.action !== 'string' || !LOOKUP_ACTIONS.includes(raw.action)) throw new Error('action')
      return {
        action: raw.action as BirthdayLookupAction,
        flow: parseBirthdayFlow(raw.flow),
        simulated: true as const,
        merchant_connected: false as const,
      }
    })
  }

  async function create(intent: unknown, key: string): Promise<BirthdayMutationResult> {
    // Local validation before HTTP so invalid input never reaches the server.
    const i = parseBirthdayIntent(intent)
    const k = parseBirthdayKey(key)
    const body = {
      recipient_label: i.recipient_label,
      event_date: i.event_date,
      account_id: i.account_id,
      budget_cents: i.budget_cents,
      request_key: k,
    }
    const raw = await call('/birthday/flows', body)
    return parsed(() => {
      const res = parseWrapped(raw)
      // Original-key replay may return current progress; only immutable root must match.
      if (!sameIntent(res.flow, i)) throw new Error('intent')
      return res
    })
  }

  async function command(flow: BirthdayFlow, action: BirthdayAction, args: unknown, key: string): Promise<BirthdayMutationResult> {
    const cmd = parseBirthdayCommand(flow, action, args)
    const k = parseBirthdayKey(key)
    const base = cmd.flow
    const body = { ...(cmd.args as Record<string, unknown>), expected_version: base.version, request_key: k }
    const raw = await call('/birthday/flows/' + encodeURIComponent(base.id) + '/' + cmd.action, body)
    return parsed(() => {
      const res = parseWrapped(raw)
      if (res.flow.id !== base.id || !sameIntent(res.flow, base)) throw new Error('flow')
      if (res.flow.version < base.version) throw new Error('version')
      return res
    })
  }

  async function readOperation(id: string, flow: BirthdayFlow): Promise<BirthdayOperation> {
    const f = parsed(() => parseBirthdayFlow(flow))
    const owned = typeof id === 'string' && id.length > 0 &&
      f.nodes.some((n) => FINANCE_IDX.includes(n.idx) && n.operation_id === id)
    if (!owned) throw fail(MSG_REJECTED)
    const raw = await call('/operations/' + encodeURIComponent(id) + '/receipt')
    return parsed(() => parseBirthdayReceipt(raw, id, f))
  }

  return { session, load, get, lookup, create, command, readOperation }
}
