import { parseBirthdayDate } from './birthday-data.ts'
import type { BirthdayFlow } from './birthday-data.ts'

export type BirthdayOperationType = 'birthday_budget_reserve' | 'birthday_order'
export interface BirthdayOperation {
  id: string
  type: BirthdayOperationType
  status: string
  risk: 'YELLOW' | 'RED'
  created_at: number
  expires_at: number
  confirmed_at?: number | null
  executed_day?: string | null
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
}

const BAD = '操作数据无效，请稍后核对。'
const BAD_SOURCE = '操作与当前生日安排不匹配，请重新核对。'
const BAD_RECEIPT = '操作回执无法核对，请稍后重试。'
const PENDING = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY']
const OP_STATUSES = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY', 'SUCCEEDED', 'CANCELLED', 'EXPIRED',
  'FAILED', 'REJECTED', 'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW']
const BUDGET_KEYS = ['account_id', 'account_name', 'amount_cents', 'event_date', 'risk_epoch', 'simulated']
const ORDER_KEYS = ['budget_id', 'budget_version', 'budget_expires_at', 'account_id', 'account_name', 'gift',
  'item_id', 'item_name', 'quote_version', 'amount_cents', 'event_date', 'risk_epoch', 'simulated', 'merchant_connected']

function fail(msg: string = BAD): never { throw new Error(msg) }
function obj(v: unknown, msg?: string): Record<string, unknown> {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) fail(msg)
  return v as Record<string, unknown>
}
function exactKeys(o: Record<string, unknown>, keys: string[]): void {
  const k = Object.keys(o)
  if (k.length !== keys.length || !keys.every(x => Object.prototype.hasOwnProperty.call(o, x))) fail()
}
function str(v: unknown): string { if (typeof v !== 'string' || v.length === 0 || v.length > 200) fail(); return v }
function epoch(v: unknown): number { if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) fail(); return v }
function posInt(v: unknown): number { if (!Number.isSafeInteger(v) || (v as number) <= 0) fail(); return v as number }
function cents(v: unknown): number { const n = posInt(v); if (n > 100000000) fail(); return n }

function parsePayload(type: BirthdayOperationType, raw: unknown): Record<string, unknown> {
  const p = obj(raw)
  exactKeys(p, type === 'birthday_budget_reserve' ? BUDGET_KEYS : ORDER_KEYS)
  str(p.account_id); str(p.account_name); cents(p.amount_cents)
  if (parseBirthdayDate(p.event_date) === null) fail()
  if (typeof p.risk_epoch !== 'string' || p.simulated !== true) fail()
  if (type === 'birthday_order') {
    str(p.budget_id); posInt(p.budget_version); epoch(p.budget_expires_at)
    if (p.gift !== 'flowers' && p.gift !== 'cake') fail()
    str(p.item_id); str(p.item_name); posInt(p.quote_version)
    if (p.merchant_connected !== false) fail()
  }
  return { ...p }
}

function parseSuccessResult(type: BirthdayOperationType, raw: unknown, payload: Record<string, unknown>): Record<string, unknown> {
  const r = obj(raw)
  if (type === 'birthday_budget_reserve') {
    exactKeys(r, ['budget_id', 'simulated', 'message'])
    str(r.budget_id); if (r.simulated !== true || typeof r.message !== 'string') fail()
  } else {
    exactKeys(r, ['order_id', 'transaction_id', 'amount_cents', 'balance_cents', 'simulated', 'merchant_connected', 'message'])
    str(r.order_id); str(r.transaction_id)
    if (cents(r.amount_cents) !== payload.amount_cents) fail()
    if (!Number.isSafeInteger(r.balance_cents) || (r.balance_cents as number) < 0) fail()
    if (r.simulated !== true || r.merchant_connected !== false || typeof r.message !== 'string') fail()
  }
  return { ...r }
}

function bindToFlow(op: BirthdayOperation, flow: BirthdayFlow): void {
  const f = obj(flow, BAD_SOURCE)
  const nodes = Array.isArray(f.nodes) ? (f.nodes as unknown[]).map(n => obj(n, BAD_SOURCE)) : fail(BAD_SOURCE)
  const linked = nodes.filter(n => n.operation_id === op.id)
  if (linked.length !== 1) fail(BAD_SOURCE)
  const node = linked[0]
  const p = op.payload
  if (p.account_id !== f.account_id || p.event_date !== f.event_date) fail(BAD_SOURCE)
  if (node.idx === 0) {
    if (op.type !== 'birthday_budget_reserve' || p.amount_cents !== f.budget_cents) fail(BAD_SOURCE)
    return
  }
  const gift = node.idx === 4 ? 'flowers' : node.idx === 5 ? 'cake' : fail(BAD_SOURCE)
  if (op.type !== 'birthday_order' || node.gift !== gift || p.gift !== gift) fail(BAD_SOURCE)
  const catalog = nodes.find(n => n.idx === (gift === 'flowers' ? 2 : 3))
  if (!catalog || typeof catalog.item_name !== 'string' || catalog.item_name !== p.item_name
    || catalog.price_cents !== p.amount_cents) fail(BAD_SOURCE)
}

export function parseBirthdayOperation(raw: unknown, flow?: BirthdayFlow): BirthdayOperation {
  const o = obj(raw)
  const type = o.type === 'birthday_budget_reserve' || o.type === 'birthday_order' ? o.type : fail()
  const status = typeof o.status === 'string' && OP_STATUSES.includes(o.status) ? o.status : fail()
  const risk = o.risk === 'YELLOW' || o.risk === 'RED' ? o.risk : fail()
  const id = str(o.id)
  const payload = parsePayload(type, o.payload)
  let result: Record<string, unknown> | null = null
  const hasResult = o.result !== null && o.result !== undefined
  if (status === 'SUCCEEDED' || (status === 'UNKNOWN' && hasResult)) result = parseSuccessResult(type, o.result, payload)
  else if (hasResult) {
    const r = obj(o.result)
    if ('simulated' in r && r.simulated !== true) fail()
    result = { ...r }
  }
  const op: BirthdayOperation = {
    id, type, status, risk,
    created_at: epoch(o.created_at), expires_at: epoch(o.expires_at), payload, result,
  }
  if ('confirmed_at' in o) op.confirmed_at = o.confirmed_at === null ? null : epoch(o.confirmed_at)
  if ('executed_day' in o) {
    if (o.executed_day !== null && typeof o.executed_day !== 'string') fail()
    op.executed_day = o.executed_day as string | null
  }
  if (flow !== undefined) bindToFlow(op, flow)
  return op
}

function parseCheckedAt(v: unknown): number {
  const m = typeof v === 'string'
    ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\+08:00$/.exec(v) : null
  if (!m) fail(BAD_RECEIPT)
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number)
  const ms = Date.UTC(y, mo - 1, d, h, mi, s)
  const t = new Date(ms)
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d
    || t.getUTCHours() !== h || t.getUTCMinutes() !== mi || t.getUTCSeconds() !== s) fail(BAD_RECEIPT)
  const frac = m[7] ? Number(m[7]) / Math.pow(10, m[7].length) : 0
  return ms / 1000 - 8 * 3600 + frac
}

export function parseBirthdayReceipt(raw: unknown, id: string, flow: BirthdayFlow): BirthdayOperation {
  const r = obj(raw, BAD_RECEIPT)
  if (r.read_only !== true || r.simulated !== true) fail(BAD_RECEIPT)
  const op = parseBirthdayOperation(r.operation, flow)
  if (op.id !== id) fail(BAD_SOURCE)
  if (r.recorded_status !== op.status) fail(BAD_RECEIPT)
  const checked = parseCheckedAt(r.checked_at)
  const observed = r.observed_status
  const expiredOk = observed === 'EXPIRED' && PENDING.includes(op.status) && checked >= op.expires_at
  if (observed !== op.status && !expiredOk) fail(BAD_RECEIPT)
  const node = (flow.nodes as unknown as Record<string, unknown>[]).find(n => n.operation_id === op.id)
  const status = op.status === 'UNKNOWN' || (node && node.observed_state === 'UNKNOWN') ? 'UNKNOWN' : observed as string
  return { ...op, status }
}
