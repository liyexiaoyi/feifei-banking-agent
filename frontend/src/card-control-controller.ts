import type { CardBundle, CardIntent, CardLookup, CardOperation, CardOperationStatus, CardOperationType, PendingCardRequest } from './card-control-client.ts'
import { parsePendingCardRequest } from './card-control-client.ts'

export type CardMode = '' | 'spend' | 'lock' | 'unlock' | 'single' | 'daily' | 'credential'
export interface CardControlState { bundle: CardBundle | null; receipt: CardLookup | null; pending: PendingCardRequest | null; busy: boolean; needsCheck: boolean; error: string }
export interface CardControlClientLike {
  load(cardId: string): Promise<CardBundle>
  prepare(bundle: CardBundle, intent: CardIntent, requestKey: string): Promise<CardOperation>
  lookup(cardId: string, requestKey: string, type: CardOperationType): Promise<CardLookup>
}
export interface CardStorageLike { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export interface CardControlOptions {
  client: CardControlClientLike; storage: CardStorageLike; user: 'demo' | 'guest'; cardId: string
  runMutation(task: () => Promise<void>): Promise<void>; onChange(state: CardControlState): void; keyFactory?: () => string
}

const MAX = 100000000
const MAX_CONTROL = 5000000
const TERMINAL: readonly CardOperationStatus[] = ['SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED']
const SECRET = /^[\x21-\x7e]{8,64}$/
const AMOUNT = /^(0|[1-9]\d{0,8})(?:\.(\d{1,2}))?$/

export function pendingCardStorageKey(user: 'demo' | 'guest', cardId: string): string {
  return 'card-pending:' + encodeURIComponent(user) + ':' + encodeURIComponent(cardId)
}

export function parseCardAmount(text: string, maxCents: number = MAX): number | null {
  const m = typeof text === 'string' ? AMOUNT.exec(text) : null
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return Number.isSafeInteger(cents) && cents >= 1 && cents <= maxCents ? cents : null
}

function isInt(v: unknown, min: number, max: number): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max
}

function intentType(b: CardBundle, i: CardIntent): CardOperationType | null {
  if (!i || typeof i !== 'object') return null
  const c = b.controls, active = b.card.status === 'ACTIVE'
  if (i.kind === 'spend') {
    if (!active || c.locked || !b.merchants.some(m => m.id === i.merchant_id)) return null
    return isInt(i.amount_cents, 1, Math.min(c.single_limit_cents, c.daily_limit_cents - c.spent_today_cents)) ? 'card_spend' : null
  }
  if (i.kind === 'control') {
    const a = i.action, v = i.value
    if (a === 'lock') return v === true && !c.locked ? 'card_controls_update' : null
    if (a === 'unlock') return v === false && c.locked ? 'card_controls_update' : null
    if (a === 'set_single_limit') return isInt(v, 1, Math.min(MAX_CONTROL, c.daily_limit_cents)) ? 'card_controls_update' : null
    if (a === 'set_daily_limit') return isInt(v, Math.max(1, c.single_limit_cents), MAX_CONTROL) ? 'card_controls_update' : null
    return null
  }
  if (i.kind === 'credential') {
    if (!active || typeof i.new_secret !== 'string' || !SECRET.test(i.new_secret)) return null
    const cur = i.current_secret
    if (b.credential.configured) return typeof cur === 'string' && SECRET.test(cur) && cur !== i.new_secret ? 'card_credential_change' : null
    return cur === undefined ? 'card_credential_change' : null
  }
  return null
}

export function createCardControlController(options: CardControlOptions) {
  const { client, storage, user, cardId, runMutation, onChange } = options
  const keyFactory = options.keyFactory ?? (() => crypto.randomUUID())
  const key = pendingCardStorageKey(user, cardId)
  let state: CardControlState = { bundle: null, receipt: null, pending: null, busy: false, needsCheck: false, error: '' }
  let readGen = 0, disposed = false, markerBroken = false, checked = false, writing = false

  const snapshot = (): CardControlState => ({
    bundle: state.bundle ? structuredClone(state.bundle) : null,
    receipt: state.receipt ? structuredClone(state.receipt) : null,
    pending: state.pending ? { ...state.pending } : null,
    busy: state.busy, needsCheck: state.needsCheck, error: state.error })
  const set = (patch: Partial<CardControlState>): void => {
    if (disposed) return
    state = { ...state, ...patch }
    onChange(snapshot())
  }

  function readMarker(): { ok: boolean; p: PendingCardRequest | null } {
    try {
      const raw = storage.getItem(key)
      if (raw === null) return { ok: true, p: null }
      const p = parsePendingCardRequest(JSON.parse(raw))
      return p.card_id === cardId ? { ok: true, p } : { ok: false, p: null }
    } catch { return { ok: false, p: null } }
  }
  function syncMarker(): PendingCardRequest | null {
    const m = readMarker()
    markerBroken = !m.ok
    if (!m.ok) { set({ pending: null, needsCheck: true, error: '原请求记录无法读取，请先核对原请求，暂不能新建。' }); return null }
    if (m.p) set({ pending: m.p, needsCheck: true })
    return m.p
  }

  async function lookup(gen: number, p: PendingCardRequest): Promise<void> {
    try {
      const r = await client.lookup(p.card_id, p.request_key, p.type)
      if (gen !== readGen || disposed) return
      checked = true
      set({ receipt: r, needsCheck: true, error: '' })
    } catch {
      if (gen === readGen && !disposed) set({ needsCheck: true, error: '暂时无法核对原请求，请稍后再查，勿重复提交。' })
    }
  }

  async function load(): Promise<void> {
    if (disposed || writing) return
    const gen = ++readGen
    checked = false
    set({ busy: true, bundle: null, receipt: null })
    try {
      const p = syncMarker()
      try {
        const b = await client.load(cardId)
        if (gen !== readGen || disposed) return
        set({ bundle: b, error: markerBroken ? state.error : '' })
      } catch {
        if (gen === readGen) set({ bundle: null, receipt: null, error: '读取卡片信息失败，请刷新。' })
        return
      }
      if (p) await lookup(gen, p)
    } finally {
      if (gen === readGen) set({ busy: false })
    }
  }

  async function check(): Promise<void> {
    if (disposed || writing || !state.pending) return
    const gen = ++readGen, p = state.pending
    checked = false
    set({ busy: true, receipt: null })
    try { await lookup(gen, p) } finally { if (gen === readGen) set({ busy: false }) }
  }

  async function prepare(intent: CardIntent): Promise<CardOperation | null> {
    const b = state.bundle
    if (disposed || writing || state.busy || !b || state.needsCheck || markerBroken || state.pending) return null
    const type = intentType(b, intent)
    if (!type) { set({ error: '填写内容不符合要求，请检查后再试。' }); return null }
    writing = true
    set({ busy: true, error: '' })
    let entered = false, result: CardOperation | null = null
    try {
      await runMutation(async () => {
        entered = true
        const m = readMarker()
        if (!m.ok || m.p) { markerBroken = !m.ok; throw new Error('marker') }
        const marker: PendingCardRequest = { card_id: cardId, request_key: keyFactory(), type }
        const raw = JSON.stringify(marker)
        storage.setItem(key, raw)
        set({ pending: marker, needsCheck: true })
        let op: CardOperation
        try { op = await client.prepare(b, intent, marker.request_key) }
        catch (e) {
          if (!disposed && (e as { definiteCardPrepareRejection?: unknown } | null)?.definiteCardPrepareRejection === true) {
            try {
              if (storage.getItem(key) === raw) {
                storage.removeItem(key)
                checked = false
                set({ pending: null, receipt: null, needsCheck: false, error: '银行未接受这次请求，请刷新或修改后重新提交。' })
                return
              }
            } catch { /* keep unknown marker */ }
          }
          set({ error: '请求结果未知，请核对原请求，勿重复提交。' }); return
        }
        checked = false
        result = op
        set({ receipt: { operation: op, recorded_status: op.status, observed_status: op.status, read_only: true, simulated: true } })
      })
    } catch {
      if (!state.pending) set({ needsCheck: markerBroken, error: entered ? '无法保存请求记录，未提交。' : '当前有其他操作进行中，未提交。' })
    } finally {
      writing = false
      set({ busy: false })
    }
    return result
  }

  function startNew(confirmed: boolean): boolean {
    const r = state.receipt
    if (confirmed !== true || disposed || writing || state.busy || markerBroken || !state.pending || !r || !checked) return false
    if (!TERMINAL.includes(r.recorded_status) && r.observed_status !== 'EXPIRED') return false
    try { storage.removeItem(key) } catch { set({ error: '无法清除原请求记录，请稍后再试。' }); return false }
    checked = false
    set({ pending: null, receipt: null, needsCheck: false, error: '' })
    return true
  }

  function dispose(): void {
    disposed = true
    readGen++
    state = { bundle: null, receipt: null, pending: null, busy: false, needsCheck: state.needsCheck, error: '' }
  }

  return { getState: snapshot, load, check, prepare, startNew, dispose }
}
