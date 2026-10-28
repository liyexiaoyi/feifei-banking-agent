// Birthday DTO types, strict parsers and pure helpers. No HTTP, no DOM, no api imports.

export type BirthdayGift = 'flowers' | 'cake'
export type BirthdayNodeType = 'birthday_budget' | 'birthday_choice' | 'birthday_catalog' | 'birthday_order' | 'birthday_summary'
export type BirthdayNodeState = 'PENDING' | 'RUNNING' | 'WAITING_CONFIRMATION' | 'WAITING_VERIFICATION' | 'SUCCEEDED' | 'FAILED' | 'SKIPPED' | 'UNKNOWN' | 'CANCELLED'
export type BirthdayFlowStatus = 'ACTIVE' | 'PAUSED' | 'CANCELLED' | 'COMPLETED' | 'BLOCKED' | 'UNKNOWN'

export interface BirthdayNode {
  idx: number
  type: BirthdayNodeType
  gift?: BirthdayGift
  state: BirthdayNodeState
  observed_state: BirthdayNodeState
  version: number
  operation_id: string | null
  item_name?: string
  price_cents?: number
}
export interface BirthdayFlow {
  id: string
  recipient_label: string
  event_date: string
  account_id: string
  budget_cents: number
  cake_choice: boolean | null
  status: BirthdayFlowStatus
  version: number
  simulated: true
  merchant_connected: false
  nodes: BirthdayNode[]
}
export interface BirthdayItem {
  item_id: string; name: string; gift: BirthdayGift; price_cents: number
  quote_version: number; simulated: true; merchant_connected: false
}
export interface BirthdayAccount { id: string; name: string; masked_number: string }
export type BirthdayAccountOption = BirthdayAccount
export interface BirthdayBundle { flows: BirthdayFlow[]; items: BirthdayItem[]; accounts: BirthdayAccount[]; business_date: string }
export interface BirthdayIntent { recipient_label: string; event_date: string; account_id: string; budget_cents: number }
export type BirthdayAction = 'cake' | 'items' | 'advance' | 'pause' | 'resume' | 'cancel'
export type BirthdayLookupAction = 'create' | 'advance' | 'decide_cake' | 'select_item' | 'pause' | 'resume' | 'cancel'
export interface BirthdayLookup { action: BirthdayLookupAction; flow: BirthdayFlow; simulated: true; merchant_connected: false }
export type BirthdayArgs = { cake: boolean } | { gift: BirthdayGift; item_id: string } | Record<string, never> | { confirmed: true }
export type BirthdayPending =
  | { kind: 'create'; request_key: string }
  | { kind: 'command'; action: BirthdayAction; flow_id: string; request_key: string }

const FLOW_ERR = '生日安排数据无效'
const INPUT_ERR = '生日安排信息无效'
const MAX_CENTS = 100000000
const NODE_TYPES: BirthdayNodeType[] = ['birthday_budget', 'birthday_choice', 'birthday_catalog', 'birthday_catalog', 'birthday_order', 'birthday_order', 'birthday_summary']
const NODE_GIFTS: (BirthdayGift | null)[] = [null, null, 'flowers', 'cake', 'flowers', 'cake', null]
const FINANCIAL = [0, 4, 5]
const CATALOG = [2, 3]
const NODE_STATES: string[] = ['PENDING', 'RUNNING', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'UNKNOWN', 'CANCELLED']
const FLOW_STATUSES: string[] = ['ACTIVE', 'PAUSED', 'CANCELLED', 'COMPLETED', 'BLOCKED', 'UNKNOWN']
const ACTIONS: string[] = ['cake', 'items', 'advance', 'pause', 'resume', 'cancel']
const FLOW_ID = /^[0-9a-f]{32}$/
const OP_ID = /^op_[0-9a-f]{32}$/
const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/

function fail(msg: string): never { throw new Error(msg) }
function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v) && Object.getPrototypeOf(v) === Object.prototype
}
// Exact key set: required keys must exist, optional keys may exist, nothing else.
function keys(v: unknown, req: string[], opt: string[], msg: string): Record<string, unknown> {
  if (!isObj(v)) fail(msg)
  for (const k of req) if (!Object.prototype.hasOwnProperty.call(v, k)) fail(msg)
  for (const k of Object.keys(v)) if (!req.includes(k) && !opt.includes(k)) fail(msg)
  return v
}
function int(v: unknown, min: number, max: number, msg: string): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) fail(msg)
  return v
}
function str(v: unknown, min: number, max: number, msg: string, re?: RegExp): string {
  if (typeof v !== 'string' || v.length < min || v.length > max || v.trim() !== v) fail(msg)
  if (re && !re.test(v)) fail(msg)
  return v
}
function flag(v: unknown, want: boolean, msg: string): void { if (v !== want) fail(msg) }
function oneOf<T extends string>(v: unknown, set: string[], msg: string): T {
  if (typeof v !== 'string' || !set.includes(v)) fail(msg)
  return v as T
}

export function parseBirthdayDate(s: unknown): string | null {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null
  const y = Number(s.slice(0, 4)), m = Number(s.slice(5, 7)), d = Number(s.slice(8, 10))
  if (y < 1 || m < 1 || m > 12 || d < 1) return null
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
  return d <= days ? s : null
}

export function parseBirthdayAmount(s: unknown): number | null {
  if (typeof s !== 'string') return null
  const m = /^(0|[1-9]\d{0,8})(?:\.(\d{1,2}))?$/.exec(s)
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0'))
  return Number.isSafeInteger(cents) && cents >= 1 && cents <= MAX_CENTS ? cents : null
}

export function parseBirthdayKey(v: unknown): string {
  if (typeof v !== 'string' || !/^[A-Za-z0-9_.:-]{8,64}$/.test(v)) fail(INPUT_ERR)
  return v
}

function parseNode(raw: unknown, i: number): BirthdayNode {
  const isCat = CATALOG.includes(i), gift = NODE_GIFTS[i]
  const req = ['idx', 'type', 'state', 'observed_state', 'version', 'operation_id']
  const opt = (gift ? ['gift'] : []).concat(isCat ? ['item_name', 'price_cents'] : [])
  const n = keys(raw, gift ? req.concat('gift') : req, opt, FLOW_ERR)
  if (n.idx !== i || n.type !== NODE_TYPES[i]) fail(FLOW_ERR)
  if (gift && n.gift !== gift) fail(FLOW_ERR)
  const op = n.operation_id
  if (op !== null && (!FINANCIAL.includes(i) || typeof op !== 'string' || !OP_ID.test(op))) fail(FLOW_ERR)
  const out: BirthdayNode = {
    idx: i, type: NODE_TYPES[i],
    state: oneOf<BirthdayNodeState>(n.state, NODE_STATES, FLOW_ERR),
    observed_state: oneOf<BirthdayNodeState>(n.observed_state, NODE_STATES, FLOW_ERR),
    version: int(n.version, 1, Number.MAX_SAFE_INTEGER, FLOW_ERR),
    operation_id: op as string | null,
  }
  if (gift) out.gift = gift
  if ('item_name' in n) out.item_name = str(n.item_name, 1, 64, FLOW_ERR)
  if ('price_cents' in n) out.price_cents = int(n.price_cents, 1, MAX_CENTS, FLOW_ERR)
  return out
}

export function parseBirthdayFlow(raw: unknown): BirthdayFlow {
  const f = keys(raw, ['id', 'recipient_label', 'event_date', 'account_id', 'budget_cents', 'cake_choice', 'status', 'version', 'simulated', 'merchant_connected', 'nodes'], [], FLOW_ERR)
  flag(f.simulated, true, FLOW_ERR); flag(f.merchant_connected, false, FLOW_ERR)
  if (f.cake_choice !== null && typeof f.cake_choice !== 'boolean') fail(FLOW_ERR)
  const date = parseBirthdayDate(f.event_date)
  if (!date) fail(FLOW_ERR)
  if (!Array.isArray(f.nodes) || f.nodes.length !== 7) fail(FLOW_ERR)
  const nodes = f.nodes.map((n, i) => parseNode(n, i))
  const ops = nodes.map(n => n.operation_id).filter((o): o is string => o !== null)
  if (new Set(ops).size !== ops.length) fail(FLOW_ERR)
  return {
    id: str(f.id, 32, 32, FLOW_ERR, FLOW_ID),
    recipient_label: str(f.recipient_label, 1, 40, FLOW_ERR),
    event_date: date,
    account_id: str(f.account_id, 1, 64, FLOW_ERR, SAFE_ID),
    budget_cents: int(f.budget_cents, 1, MAX_CENTS, FLOW_ERR),
    cake_choice: f.cake_choice as boolean | null,
    status: oneOf<BirthdayFlowStatus>(f.status, FLOW_STATUSES, FLOW_ERR),
    version: int(f.version, 1, Number.MAX_SAFE_INTEGER, FLOW_ERR),
    simulated: true, merchant_connected: false, nodes,
  }
}

// UNKNOWN dominates: a raw SUCCEEDED/COMPLETED never clears an unknown.
export function birthdayNeedsCheck(flow: BirthdayFlow): boolean {
  return flow.status === 'UNKNOWN' || flow.nodes.some(n => n.observed_state === 'UNKNOWN' || n.state === 'UNKNOWN')
}

export function parseBirthdayItem(raw: unknown): BirthdayItem {
  const it = keys(raw, ['item_id', 'name', 'gift', 'price_cents', 'quote_version', 'simulated', 'merchant_connected'], [], FLOW_ERR)
  flag(it.simulated, true, FLOW_ERR); flag(it.merchant_connected, false, FLOW_ERR)
  return {
    item_id: str(it.item_id, 1, 64, FLOW_ERR, SAFE_ID),
    name: str(it.name, 1, 64, FLOW_ERR),
    gift: oneOf<BirthdayGift>(it.gift, ['flowers', 'cake'], FLOW_ERR),
    price_cents: int(it.price_cents, 1, MAX_CENTS, FLOW_ERR),
    quote_version: int(it.quote_version, 1, Number.MAX_SAFE_INTEGER, FLOW_ERR),
    simulated: true, merchant_connected: false,
  }
}

export function parseBirthdayOptions(raw: unknown): { accounts: BirthdayAccount[]; business_date: string } {
  const o = keys(raw, ['accounts', 'business_date', 'simulated', 'merchant_connected'], [], FLOW_ERR)
  flag(o.simulated, true, FLOW_ERR); flag(o.merchant_connected, false, FLOW_ERR)
  const date = parseBirthdayDate(o.business_date)
  if (!date || !Array.isArray(o.accounts) || o.accounts.length > 50) fail(FLOW_ERR)
  const accounts = o.accounts.map(a => {
    const r = keys(a, ['id', 'name', 'masked_number'], [], FLOW_ERR)
    return { id: str(r.id, 1, 64, FLOW_ERR, SAFE_ID), name: str(r.name, 1, 64, FLOW_ERR), masked_number: str(r.masked_number, 1, 32, FLOW_ERR) }
  })
  if (new Set(accounts.map(a => a.id)).size !== accounts.length) fail(FLOW_ERR)
  return { accounts, business_date: date }
}

export function parseBirthdayIntent(raw: unknown): BirthdayIntent {
  const i = keys(raw, ['recipient_label', 'event_date', 'account_id', 'budget_cents'], [], INPUT_ERR)
  const date = parseBirthdayDate(i.event_date)
  if (!date) fail(INPUT_ERR)
  return {
    recipient_label: str(i.recipient_label, 1, 40, INPUT_ERR),
    event_date: date,
    account_id: str(i.account_id, 1, 64, INPUT_ERR, SAFE_ID),
    budget_cents: int(i.budget_cents, 1, MAX_CENTS, INPUT_ERR),
  }
}

export function parseBirthdayCommand(flow: unknown, action: unknown, args: unknown): { flow: BirthdayFlow; action: BirthdayAction; args: BirthdayArgs } {
  const f = parseBirthdayFlow(flow)
  const a = oneOf<BirthdayAction>(action, ACTIONS, INPUT_ERR)
  let out: BirthdayArgs
  if (a === 'cake') {
    const r = keys(args, ['cake'], [], INPUT_ERR)
    if (typeof r.cake !== 'boolean') fail(INPUT_ERR)
    out = { cake: r.cake }
  } else if (a === 'items') {
    const r = keys(args, ['gift', 'item_id'], [], INPUT_ERR)
    out = { gift: oneOf<BirthdayGift>(r.gift, ['flowers', 'cake'], INPUT_ERR), item_id: str(r.item_id, 1, 64, INPUT_ERR, SAFE_ID) }
  } else if (a === 'resume' || a === 'cancel') {
    const r = keys(args, ['confirmed'], [], INPUT_ERR)
    if (r.confirmed !== true) fail(INPUT_ERR)
    out = { confirmed: true }
  } else {
    keys(args, [], [], INPUT_ERR)
    out = {}
  }
  return { flow: f, action: a, args: out }
}

export function parseBirthdayPending(raw: unknown): BirthdayPending {
  if (!isObj(raw)) fail(INPUT_ERR)
  if (raw.kind === 'create') {
    const p = keys(raw, ['kind', 'request_key'], [], INPUT_ERR)
    return { kind: 'create', request_key: parseBirthdayKey(p.request_key) }
  }
  const p = keys(raw, ['kind', 'action', 'flow_id', 'request_key'], [], INPUT_ERR)
  if (p.kind !== 'command') fail(INPUT_ERR)
  return {
    kind: 'command',
    action: oneOf<BirthdayAction>(p.action, ACTIONS, INPUT_ERR),
    flow_id: str(p.flow_id, 32, 32, INPUT_ERR, FLOW_ID),
    request_key: parseBirthdayKey(p.request_key),
  }
}
