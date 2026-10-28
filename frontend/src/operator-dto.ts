// Pure DTO parsers mirroring backend/ff_agent/operator_cases.py projection.
// No runtime imports, side effects, or storage. Throws on any mismatch.

export type CaseStatus = 'QUEUED' | 'IN_REVIEW' | 'REPLIED' | 'CANCELLED'
export type BankStatus = 'WAITING_CONFIRMATION' | 'WAITING_VERIFICATION' | 'READY' | 'EXECUTING' | 'MANUAL_REVIEW' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'EXPIRED' | 'UNKNOWN'

export interface FactOperation {
  id: string
  type: string
  status: BankStatus
  amount_cents?: number
  payee_name?: string
  product_name?: string
  payee_phone?: string
  masked_number?: string
}

export interface CaseFacts {
  plan_id: string
  plan_version: number
  observed_at: number
  operations: FactOperation[]
}

export interface OperatorCase {
  id: string
  plan_id: string
  handoff_id: string
  status: CaseStatus
  version: number
  simulated: true
  created_at: number
  updated_at: number
  reply: string
  replayed: boolean
  snapshot: CaseFacts | null
  current_facts: CaseFacts | null
}

const CASE_STATUSES: readonly string[] = ['QUEUED', 'IN_REVIEW', 'REPLIED', 'CANCELLED']
const BANK_STATUSES: readonly string[] = ['WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'READY', 'EXECUTING', 'MANUAL_REVIEW', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'EXPIRED', 'UNKNOWN']
const SNAPSHOT_OP_KEYS: readonly string[] = ['id', 'type', 'status', 'amount_cents', 'payee_name', 'product_name', 'payee_phone', 'masked_number']
const CURRENT_OP_KEYS: readonly string[] = ['id', 'type', 'status']
const MAX_ID = 128
const MAX_NAME = 64
const MAX_REPLY = 500
const MAX_AMOUNT = 100_000_000
const PHONE = /^(?:\*{4}|\d{3}\*{4}\d{4})$/
const MASKED_NUMBER = /^\*{4}(?:\d{4})?$/

function fail(field: string): never {
  throw new TypeError(`invalid operator DTO field: ${field}`)
}

function record(raw: unknown, field: string): Record<string, unknown> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) fail(field)
  return raw as Record<string, unknown>
}

function id(raw: unknown, field: string): string {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > MAX_ID) fail(field)
  return raw
}

function count(raw: unknown, field: string, min: number): number {
  if (typeof raw !== 'number' || !Number.isSafeInteger(raw) || raw < min) fail(field)
  return raw
}

function timestamp(raw: unknown, field: string): number {
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0) fail(field)
  return raw
}

function bankStatus(raw: unknown, field: string): BankStatus {
  if (typeof raw !== 'string' || !BANK_STATUSES.includes(raw)) fail(field)
  return raw as BankStatus
}

function onlyKeys(obj: Record<string, unknown>, allowed: readonly string[], field: string): void {
  for (const key of Object.keys(obj)) if (!allowed.includes(key)) fail(`${field}.${key}`)
}

function operation(raw: unknown, field: string, minimal: boolean): FactOperation {
  const obj = record(raw, field)
  onlyKeys(obj, minimal ? CURRENT_OP_KEYS : SNAPSHOT_OP_KEYS, field)
  const out: FactOperation = {
    id: id(obj.id, `${field}.id`),
    type: id(obj.type, `${field}.type`),
    status: bankStatus(obj.status, `${field}.status`),
  }
  if (minimal) return out
  if (obj.amount_cents !== undefined) {
    const amount = count(obj.amount_cents, `${field}.amount_cents`, 0)
    if (amount > MAX_AMOUNT) fail(`${field}.amount_cents`)
    out.amount_cents = amount
  }
  for (const key of ['payee_name', 'product_name'] as const) {
    const value = obj[key]
    if (value === undefined) continue
    if (typeof value !== 'string' || value.length > MAX_NAME) fail(`${field}.${key}`)
    out[key] = value
  }
  if (obj.payee_phone !== undefined) {
    if (typeof obj.payee_phone !== 'string' || !PHONE.test(obj.payee_phone)) fail(`${field}.payee_phone`)
    out.payee_phone = obj.payee_phone
  }
  if (obj.masked_number !== undefined) {
    if (typeof obj.masked_number !== 'string' || !MASKED_NUMBER.test(obj.masked_number)) fail(`${field}.masked_number`)
    out.masked_number = obj.masked_number
  }
  return out
}

function facts(raw: unknown, field: string, minimal: boolean): CaseFacts {
  const obj = record(raw, field)
  if (!Array.isArray(obj.operations)) fail(`${field}.operations`)
  const operations = obj.operations.map((op, i) => operation(op, `${field}.operations[${i}]`, minimal))
  const seen = new Set<string>()
  for (const op of operations) {
    if (seen.has(op.id)) fail(`${field}.operations.id`)
    seen.add(op.id)
  }
  return {
    plan_id: id(obj.plan_id, `${field}.plan_id`),
    plan_version: count(obj.plan_version, `${field}.plan_version`, 0),
    observed_at: timestamp(obj.observed_at, `${field}.observed_at`),
    operations,
  }
}

export function parseCasePreview(raw: unknown): CaseFacts {
  return facts(raw, 'preview', false)
}

export function parseOperatorCase(raw: unknown): OperatorCase {
  const obj = record(raw, 'case')
  const planId = id(obj.plan_id, 'case.plan_id')
  if (typeof obj.status !== 'string' || !CASE_STATUSES.includes(obj.status)) fail('case.status')
  const status = obj.status as CaseStatus
  if (obj.simulated !== true) fail('case.simulated')
  if (typeof obj.replayed !== 'boolean') fail('case.replayed')
  if (typeof obj.reply !== 'string' || obj.reply.length > MAX_REPLY) fail('case.reply')
  let snapshot: CaseFacts | null = null
  let current: CaseFacts | null = null
  if (status === 'CANCELLED') {
    if (obj.snapshot !== null || obj.current_facts !== null) fail('case.cancelled_facts')
  } else {
    snapshot = facts(obj.snapshot, 'case.snapshot', false)
    if (snapshot.plan_id !== planId) fail('case.snapshot.plan_id')
    if (obj.current_facts !== null) {
      current = facts(obj.current_facts, 'case.current_facts', true)
      if (current.plan_id !== planId) fail('case.current_facts.plan_id')
      const shared = new Map(snapshot.operations.map(op => [op.id, op.type]))
      for (const op of current.operations) {
        if (shared.get(op.id) !== op.type) fail('case.current_facts.operations')
      }
    }
  }
  return {
    id: id(obj.id, 'case.id'),
    plan_id: planId,
    handoff_id: id(obj.handoff_id, 'case.handoff_id'),
    status,
    version: count(obj.version, 'case.version', 1),
    simulated: true,
    created_at: timestamp(obj.created_at, 'case.created_at'),
    updated_at: timestamp(obj.updated_at, 'case.updated_at'),
    reply: obj.reply,
    replayed: obj.replayed,
    snapshot,
    current_facts: current,
  }
}
