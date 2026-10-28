// Typed client for simulated card controls and credential changes.
// No confirm/verify, no retry, no storage, no logging. Errors are generic.

export type CardStatus = 'ACTIVE' | 'LOST'
export type CardOperationType = 'card_spend' | 'card_controls_update' | 'card_credential_change'
export type CardOperationStatus = 'WAITING_CONFIRMATION' | 'WAITING_VERIFICATION' | 'READY' | 'SUCCEEDED' | 'CANCELLED' | 'EXPIRED' | 'FAILED' | 'UNKNOWN' | 'EXECUTING' | 'MANUAL_REVIEW'
export type ControlAction = 'lock' | 'unlock' | 'set_single_limit' | 'set_daily_limit'

export interface CardInfo { id: string; account_id: string; name: string; masked_number: string; status: CardStatus; version: number; simulated: true }
export interface CardControls { card_id: string; locked: boolean; single_limit_cents: number; daily_limit_cents: number; control_version: number; spent_today_cents: number; simulated: true }
export interface CardCredential { card_id: string; configured: boolean; credential_version: number; simulated: true; bank_connected: false }
export interface CardMerchant { id: string; name: string; simulated: true; merchant_connected: false }
export interface CardBundle { card: CardInfo; controls: CardControls; credential: CardCredential; merchants: CardMerchant[] }
export interface ControlState { locked: boolean; single_limit_cents: number; daily_limit_cents: number }
export interface CardOperation { id: string; type: CardOperationType; status: CardOperationStatus; risk: 'YELLOW' | 'RED'; created_at: number; expires_at: number; payload: Record<string, unknown>; result: Record<string, unknown> | null }
export interface CardLookup { operation: CardOperation; recorded_status: CardOperationStatus; observed_status: CardOperationStatus; read_only: true; simulated: true }
export interface PendingCardRequest { card_id: string; request_key: string; type: CardOperationType }
export type CardIntent =
  | { kind: 'spend'; merchant_id: string; amount_cents: number }
  | { kind: 'control'; action: ControlAction; value: boolean | number }
  | { kind: 'credential'; new_secret: string; current_secret?: string }
export type CardApi = (path: string, body?: unknown) => Promise<unknown>

const TYPES: readonly string[] = ['card_spend', 'card_controls_update', 'card_credential_change']
const STATUSES: readonly string[] = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY', 'SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED', 'UNKNOWN', 'EXECUTING', 'MANUAL_REVIEW']
const ACTIONS: readonly string[] = ['lock', 'unlock', 'set_single_limit', 'set_daily_limit']
const MAX_SPEND = 100000000
const MAX_LIMIT = 5000000
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/
const KEY_RE = /^[A-Za-z0-9_-]{8,64}$/
const SECRET_RE = /^[\x21-\x7e]{8,64}$/
const SECRETISH = /secret|salt|verifier|hash|pin|password/i
const OP_KEYS = ['id', 'type', 'status', 'risk', 'created_at', 'expires_at', 'payload', 'result']
const SPEND_KEYS = ['card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind', 'expected_card_version', 'expected_control_version', 'merchant_id', 'merchant_name', 'amount_cents', 'risk_epoch', 'simulated']
const CONTROL_KEYS = ['card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind', 'expected_control_version', 'action', 'before', 'after', 'simulated']
const CREDENTIAL_KEYS = ['card_id', 'account_id', 'card_name', 'masked_number', 'storage_kind', 'expected_card_version', 'expected_credential_version', 'challenge_id', 'risk_epoch', 'simulated']
const STATE_KEYS = ['locked', 'single_limit_cents', 'daily_limit_cents']
const LOOKUP_KEYS = ['operation', 'recorded_status', 'observed_status', 'read_only', 'simulated']
const OP_META_KEYS = ['confirmed_at', 'executed_day']
const CONTROL_RESULT_KEYS = ['card_id', 'locked', 'single_limit_cents', 'daily_limit_cents', 'control_version', 'simulated']
const PENDING_STATUSES: readonly string[] = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY']
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/
function exactWith(o: Record<string, unknown>, keys: readonly string[], optional: readonly string[]): void {
  const own = Object.keys(o)
  if (!keys.every(k => own.includes(k)) || !own.every(k => keys.includes(k) || optional.includes(k))) fail()
}
const PENDING_KEYS = ['card_id', 'request_key', 'type']

function fail(): never { throw new Error('卡片数据校验失败，请刷新后重试。') }
function obj(v: unknown): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) fail()
  const proto = Object.getPrototypeOf(v)
  if (proto !== Object.prototype && proto !== null) fail()
  return v as Record<string, unknown>
}
function exact(o: Record<string, unknown>, keys: readonly string[]): void {
  const own = Object.keys(o)
  if (own.length !== keys.length || !own.every(k => keys.includes(k))) fail()
}
function str(v: unknown): string { if (typeof v !== 'string' || v.length === 0 || v.length > 256) fail(); return v }
function anyStr(v: unknown): string { if (typeof v !== 'string' || v.length > 256) fail(); return v }
function id(v: unknown): string { const s = str(v); if (!ID_RE.test(s)) fail(); return s }
function bool(v: unknown): boolean { if (v !== true && v !== false) fail(); return v }
function int(v: unknown, min: number, max: number = Number.MAX_SAFE_INTEGER): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) fail()
  return v
}
function time(v: unknown): number { if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) fail(); return v }
function isTrue(v: unknown): void { if (v !== true) fail() }
function isFalse(v: unknown): void { if (v !== false) fail() }
function oneOf<T extends string>(v: unknown, list: readonly string[]): T { if (typeof v !== 'string' || !list.includes(v)) fail(); return v as T }
function validKey(v: unknown): string { if (typeof v !== 'string' || !KEY_RE.test(v)) fail(); return v }
function containsString(v: unknown, needle: string, depth = 0): boolean {
  if (depth > 8) return true
  if (typeof v === 'string') return v.includes(needle)
  if (Array.isArray(v)) return v.some(x => containsString(x, needle, depth + 1))
  if (typeof v === 'object' && v !== null) return Object.entries(v).some(([k, x]) => k.includes(needle) || containsString(x, needle, depth + 1))
  return false
}

function parseCard(v: unknown): CardInfo {
  const o = obj(v)
  isTrue(o.simulated)
  return { id: id(o.id), account_id: id(o.account_id), name: str(o.name), masked_number: str(o.masked_number), status: oneOf<CardStatus>(o.status, ['ACTIVE', 'LOST']), version: int(o.version, 1), simulated: true }
}
function parseControls(v: unknown, cardId: string): CardControls {
  const o = obj(v)
  if (o.card_id !== cardId) fail()
  isTrue(o.simulated)
  const single = int(o.single_limit_cents, 1, MAX_LIMIT), daily = int(o.daily_limit_cents, 1, MAX_LIMIT)
  if (single > daily) fail()
  return { card_id: cardId, locked: bool(o.locked), single_limit_cents: single, daily_limit_cents: daily, control_version: int(o.control_version, 1), spent_today_cents: int(o.spent_today_cents, 0), simulated: true }
}
function parseCredential(v: unknown, cardId: string): CardCredential {
  const o = obj(v)
  if (o.card_id !== cardId) fail()
  isTrue(o.simulated); isFalse(o.bank_connected)
  const configured = bool(o.configured), version = int(o.credential_version, 0)
  if (configured ? version < 1 : version !== 0) fail()
  return { card_id: cardId, configured, credential_version: version, simulated: true, bank_connected: false }
}
function parseMerchants(v: unknown): CardMerchant[] {
  const o = obj(v)
  isTrue(o.simulated)
  if (!Array.isArray(o.merchants) || o.merchants.length > 500) fail()
  const ids = new Set<string>(), names = new Set<string>()
  return o.merchants.map(item => {
    const m = obj(item)
    isTrue(m.simulated); isFalse(m.merchant_connected)
    const mid = id(m.id), name = str(m.name)
    if (ids.has(mid) || names.has(name)) fail()
    ids.add(mid); names.add(name)
    return { id: mid, name, simulated: true, merchant_connected: false }
  })
}

export function parseCardBundle(value: unknown, expectedCardId: string): CardBundle {
  const cardId = id(expectedCardId)
  const v = obj(value)
  const cardsResp = obj(v.cards)
  isTrue(cardsResp.simulated)
  if (!Array.isArray(cardsResp.cards) || cardsResp.cards.length > 500) fail()
  const seen = new Set<string>()
  let card: CardInfo | null = null
  for (const raw of cardsResp.cards) {
    const c = parseCard(raw)
    if (seen.has(c.id)) fail()
    seen.add(c.id)
    if (c.id === cardId) card = c
  }
  if (card === null) fail()
  return { card, controls: parseControls(v.controls, cardId), credential: parseCredential(v.credential, cardId), merchants: parseMerchants(v.merchants) }
}

function parseState(v: unknown): ControlState {
  const o = obj(v)
  exact(o, STATE_KEYS)
  const s = { locked: bool(o.locked), single_limit_cents: int(o.single_limit_cents, 1, MAX_LIMIT), daily_limit_cents: int(o.daily_limit_cents, 1, MAX_LIMIT) }
  if (s.single_limit_cents > s.daily_limit_cents) fail()
  return s
}
function sameState(a: ControlState, b: ControlState): boolean {
  return a.locked === b.locked && a.single_limit_cents === b.single_limit_cents && a.daily_limit_cents === b.daily_limit_cents
}
function coherentControl(action: ControlAction, before: ControlState, after: ControlState): void {
  const expected: ControlState = { ...before }
  if (action === 'lock') { if (after.locked !== true) fail(); expected.locked = true }
  else if (action === 'unlock') { if (after.locked !== false) fail(); expected.locked = false }
  else if (action === 'set_single_limit') expected.single_limit_cents = after.single_limit_cents
  else expected.daily_limit_cents = after.daily_limit_cents
  if (!sameState(expected, after)) fail()
}

function parsePayload(type: CardOperationType, v: unknown, cardId: string): Record<string, unknown> {
  const p = obj(v)
  exact(p, type === 'card_spend' ? SPEND_KEYS : type === 'card_controls_update' ? CONTROL_KEYS : CREDENTIAL_KEYS)
  if (p.card_id !== cardId) fail()
  isTrue(p.simulated)
  const base = { card_id: cardId, account_id: id(p.account_id), card_name: str(p.card_name), masked_number: str(p.masked_number), storage_kind: oneOf<string>(p.storage_kind, ['physical', 'virtual']) }
  if (type === 'card_spend') {
    return { ...base, expected_card_version: int(p.expected_card_version, 1), expected_control_version: int(p.expected_control_version, 1), merchant_id: id(p.merchant_id), merchant_name: str(p.merchant_name), amount_cents: int(p.amount_cents, 1, MAX_SPEND), risk_epoch: anyStr(p.risk_epoch), simulated: true }
  }
  if (type === 'card_controls_update') {
    const action = oneOf<ControlAction>(p.action, ACTIONS), before = parseState(p.before), after = parseState(p.after)
    coherentControl(action, before, after)
    return { ...base, expected_control_version: int(p.expected_control_version, 1), action, before, after, simulated: true }
  }
  return { ...base, expected_card_version: int(p.expected_card_version, 1), expected_credential_version: int(p.expected_credential_version, 0), challenge_id: id(p.challenge_id), risk_epoch: anyStr(p.risk_epoch), simulated: true }
}

function parseResult(type: CardOperationType, v: unknown, payload: Record<string, unknown>): Record<string, unknown> {
  const r = obj(v)
  if (Object.keys(r).some(k => SECRETISH.test(k))) fail()
  isTrue(r.simulated)
  if (type === 'card_spend') {
    if (r.amount_cents !== payload.amount_cents) fail()
    isFalse(r.merchant_connected)
    const balanceKey = 'balance_cents' in r ? 'balance_cents' : 'balance_after_cents' in r ? 'balance_after_cents' : null
    if (balanceKey === null) fail()
    return { transaction_id: id(r.transaction_id), amount_cents: int(r.amount_cents, 1, MAX_SPEND), [balanceKey]: int(r[balanceKey], 0), merchant_connected: false, simulated: true }
  }
  if (type === 'card_controls_update') {
    exact(r, CONTROL_RESULT_KEYS)
    if (r.card_id !== payload.card_id) fail()
    const version = int(r.control_version, 2)
    if (version !== (payload.expected_control_version as number) + 1) fail()
    const after = parseState({ locked: r.locked, single_limit_cents: r.single_limit_cents, daily_limit_cents: r.daily_limit_cents })
    if (!sameState(after, payload.after as ControlState)) fail()
    return { card_id: payload.card_id as string, ...after, control_version: version, simulated: true }
  }
  isTrue(r.configured); isFalse(r.bank_connected)
  const version = int(r.credential_version, 1)
  if (version !== (payload.expected_credential_version as number) + 1) fail()
  return { configured: true, credential_version: version, bank_connected: false, simulated: true }
}

export function parseCardOperation(value: unknown, cardId: string, expectedType: CardOperationType): CardOperation {
  const cid = id(cardId)
  const type = oneOf<CardOperationType>(expectedType, TYPES)
  const o = obj(value)
  exactWith(o, OP_KEYS, OP_META_KEYS)
  if ('confirmed_at' in o && o.confirmed_at !== null) time(o.confirmed_at)
  if ('executed_day' in o && o.executed_day !== null && (typeof o.executed_day !== 'string' || !DAY_RE.test(o.executed_day))) fail()
  if (o.type !== type) fail()
  const status = oneOf<CardOperationStatus>(o.status, STATUSES)
  const risk = oneOf<'YELLOW' | 'RED'>(o.risk, type === 'card_spend' ? ['YELLOW', 'RED'] : ['RED'])
  const created = time(o.created_at), expires = time(o.expires_at)
  if (expires < created) fail()
  const payload = parsePayload(type, o.payload, cid)
  let result: Record<string, unknown> | null = null
  if (status === 'SUCCEEDED') result = parseResult(type, o.result, payload)
  else if (o.result !== null) fail()
  return { id: id(o.id), type, status, risk, created_at: created, expires_at: expires, payload, result }
}

export function parsePendingCardRequest(value: unknown): PendingCardRequest {
  const o = obj(value)
  exact(o, PENDING_KEYS)
  return { card_id: id(o.card_id), request_key: validKey(o.request_key), type: oneOf<CardOperationType>(o.type, TYPES) }
}

function requestFailed(): never { throw new Error('请求未完成，请稍后查询原请求结果，勿重复提交。') }
async function call(api: CardApi, path: string, body?: unknown): Promise<unknown> {
  try { return body === undefined ? await api(path) : await api(path, body) } catch { return requestFailed() }
}
function cardPath(cardId: string): string { return '/cards/' + encodeURIComponent(cardId) }
const REJECT_STATUS: readonly number[] = [400, 403, 409, 423]
const REJECT_CODES: readonly string[] = ['INVALID_VERIFICATION', 'CURRENT_SECRET_REQUIRED', 'INVALID_SECRET', 'SENSITIVE_LOCKED', 'INVALID_VALUE', 'INVALID_AMOUNT', 'INVALID_LIMIT', 'NO_CHANGE', 'INSUFFICIENT_FUNDS', 'SINGLE_LIMIT_EXCEEDED', 'DAILY_LIMIT_EXCEEDED', 'SAFETY_HOLD', 'CARD_LOCKED', 'CARD_CONFLICT', 'CONTROL_CONFLICT', 'CREDENTIAL_CONFLICT']
// PREPARE POST only: classify authoritative pre-create business rejection from the raw api error.
async function prepareCall(api: CardApi, path: string, body: unknown): Promise<unknown> {
  let definite = false
  const wrapped: CardApi = async (p, b) => {
    try { return await api(p, b) } catch (e) {
      const x = e as { code?: unknown; status?: unknown } | null
      definite = !!x && typeof x.code === 'string' && REJECT_CODES.includes(x.code) && typeof x.status === 'number' && REJECT_STATUS.includes(x.status)
      throw e
    }
  }
  try { return await call(wrapped, path, body) } catch (e) {
    if (definite) throw Object.assign(new Error('银行未接受这次请求，请检查后重试。'), { definiteCardPrepareRejection: true })
    throw e
  }
}
function checkBundle(b: CardBundle): void {
  id(b.card.id); int(b.card.version, 1); int(b.controls.control_version, 1); int(b.credential.credential_version, 0)
  if (b.controls.card_id !== b.card.id || b.credential.card_id !== b.card.id) fail()
}

export function createCardControlClient(api: CardApi) {
  if (typeof api !== 'function') fail()
  return {
    async load(cardId: string): Promise<CardBundle> {
      const cid = id(cardId), base = cardPath(cid)
      const [cards, controls, credential, merchants] = await Promise.all([
        call(api, '/cards'), call(api, base + '/controls'), call(api, base + '/credential'), call(api, '/card-spend/merchants')])
      return parseCardBundle({ cards, controls, credential, merchants }, cid)
    },

    async prepare(bundle: CardBundle, intent: CardIntent, requestKey: string): Promise<CardOperation> {
      checkBundle(bundle)
      const key = validKey(requestKey)
      const cid = bundle.card.id, base = cardPath(cid)
      const i = obj(intent)
      if (i.kind === 'spend') {
        exact(i, ['kind', 'merchant_id', 'amount_cents'])
        const merchantId = id(i.merchant_id), amount = int(i.amount_cents, 1, MAX_SPEND)
        if (!bundle.merchants.some(m => m.id === merchantId)) fail()
        const body = { merchant_id: merchantId, amount_cents: amount, expected_card_version: bundle.card.version, expected_control_version: bundle.controls.control_version, request_key: key }
        const op = parseCardOperation(await prepareCall(api, base + '/spend', body), cid, 'card_spend')
        const p = op.payload
        if (p.merchant_id !== merchantId || p.amount_cents !== amount || p.expected_card_version !== body.expected_card_version || p.expected_control_version !== body.expected_control_version) fail()
        return op
      }
      if (i.kind === 'control') {
        exact(i, ['kind', 'action', 'value'])
        const action = oneOf<ControlAction>(i.action, ACTIONS)
        let value: boolean | number
        if (action === 'lock') { isTrue(i.value); value = true }
        else if (action === 'unlock') { isFalse(i.value); value = false }
        else value = int(i.value, 1, MAX_LIMIT)
        const body = { action, value, expected_version: bundle.controls.control_version, request_key: key }
        const op = parseCardOperation(await prepareCall(api, base + '/controls', body), cid, 'card_controls_update')
        const p = op.payload, after = p.after as ControlState
        if (p.action !== action || p.expected_control_version !== body.expected_version) fail()
        if (action === 'lock' || action === 'unlock') { if (after.locked !== value) fail() }
        else if ((action === 'set_single_limit' ? after.single_limit_cents : after.daily_limit_cents) !== value) fail()
        return op
      }
      if (i.kind === 'credential') {
        const hasCurrent = 'current_secret' in i && i.current_secret !== undefined
        exact(i, 'current_secret' in i ? ['kind', 'new_secret', 'current_secret'] : ['kind', 'new_secret'])
        if (typeof i.new_secret !== 'string' || !SECRET_RE.test(i.new_secret)) fail()
        if (bundle.credential.configured !== hasCurrent) fail()
        if (hasCurrent && (typeof i.current_secret !== 'string' || !SECRET_RE.test(i.current_secret))) fail()
        const newSecret = i.new_secret as string
        const body: Record<string, unknown> = { new_secret: newSecret, expected_version: bundle.credential.credential_version, request_key: key }
        if (hasCurrent) body.current_secret = i.current_secret
        const raw = await prepareCall(api, base + '/credential', body)
        if (containsString(raw, newSecret) || (hasCurrent && containsString(raw, i.current_secret as string))) fail()
        const op = parseCardOperation(raw, cid, 'card_credential_change')
        if (op.payload.expected_credential_version !== bundle.credential.credential_version || op.payload.expected_card_version !== bundle.card.version) fail()
        return op
      }
      return fail()
    },

    async lookup(cardId: string, requestKey: string, type: CardOperationType): Promise<CardLookup> {
      const marker = parsePendingCardRequest({ card_id: cardId, request_key: requestKey, type })
      const raw = await call(api, cardPath(marker.card_id) + '/requests/' + encodeURIComponent(marker.request_key))
      const o = obj(raw)
      exactWith(o, LOOKUP_KEYS, ['checked_at'])
      if ('checked_at' in o && (typeof o.checked_at !== 'string' || !ISO_RE.test(o.checked_at) || Number.isNaN(Date.parse(o.checked_at)))) fail()
      isTrue(o.read_only); isTrue(o.simulated)
      const recorded = oneOf<CardOperationStatus>(o.recorded_status, STATUSES)
      const observed = oneOf<CardOperationStatus>(o.observed_status, STATUSES)
      const operation = parseCardOperation(o.operation, marker.card_id, marker.type)
      if (operation.status !== recorded) fail()
      if (observed !== recorded && !(observed === 'EXPIRED' && PENDING_STATUSES.includes(recorded))) fail()
      return { operation, recorded_status: recorded, observed_status: observed, read_only: true, simulated: true }
    },
  }
}
